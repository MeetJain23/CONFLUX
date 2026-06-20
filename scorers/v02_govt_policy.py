"""
Vector 02: Government Policy.

Scores stocks based on discrete government policy events (PLI schemes,
anti-dumping rulings, tariff/duty changes, GST decisions, budget allocations,
RBI rate moves, privatization, sectoral subsidies).

Architecture per ADR-003: two-mode hybrid stock-targeting.
- Mode A: explicit mapping from metadata/v2_policy_mappings.csv (the moat)
- Mode B: inferred from existing stock_input_commodities or sector metadata
- Mode C: unmapped — event ingests but no stock receives signal

Mode A is checked first; Mode B is fallback. If both apply, Mode A wins.
Mode B contributions are multiplied by 0.7 to reflect lower confidence.

Phase 2 calibration items (see TODO.md):
- Magnitudes are initial guesses, tune with forward returns
- 0.7 inferred discount is unvalidated heuristic
- Sub-sector granularity in Mode B might improve precision
- Open question: explicit + inferred combine, or explicit override entirely?
  Currently explicit overrides — revisit if real events suggest otherwise.
"""

import csv
import logging
import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from data.schema import (
    IngestionRun, PolicyEvent, Stock, StockInputCommodity, Commodity, get_session,
)
from scorers.base import VectorScorer, ScoreResult

logger = logging.getLogger(__name__)


# --- Config ---------------------------------------------------------------

SUBTYPES_CSV_PATH = Path("metadata/v2_policy_subtypes.csv")
MAPPINGS_CSV_PATH = Path("metadata/v2_policy_mappings.csv")
DEFAULT_LOOKBACK_DAYS = 180  # widest decay window we use (BUDGET_*)
INGESTION_STALENESS_HOURS = 48

# Mode B uncertainty discount (per ADR-003)
INFERRED_DISCOUNT = 0.7

# Confidence thresholds based on age of most recent contributing event
CONFIDENCE_BUCKETS = [
    (14, 1.0),   # event within 14 days → full confidence
    (30, 0.7),
    (60, 0.4),
    (120, 0.2),
    # > 120 days → 0.0 (handled implicitly)
]

# Top N events to mention in the rationale string
RATIONALIZE_TOP_N = 3


# --- Subtype-to-commodity / sector inference rules ------------------------
#
# Used by Mode B. When an event subtype isn't in the explicit mappings CSV,
# we parse the subtype string to figure out which existing CONFLUX metadata
# can resolve the affected stocks.
#
# Example: TARIFF_INCREASE_STEEL → look up commodity STEEL_HRC in
# stock_input_commodities. Stocks with STEEL_HRC as an input get scored.

# Maps subtype suffix → commodity name in commodities table
# Mode B for TARIFF_/DUTY_ subtypes uses this.
SUBTYPE_TO_COMMODITY = {
    "STEEL": "STEEL_HRC",
    "CRUDE": "CRUDE_BRENT",
    "GOLD": "GOLD",
    "ALUMINIUM": "ALUMINIUM",
    "COPPER": "COPPER",
    "COAL": "COAL_THERMAL",
}

# Maps subtype suffix → sector name in stocks.sector
# Mode B for BUDGET_/PLI_ subtypes uses this.
SUBTYPE_TO_SECTOR = {
    "DEFENCE": "Capital Goods",  # L&T is the only defence-adjacent stock in current universe
    "INFRASTRUCTURE": "Capital Goods",
    "RAILWAYS": "Capital Goods",
    "RENEWABLE_ENERGY": "Capital Goods",
    "AGRICULTURE": "Chemicals",  # UPL most agri-adjacent in universe
    "AUTO_COMPONENTS": "Auto",
    "PHARMA": "Pharma",
    "TEXTILES": "Consumer",  # placeholder; no textile pure-play in universe yet
    "ELECTRONICS": "IT",  # placeholder
    "SOLAR": "Utilities",  # placeholder
    "TELECOM": "Telecom",  # placeholder
    "FOOD_PROCESSING": "FMCG",
    "SEMICONDUCTORS": "IT",  # placeholder
}


# --- Loaders --------------------------------------------------------------

def _load_subtypes(csv_path: Path = SUBTYPES_CSV_PATH) -> dict:
    """
    Read v2_policy_subtypes.csv into a dict keyed by subtype.
    Returns: {subtype: {"issuer": float, "peer": float, "decay_days": int}}
    """
    if not csv_path.exists():
        raise FileNotFoundError(
            f"V2 subtypes CSV not found at {csv_path}. "
            "Did you commit metadata/v2_policy_subtypes.csv?"
        )
    table = {}
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            table[row["subtype"]] = {
                "issuer": float(row["issuer_magnitude"]),
                "peer": float(row["peer_magnitude"]),  # always 0 for V2
                "decay_days": int(row["decay_days"]),
            }
    logger.debug(f"Loaded {len(table)} V2 subtype rules")
    return table


def _load_explicit_mappings(csv_path: Path = MAPPINGS_CSV_PATH) -> dict:
    """
    Read v2_policy_mappings.csv into a dict keyed by subtype.
    Returns: {subtype: {"stocks": [str], "magnitude_override": Optional[float]}}
    
    Missing CSV is NOT a fatal error — Mode A simply produces no hits,
    and Mode B handles everything. Useful when running on a fresh clone
    without curated mappings yet.
    """
    if not csv_path.exists():
        logger.warning(
            f"V2 explicit mappings CSV not found at {csv_path}. "
            "Mode A will produce no hits; only Mode B (inferred) will fire."
        )
        return {}
    
    table = {}
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            stocks = [s.strip() for s in row["affected_stocks"].split(",")]
            override = row.get("magnitude_override", "").strip()
            table[row["subtype"]] = {
                "stocks": stocks,
                "magnitude_override": float(override) if override else None,
            }
    logger.debug(f"Loaded {len(table)} V2 explicit mappings")
    return table


# --- Helper: ingestion freshness check ------------------------------------

def _is_ingestion_stale(session, max_age_hours: int = INGESTION_STALENESS_HOURS) -> bool:
    """
    Stale policy_news ingestion → V2 returns confidence 0 to avoid lying
    about coverage. Same pattern as V12 scorer.
    """
    latest = (
        session.query(IngestionRun)
        .filter_by(job_name="policy_news", status="success")
        .order_by(IngestionRun.finished_at.desc())
        .first()
    )
    if latest is None:
        logger.warning("No successful policy_news ingestion runs found")
        return True
    age = datetime.now() - latest.finished_at
    is_stale = age > timedelta(hours=max_age_hours)
    if is_stale:
        logger.warning(
            f"Policy news ingestion is stale "
            f"(last success: {latest.finished_at}, {age} ago)"
        )
    return is_stale


# --- Decay and confidence helpers (same as V12) ---------------------------

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


# --- Subtype parsing for Mode B inference ---------------------------------

def _parse_subtype_for_mode_b(subtype: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Parse a subtype string into (category, commodity, sector) for Mode B
    inference. Returns (category, None, None) if subtype doesn't match a
    Mode B-eligible pattern.
    
    Categories that Mode B can resolve:
    - "tariff_duty" → look up commodity in stock_input_commodities
    - "budget_pli" → look up sector in stocks table
    """
    # TARIFF_INCREASE_<COMMODITY>, TARIFF_DECREASE_<COMMODITY>,
    # DUTY_INCREASE_<COMMODITY>, DUTY_DECREASE_<COMMODITY>
    tariff_match = re.match(r"^(TARIFF|DUTY)_(INCREASE|DECREASE)_(\w+)$", subtype)
    if tariff_match:
        commodity_suffix = tariff_match.group(3)
        commodity = SUBTYPE_TO_COMMODITY.get(commodity_suffix)
        # direction: increase = positive for domestic producer / negative for consumer
        # Sign is encoded in the magnitude in the subtypes CSV, so Mode B
        # just uses that magnitude as-is.
        return ("tariff_duty", commodity, None)
    
    # BUDGET_<SECTOR>, PLI_<SECTOR>
    budget_pli_match = re.match(r"^(BUDGET|PLI)_(\w+)$", subtype)
    if budget_pli_match:
        sector_suffix = budget_pli_match.group(2)
        sector = SUBTYPE_TO_SECTOR.get(sector_suffix)
        return ("budget_pli", None, sector)
    
    return (None, None, None)


# --- The scorer -----------------------------------------------------------

class GovtPolicyScorer(VectorScorer):
    """
    V2: Government Policy.
    
    For each stock, scans recent policy events. For each event, determines
    if the stock is affected via Mode A (explicit mapping) → Mode B
    (inferred from existing metadata) → Mode C (unmapped, no signal).
    
    Returns ScoreResult with rationale showing top contributing events.
    Returns None for stocks with no contributing events (sparse vector).
    """
    
    vector_id = 2
    vector_name = "Government Policy"
    
    def __init__(self, session=None, lookback_days: int = DEFAULT_LOOKBACK_DAYS):
        super().__init__(session=session)
        self.lookback_days = lookback_days
        self.subtypes = _load_subtypes()
        self.explicit_mappings = _load_explicit_mappings()
        
        # Cache ingestion-staleness check once per scorer instance
        self._ingestion_stale = _is_ingestion_stale(self.session)
    
    def score_one(self, stock: Stock, asof: date) -> Optional[ScoreResult]:
        """
        Compute V2 score for one stock on one date.
        
        Returns None when there's no signal (no events affect this stock).
        Returns ScoreResult with score=0, confidence=0 when ingestion is stale.
        """
        # --- Stale-ingestion short circuit ---
        if self._ingestion_stale:
            return ScoreResult(
                score=0.0,
                confidence=0.0,
                rationale=f"V2 unavailable: policy_news ingestion stale "
                          f"(>{INGESTION_STALENESS_HOURS}h since last success). "
                          "Score withheld to avoid false signal.",
                components={"ingestion_stale": True},
            )
        
        window_start = asof - timedelta(days=self.lookback_days)
        
        # --- Fetch all events in window ---
        events = (
            self.session.query(PolicyEvent)
            .filter(PolicyEvent.event_date >= window_start)
            .filter(PolicyEvent.event_date <= asof)
            .all()
        )
        
        if not events:
            return None
        
        # --- Deduplicate events: collapse multiple news articles reporting
        # the same underlying policy event into one. Key = (event_date, subtype).
        # When multiple articles match the same key, keep the one whose magnitude
        # for THIS stock has the largest absolute value.
        #
        # Without this, a duty hike reported by 17 news outlets would count as
        # 17 separate policy events. V2 would measure media coverage volume
        # rather than policy magnitude.
        deduped_events = self._deduplicate_events(events, stock)
        
        contributions = []
        components = {"direct_events": [], "inferred_events": []}
        
        for ev in deduped_events:
            magnitude, mode = self._compute_magnitude_for_stock(ev, stock)
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
                "mode": mode,
                "raw_magnitude": magnitude,
                "contribution": contribution,
                "headline": ev.headline_text[:120] if ev.headline_text else "",
            }
            contributions.append(entry)
            
            if mode == "A":
                components["direct_events"].append({
                    "subtype": ev.subtype,
                    "age_days": age_days,
                    "contribution": round(contribution, 4),
                })
            else:
                components["inferred_events"].append({
                    "subtype": ev.subtype,
                    "age_days": age_days,
                    "contribution": round(contribution, 4),
                })
        
        if not contributions:
            return None
        
        # --- Sum + tanh squash ---
        raw_sum = sum(c["contribution"] for c in contributions)
        score = math.tanh(raw_sum * 1.5)  # gain=1.5 (less aggressive than V12's 2.0)
        
        # --- Confidence based on most recent contributing event ---
        min_age = min(c["age_days"] for c in contributions)
        confidence = _confidence_from_age_days(min_age)
        
        # If ONLY inferred events contributed, reduce confidence slightly
        # (Mode B's 0.7 discount is in magnitude; this is the confidence equivalent)
        has_explicit = any(c["mode"] == "A" for c in contributions)
        if not has_explicit:
            confidence *= 0.8
        
        # --- Top-3 rationale ---
        sorted_contribs = sorted(
            contributions, key=lambda c: abs(c["contribution"]), reverse=True
        )[:RATIONALIZE_TOP_N]
        
        top_descriptions = []
        for c in sorted_contribs:
            mode_tag = "explicit" if c["mode"] == "A" else "inferred"
            top_descriptions.append(
                f"{c['subtype']} {c['age_days']}d ago "
                f"({c['contribution']:+.3f}, {mode_tag})"
            )
        
        direction_word = (
            "tailwind" if score > 0.15
            else "headwind" if score < -0.15
            else "neutral"
        )
        
        rationale = (
            f"Policy {direction_word}: {len(contributions)} events contributing "
            f"(raw sum {raw_sum:+.3f}). "
            f"Top: {'; '.join(top_descriptions)}."
        )
        
        components["raw_sum"] = round(raw_sum, 4)
        components["n_contributions"] = len(contributions)
        components["min_age_days"] = min_age
        components["has_explicit"] = has_explicit
        
        return ScoreResult(
            score=score,
            confidence=confidence,
            rationale=rationale,
            components=components,
        )
    
    # --- Mode A / B / C cascade ---------------------------------------
    
    def _compute_magnitude_for_stock(
        self, event: PolicyEvent, stock: Stock
    ) -> tuple[Optional[float], Optional[str]]:
        """
        Determine the magnitude this event contributes to this stock.
        
        Returns (magnitude, mode) where:
          mode "A" = explicit mapping match
          mode "B" = inferred from metadata
          (None, None) = Mode C, no signal for this stock
        
        Per ADR-003: Mode A wins if both match.
        """
        subtype = event.subtype
        
        # --- Mode A: explicit mapping ---
        explicit = self.explicit_mappings.get(subtype)
        if explicit and stock.symbol_nse in explicit["stocks"]:
            if explicit["magnitude_override"] is not None:
                return (explicit["magnitude_override"], "A")
            # Fall back to subtype's default magnitude
            subtype_rule = self.subtypes.get(subtype)
            if subtype_rule:
                return (subtype_rule["issuer"], "A")
            return (None, None)
        
        # --- Mode B: inferred ---
        category, commodity_name, sector_name = _parse_subtype_for_mode_b(subtype)
        
        if category == "tariff_duty" and commodity_name:
            # Look up stock's input commodity weight
            commodity = (
                self.session.query(Commodity)
                .filter_by(name=commodity_name)
                .first()
            )
            if commodity:
                link = (
                    self.session.query(StockInputCommodity)
                    .filter_by(stock_id=stock.id, commodity_id=commodity.id)
                    .first()
                )
                if link:
                    subtype_rule = self.subtypes.get(subtype)
                    if subtype_rule:
                        base = subtype_rule["issuer"]
                        # Mode B magnitude = base × weight_pct × 0.7 discount
                        # weight_pct is 0-1 (cogs share)
                        weight = link.cogs_weight_pct or 0
                        return (base * weight * INFERRED_DISCOUNT, "B")
        
        elif category == "budget_pli" and sector_name:
            if stock.sector == sector_name:
                subtype_rule = self.subtypes.get(subtype)
                if subtype_rule:
                    return (subtype_rule["issuer"] * INFERRED_DISCOUNT, "B")
        
        # --- Mode C: no signal for this stock ---
        return (None, None)
    
    def _deduplicate_events(
        self, events: list[PolicyEvent], stock: Stock
    ) -> list[PolicyEvent]:
        """
        Collapse events with the same (event_date, subtype) into a single
        representative event. When the V2 ingester pulls news from Google
        News RSS, the same real-world policy event gets reported by multiple
        outlets — each becomes a separate PolicyEvent row. Without dedup,
        V2 would count media coverage volume instead of policy magnitude.
        
        Dedup rule: for each (date, subtype) group, keep the event whose
        contribution to THIS stock has the largest absolute magnitude.
        This preserves the strongest signal in case any explicit mapping
        override differs between articles (unusual but possible).
        """
        groups = {}  # (event_date, subtype) → (event, abs_magnitude)
        for ev in events:
            key = (ev.event_date, ev.subtype)
            magnitude, _ = self._compute_magnitude_for_stock(ev, stock)
            current_strength = abs(magnitude) if magnitude is not None else 0
            
            if key not in groups or current_strength > groups[key][1]:
                groups[key] = (ev, current_strength)
        
        return [pair[0] for pair in groups.values()]