"""
Vector 12: Re-rating Catalysts.

Scores stocks based on corporate-action events (buybacks, demergers, bonuses,
special dividends, splits, etc.) with linear decay over a 60-day window.

Design:
- Events live in corporate_actions table (populated by ingestion/corporate_actions.py)
- Each event has an action_type that maps to issuer/peer magnitudes via
  metadata/v12_event_magnitudes.csv
- Issuer events: stock gets full issuer_magnitude with linear decay
- Peer events: same-sector stocks get peer_magnitude with linear decay
- All contributions sum, then tanh-squashed to [-1, +1]
- Confidence reflects event recency (recent = high confidence)
- Stale ingestion (>48h) forces confidence=0 to avoid lying about coverage

Phase 1 simplifications:
- Peer scoping is sector-level only (not sub-sector or industry)
- All special/interim dividends get full SPECIAL_DIVIDEND magnitude
  (yield-based tiering deferred to Phase 2)
- No event-type combinations (e.g., demerger + dividend in same week
  scored additively, not interactively)

Phase 2 candidates:
- Yield-based dividend tiering (requires price lookup at event date)
- Per-stock magnitude overrides (some buybacks are bigger deals than others)
- Sub-sector / industry peer scoping (currently sector-level)
- Pre-announcement signal detection (events often have weeks of buildup)
"""

import csv
import logging
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from data.schema import (
    CorporateAction, IngestionRun, Stock, get_session,
)
from scorers.base import VectorScorer, ScoreResult

logger = logging.getLogger(__name__)


# --- Config ---------------------------------------------------------------

MAGNITUDE_CSV_PATH = Path("metadata/v12_event_magnitudes.csv")
DEFAULT_LOOKBACK_DAYS = 60
INGESTION_STALENESS_HOURS = 48

# Confidence thresholds based on age of most recent event
CONFIDENCE_BUCKETS = [
    (14, 1.0),   # event within 14 days → full confidence
    (30, 0.7),   # 14-30 days → 0.7
    (50, 0.4),   # 30-50 days → 0.4
    (60, 0.2),   # 50-60 days → 0.2
    # > 60 days → 0.0 (handled implicitly)
]

# Peer signals are weaker evidence than direct signals.
# A stock with ONLY peer events gets confidence multiplied by this factor.
PEER_ONLY_CONFIDENCE_MULTIPLIER = 0.5

# Top N events to mention in the rationale string
RATIONALE_TOP_N = 3


# --- Magnitude table loader -----------------------------------------------

def _load_magnitudes(csv_path: Path = MAGNITUDE_CSV_PATH) -> dict:
    """
    Read v12_event_magnitudes.csv into a dict keyed by action_type.
    
    Returns: {action_type: {"issuer": float, "peer": float, "decay_days": int}}
    """
    if not csv_path.exists():
        raise FileNotFoundError(
            f"V12 magnitude CSV not found at {csv_path}. "
            "Did you commit metadata/v12_event_magnitudes.csv?"
        )
    
    table = {}
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            table[row["action_type"]] = {
                "issuer": float(row["issuer_magnitude"]),
                "peer": float(row["peer_magnitude"]),
                "decay_days": int(row["decay_days"]),
            }
    
    logger.debug(f"Loaded {len(table)} V12 magnitude rules")
    return table


# --- Helper: ingestion freshness check ------------------------------------

def _is_ingestion_stale(session, max_age_hours: int = INGESTION_STALENESS_HOURS) -> bool:
    """
    Check if the most recent corporate_actions ingestion run is stale.
    Stale ingestion → V12 returns confidence 0 to avoid lying about coverage.
    """
    latest = (
        session.query(IngestionRun)
        .filter_by(job_name="corporate_actions", status="success")
        .order_by(IngestionRun.finished_at.desc())
        .first()
    )
    
    if latest is None:
        logger.warning("No successful corporate_actions ingestion runs found")
        return True
    
    age = datetime.now() - latest.finished_at
    is_stale = age > timedelta(hours=max_age_hours)
    
    if is_stale:
        logger.warning(
            f"Corporate actions ingestion is stale "
            f"(last success: {latest.finished_at}, {age} ago)"
        )
    
    return is_stale


# --- Confidence from recency ----------------------------------------------

def _confidence_from_age_days(age_days: int) -> float:
    """Map age of most-recent event to confidence."""
    for threshold, conf in CONFIDENCE_BUCKETS:
        if age_days <= threshold:
            return conf
    return 0.0


# --- Decay function -------------------------------------------------------

def _decayed_magnitude(magnitude: float, age_days: int, decay_days: int) -> float:
    """
    Linear decay: full magnitude at age 0, zero at age >= decay_days.
    """
    if age_days >= decay_days:
        return 0.0
    if age_days <= 0:
        return magnitude
    return magnitude * (1.0 - age_days / decay_days)


# --- The scorer -----------------------------------------------------------

class RerateCatalystScorer(VectorScorer):
    """
    V12: Re-rating Catalysts.
    
    Sums decayed contributions from issuer + peer corporate events,
    tanh-squashes, returns score with rationale.
    """
    
    vector_id = 12
    vector_name = "Re-rating Catalysts"
    
    def __init__(self, session=None, lookback_days: int = DEFAULT_LOOKBACK_DAYS):
        super().__init__(session=session)
        self.lookback_days = lookback_days
        self.magnitudes = _load_magnitudes()
        
        # Cache the ingestion-staleness check once per scorer instance
        # so we don't query the run log for every stock.
        self._ingestion_stale = _is_ingestion_stale(self.session)
        
        # Cache sector->[stock_id] map for peer lookup
        all_stocks = self.session.query(Stock).filter(Stock.active.is_(True)).all()
        self._sector_to_stock_ids = {}
        for s in all_stocks:
            self._sector_to_stock_ids.setdefault(s.sector, []).append(s.id)
    
    def score_one(self, stock: Stock, asof: date) -> Optional[ScoreResult]:
        """
        Compute V12 score for one stock on one date.
        
        Returns None if there's no signal AND we're not in a stale state
        (i.e., genuinely "no events to score"). When ingestion is stale,
        returns a ScoreResult with confidence=0 and a rationale flagging
        the staleness — so the dashboard shows the gap explicitly.
        """
        # --- Stale-ingestion short circuit ---
        if self._ingestion_stale:
            return ScoreResult(
                score=0.0,
                confidence=0.0,
                rationale="V12 unavailable: corporate-actions ingestion stale "
                          f"(>{INGESTION_STALENESS_HOURS}h since last success). "
                          "Score withheld to avoid false signal.",
                components={"ingestion_stale": True},
            )
        
        window_start = asof - timedelta(days=self.lookback_days)
        
        # --- Direct (issuer) events for this stock ---
        direct_events = (
            self.session.query(CorporateAction)
            .filter(CorporateAction.stock_id == stock.id)
            .filter(CorporateAction.action_date >= window_start)
            .filter(CorporateAction.action_date <= asof)
            .all()
        )
        
        # --- Peer events: same sector, different stock ---
        peer_stock_ids = [
            sid for sid in self._sector_to_stock_ids.get(stock.sector, [])
            if sid != stock.id
        ]
        peer_events = []
        if peer_stock_ids:
            peer_events = (
                self.session.query(CorporateAction)
                .filter(CorporateAction.stock_id.in_(peer_stock_ids))
                .filter(CorporateAction.action_date >= window_start)
                .filter(CorporateAction.action_date <= asof)
                .all()
            )
        
        # --- No events: return None (genuinely no signal) ---
        if not direct_events and not peer_events:
            return None
        
        # --- Accumulate contributions ---
        contributions = []
        components = {"direct_events": [], "peer_events": []}
        
        for ev in direct_events:
            rule = self.magnitudes.get(ev.action_type)
            if rule is None:
                logger.debug(f"No magnitude rule for action_type={ev.action_type}")
                continue
            
            magnitude = ev.magnitude_override if ev.magnitude_override is not None else rule["issuer"]
            age_days = (asof - ev.action_date).days
            contribution = _decayed_magnitude(magnitude, age_days, rule["decay_days"])
            
            if contribution == 0.0:
                continue
            
            contributions.append({
                "kind": "direct",
                "action_type": ev.action_type,
                "age_days": age_days,
                "raw_magnitude": magnitude,
                "contribution": contribution,
                "stock_symbol": stock.symbol_nse,
            })
            components["direct_events"].append({
                "action_type": ev.action_type,
                "age_days": age_days,
                "contribution": round(contribution, 4),
            })
        
        for ev in peer_events:
            rule = self.magnitudes.get(ev.action_type)
            if rule is None:
                continue
            
            # Peer events use the peer column, not issuer
            peer_mag = rule["peer"]
            if peer_mag == 0.0:
                continue  # action type with no peer signal (e.g., SPLIT)
            
            age_days = (asof - ev.action_date).days
            contribution = _decayed_magnitude(peer_mag, age_days, rule["decay_days"])
            
            if contribution == 0.0:
                continue
            
            # Look up the peer stock symbol for the rationale
            peer_stock = self.session.query(Stock).filter_by(id=ev.stock_id).first()
            peer_symbol = peer_stock.symbol_nse if peer_stock else f"id={ev.stock_id}"
            
            contributions.append({
                "kind": "peer",
                "action_type": ev.action_type,
                "age_days": age_days,
                "raw_magnitude": peer_mag,
                "contribution": contribution,
                "stock_symbol": peer_symbol,
            })
            components["peer_events"].append({
                "action_type": ev.action_type,
                "age_days": age_days,
                "peer_symbol": peer_symbol,
                "contribution": round(contribution, 4),
            })
        
        # --- After filtering: maybe nothing actually contributed ---
        if not contributions:
            return None
        
        # --- Sum + squash ---
        raw_sum = sum(c["contribution"] for c in contributions)
        score = math.tanh(raw_sum * 2)  # gain=2 to keep typical sums in [-0.6, +0.6]
        
        # --- Confidence: based on most recent event ---
        min_age = min(c["age_days"] for c in contributions)
        base_confidence = _confidence_from_age_days(min_age)
        
        # If ONLY peer events contributed, reduce confidence
        has_direct = any(c["kind"] == "direct" for c in contributions)
        if not has_direct:
            base_confidence *= PEER_ONLY_CONFIDENCE_MULTIPLIER
        
        # --- Rationale string: top N by absolute contribution ---
        sorted_contribs = sorted(
            contributions, key=lambda c: abs(c["contribution"]), reverse=True
        )[:RATIONALE_TOP_N]
        
        top_descriptions = []
        for c in sorted_contribs:
            if c["kind"] == "direct":
                top_descriptions.append(
                    f"{c['action_type']} {c['age_days']}d ago ({c['contribution']:+.3f})"
                )
            else:
                top_descriptions.append(
                    f"peer {c['stock_symbol']} {c['action_type']} "
                    f"{c['age_days']}d ago ({c['contribution']:+.3f})"
                )
        
        direction_word = "tailwind" if score > 0.15 else (
            "headwind" if score < -0.15 else "neutral"
        )
        
        rationale = (
            f"Re-rating {direction_word}: {len(contributions)} events "
            f"contributing (raw sum {raw_sum:+.3f}). "
            f"Top: {'; '.join(top_descriptions)}."
        )
        
        components["raw_sum"] = round(raw_sum, 4)
        components["n_contributions"] = len(contributions)
        components["min_age_days"] = min_age
        
        return ScoreResult(
            score=score,
            confidence=base_confidence,
            rationale=rationale,
            components=components,
        )