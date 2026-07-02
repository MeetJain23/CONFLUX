"""
Vector 06: Input Material Supply Side Disruption.

Scores stocks based on discrete supply-side disruption events (monsoon,
Hormuz/Gulf shipping, China steel cuts, China API events, critical minerals
export curbs, semiconductor shortages, OPEC cuts, India natural gas
allocation, global tariff shocks, force majeure).

Architecture: Mode A only (explicit mappings from metadata/v6_supply_mappings.csv).

Rationale for no Mode B: unlike V2 which has clean subtype → commodity /
subtype → sector fallback paths, V6's disruption categories don't map
cleanly to existing metadata (no "imported input dependency" or "agri
exposure" columns). Attempting Mode B would coarsen the vector and lose
its edge. Same pattern as V1 Promoters and V11 Global Parallels — Mode A
only, higher precision, narrower coverage. Vector fires when it fires.

Phase 2 calibration items (see TODO.md):
- source_country, geography_scope, event_severity fields on SupplyEvent
  are persisted but not used in v0. Phase 2 hook: severity as multiplier,
  source_country as per-pair override, geography_scope for coverage tags.
- peer_magnitude column exists in subtypes CSV but is 0.00 across all
  rows in v0. Phase 2 activation when V2 peer signals get calibrated.
- Magnitudes are initial guesses; tune with forward returns.
"""

import csv
import logging
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from data.schema import IngestionRun, Stock, SupplyEvent, get_session
from scorers.base import VectorScorer, ScoreResult

logger = logging.getLogger(__name__)


# --- Config ---------------------------------------------------------------

SUBTYPES_CSV_PATH = Path("metadata/v6_supply_subtypes.csv")
MAPPINGS_CSV_PATH = Path("metadata/v6_supply_mappings.csv")
DEFAULT_LOOKBACK_DAYS = 90  # widest decay we use (MONSOON, CRITICAL_MINERALS)
INGESTION_STALENESS_HOURS = 48

# Confidence thresholds based on age of most recent contributing event
# Matches V2's schedule exactly.
CONFIDENCE_BUCKETS = [
    (14, 1.0),
    (30, 0.7),
    (60, 0.4),
    (90, 0.2),
    # > 90 days → 0.0
]

RATIONALIZE_TOP_N = 3


# --- Loaders --------------------------------------------------------------

def _load_subtypes(csv_path: Path = SUBTYPES_CSV_PATH) -> dict:
    """
    Read v6_supply_subtypes.csv into a dict keyed by subtype.
    Returns: {subtype: {"issuer": float, "peer": float, "decay_days": int}}

    Mirrors V2 loader exactly. peer values are 0.00 across V6 v0; the
    column exists for Phase 2 activation.
    """
    if not csv_path.exists():
        raise FileNotFoundError(
            f"V6 subtypes CSV not found at {csv_path}. "
            "Did you commit metadata/v6_supply_subtypes.csv?"
        )
    table = {}
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            table[row["subtype"]] = {
                "issuer": float(row["issuer_magnitude"]),
                "peer": float(row["peer_magnitude"]),
                "decay_days": int(row["decay_days"]),
            }
    logger.debug(f"Loaded {len(table)} V6 subtype rules")
    return table


def _load_explicit_mappings(csv_path: Path = MAPPINGS_CSV_PATH) -> dict:
    """
    Read v6_supply_mappings.csv into a list of rows keyed by subtype.

    A single subtype can have MULTIPLE rows in the CSV — one row per
    (stock-group, magnitude) tuple. Example: SUPPLY_HORMUZ_GULF_DISRUPTION
    has three rows: fertilizer producers at -0.30, refiners at -0.15,
    ONGC at +0.20. All three need to be considered.

    Returns: {subtype: [{"stocks": [str], "magnitude_override": Optional[float]}, ...]}

    Missing CSV logged as warning; V6 simply produces no signals. Same
    graceful behavior as V2 running on a fresh clone.
    """
    if not csv_path.exists():
        logger.warning(
            f"V6 explicit mappings CSV not found at {csv_path}. "
            "V6 will produce no signals until mappings are added."
        )
        return {}

    table = {}
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            subtype = row["subtype"]
            stocks = [s.strip() for s in row["affected_stocks"].split(",")]
            override = row.get("magnitude_override", "").strip()
            entry = {
                "stocks": stocks,
                "magnitude_override": float(override) if override else None,
            }
            table.setdefault(subtype, []).append(entry)

    total_rows = sum(len(v) for v in table.values())
    logger.debug(f"Loaded {total_rows} V6 mapping rows across {len(table)} subtypes")
    return table


# --- Helper: ingestion freshness check ------------------------------------

def _is_ingestion_stale(session, max_age_hours: int = INGESTION_STALENESS_HOURS) -> bool:
    """
    Stale supply_news ingestion → V6 returns confidence 0 to avoid lying
    about coverage. Same pattern as V2, V12.
    """
    latest = (
        session.query(IngestionRun)
        .filter_by(job_name="supply_news", status="success")
        .order_by(IngestionRun.finished_at.desc())
        .first()
    )
    if latest is None:
        # Also accept "partial" — if partial runs are ingesting data,
        # V6 shouldn't be silenced just because one query in 30 failed.
        latest = (
            session.query(IngestionRun)
            .filter_by(job_name="supply_news", status="partial")
            .order_by(IngestionRun.finished_at.desc())
            .first()
        )
    if latest is None:
        logger.warning("No successful supply_news ingestion runs found")
        return True
    age = datetime.now() - latest.finished_at
    is_stale = age > timedelta(hours=max_age_hours)
    if is_stale:
        logger.warning(
            f"Supply news ingestion is stale "
            f"(last success: {latest.finished_at}, {age} ago)"
        )
    return is_stale


# --- Decay and confidence helpers (same shape as V2) ----------------------

def _confidence_from_age_days(age_days: int) -> float:
    for threshold, conf in CONFIDENCE_BUCKETS:
        if age_days <= threshold:
            return conf
    return 0.0


def _decayed_magnitude(magnitude: float, age_days: int, decay_days: int) -> float:
    """Linear decay: full magnitude at age 0, zero at age >= decay_days."""
    if age_days >= decay_days:
        return 0.0
    if age_days <= 0:
        return magnitude
    return magnitude * (1.0 - age_days / decay_days)


# --- The scorer -----------------------------------------------------------

class SupplyDisruptionScorer(VectorScorer):
    """
    V6: Input Material Supply Side Disruption.

    For each stock, scans recent supply events. For each event, determines
    if the stock is explicitly mapped (Mode A). If not, no signal for that
    (stock, event) pair.

    Returns ScoreResult with rationale showing top contributing events.
    Returns None for stocks with no contributing events (sparse vector,
    like V1 and V11).
    """

    vector_id = 6
    vector_name = "Supply Disruption"

    def __init__(self, session=None, lookback_days: int = DEFAULT_LOOKBACK_DAYS):
        super().__init__(session=session)
        self.lookback_days = lookback_days
        self.subtypes = _load_subtypes()
        self.explicit_mappings = _load_explicit_mappings()

        # Cache ingestion-staleness check once per scorer instance
        self._ingestion_stale = _is_ingestion_stale(self.session)

    def score_one(self, stock: Stock, asof: date) -> Optional[ScoreResult]:
        """
        Compute V6 score for one stock on one date.

        Returns None when there's no signal (no mapped events affect this
        stock). Returns ScoreResult with score=0, confidence=0 when
        ingestion is stale — same contract as V2.
        """
        # --- Stale-ingestion short circuit ---
        if self._ingestion_stale:
            return ScoreResult(
                score=0.0,
                confidence=0.0,
                rationale=f"V6 unavailable: supply_news ingestion stale "
                          f"(>{INGESTION_STALENESS_HOURS}h since last success). "
                          "Score withheld to avoid false signal.",
                components={"ingestion_stale": True},
            )

        window_start = asof - timedelta(days=self.lookback_days)

        # --- Fetch all events in window ---
        events = (
            self.session.query(SupplyEvent)
            .filter(SupplyEvent.event_date >= window_start)
            .filter(SupplyEvent.event_date <= asof)
            .all()
        )

        if not events:
            return None

        # --- Deduplicate events: collapse multiple news articles reporting
        # the same underlying disruption event into one. Key = (event_date,
        # subtype, source_country). source_country is included in the dedup
        # key so a Yemen-driven Red Sea event and an Iran-driven Hormuz event
        # on the same day both classified as HORMUZ_GULF_DISRUPTION stay
        # distinct.
        deduped_events = self._deduplicate_events(events, stock)

        contributions = []
        components = {"events": [], "sources": set()}

        for ev in deduped_events:
            magnitude = self._compute_magnitude_for_stock(ev, stock)
            if magnitude is None or magnitude == 0:
                continue

            subtype_rule = self.subtypes.get(ev.subtype)
            if subtype_rule is None:
                logger.debug(f"No subtype rule for {ev.subtype}; skipping")
                continue

            age_days = (asof - ev.event_date).days
            decay_days = subtype_rule["decay_days"]
            contribution = _decayed_magnitude(magnitude, age_days, decay_days)

            if contribution == 0.0:
                continue

            entry = {
                "subtype": ev.subtype,
                "age_days": age_days,
                "raw_magnitude": magnitude,
                "contribution": contribution,
                "source_country": ev.source_country,
                "geography_scope": ev.geography_scope,
                "headline": ev.headline_text[:120] if ev.headline_text else "",
            }
            contributions.append(entry)

            components["events"].append({
                "subtype": ev.subtype,
                "age_days": age_days,
                "contribution": round(contribution, 4),
                "source_country": ev.source_country,
            })
            if ev.source_country:
                components["sources"].add(ev.source_country)

        if not contributions:
            return None

        # --- Sum + tanh squash. Gain 1.5 matches V2. ---
        raw_sum = sum(c["contribution"] for c in contributions)
        score = math.tanh(raw_sum * 1.5)

        # --- Confidence based on most recent contributing event ---
        min_age = min(c["age_days"] for c in contributions)
        confidence = _confidence_from_age_days(min_age)

        # --- Top-3 rationale ---
        sorted_contribs = sorted(
            contributions, key=lambda c: abs(c["contribution"]), reverse=True
        )[:RATIONALIZE_TOP_N]

        top_descriptions = []
        for c in sorted_contribs:
            src = f" ({c['source_country']})" if c['source_country'] else ""
            top_descriptions.append(
                f"{c['subtype']}{src} {c['age_days']}d ago "
                f"({c['contribution']:+.3f})"
            )

        direction_word = (
            "tailwind" if score > 0.15
            else "headwind" if score < -0.15
            else "neutral"
        )

        rationale = (
            f"Supply {direction_word}: {len(contributions)} events contributing "
            f"(raw sum {raw_sum:+.3f}). "
            f"Top: {'; '.join(top_descriptions)}."
        )

        # Convert set to sorted list for JSON serialization of components
        components["sources"] = sorted(components["sources"])
        components["raw_sum"] = round(raw_sum, 4)
        components["n_contributions"] = len(contributions)
        components["min_age_days"] = min_age

        return ScoreResult(
            score=score,
            confidence=confidence,
            rationale=rationale,
            components=components,
        )

    # --- Mode A cascade -----------------------------------------------

    def _compute_magnitude_for_stock(
        self, event: SupplyEvent, stock: Stock
    ) -> Optional[float]:
        """
        Determine the magnitude this event contributes to this stock.

        V6 is Mode A only: if the stock is not in v6_supply_mappings.csv
        for this subtype, no signal. Returns None for Mode C.

        A single subtype may have multiple mapping rows (e.g., HORMUZ hits
        fertilizer at -0.30, refiners at -0.15, ONGC at +0.20). We check
        all rows for this subtype and return the first one matching this
        stock. Mapping file order = precedence; put more-specific rows
        first if a stock appears in multiple rows.
        """
        subtype = event.subtype
        mapping_rows = self.explicit_mappings.get(subtype, [])

        for row in mapping_rows:
            if stock.symbol_nse in row["stocks"]:
                if row["magnitude_override"] is not None:
                    return row["magnitude_override"]
                # Fall back to subtype default magnitude
                subtype_rule = self.subtypes.get(subtype)
                if subtype_rule:
                    return subtype_rule["issuer"]
                return None

        # Mode C: no mapping row includes this stock
        return None

    def _deduplicate_events(
        self, events: list[SupplyEvent], stock: Stock
    ) -> list[SupplyEvent]:
        """
        Collapse events with the same (event_date, subtype, source_country)
        into a single representative event. When V6 ingester pulls from
        Google News RSS, the same disruption gets reported by many outlets.
        Without dedup, V6 measures media coverage volume, not event magnitude.

        source_country in the key ensures a Yemen-driven Red Sea event and
        an Iran-driven Hormuz event on the same day (both classified as
        HORMUZ_GULF_DISRUPTION) stay distinct.

        Dedup rule: for each (date, subtype, source_country) group, keep the
        event whose contribution to THIS stock has the largest absolute
        magnitude. Preserves the strongest signal.
        """
        groups = {}
        for ev in events:
            key = (ev.event_date, ev.subtype, ev.source_country)
            magnitude = self._compute_magnitude_for_stock(ev, stock)
            current_strength = abs(magnitude) if magnitude is not None else 0

            if key not in groups or current_strength > groups[key][1]:
                groups[key] = (ev, current_strength)

        return [pair[0] for pair in groups.values()]