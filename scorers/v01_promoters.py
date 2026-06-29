"""
Vector 1: Promoters (PIT Insider Trading Signal).

Hypothesis: promoter and insider trades carry private information about
the company's near-term prospects. Promoter buying = potentially bullish
(undisclosed positive info); promoter selling = potentially bearish.

Academic basis: Indian-market studies (Brochet/Lee/Srinivasan NYU 2017;
IJLLR 2026 panel) find the predictive effect is strongest for promoter
trades specifically. Purchases more informative than sales (Seyhun 1986,
Lakonishok-Lee 2001). Cluster trades (multiple insiders in short window)
especially predictive.

Score logic (per stock per date, looking back LOOKBACK_DAYS):
  For each insider trade in window:
    direction = +1 (Buy) / -1 (Sell) / 0 (unparseable)
    category_weight: Promoter 1.0 > Promoter Group 0.7 > Immediate relative 0.6
                     > Director 0.5 > KMP 0.4 > Designated/Employee 0.3 > Other 0.2
    mode_weight:     Market Purchase/Sale/Block/Bulk 1.0 > Off Market 0.8
                     > Inter-se 0.4 > Others 0.3 > ESOP 0.1
                     > Inheritance/Gift/Transmission 0.0
    magnitude:       max of (pct_holding_change/2 capped at 1.0,
                              log10(secVal/1cr)/2 capped at 1.0)
    time_decay:      linear, floored at 0.1 so edge-of-window trades still count
    contribution = direction * category_weight * mode_weight * magnitude * time_decay

  raw_score = sum(contributions)
  cluster bonus: 3+ distinct insiders in same direction over last 30d → 1.3x
  score = tanh(raw_score * 1.5)

Modes (mirroring V11 contract):
  A: qualifying trades exist → score with appropriate confidence
  B: only routine (ESOP/Inheritance) or unparseable trades → near-zero
     score, low confidence, diagnostic rationale explaining why
  C: no trades in window → None (NO_SIGNAL). Expected for PSUs and
     widely-held companies without an active controlling promoter.

V1 universe naturally narrow. Realistic coverage: 3-10 stocks on any given
day depending on filing activity. Vector fires when it fires — by design.
"""

import math
import logging
from datetime import date as date_type, timedelta
from typing import Optional

from scorers.base import VectorScorer, ScoreResult
from data.schema import Stock, InsiderTrade

logger = logging.getLogger(__name__)


# --- Weight tables ---------------------------------------------------------

CATEGORY_WEIGHTS = [
    ("promoter group", 0.7),         # must come before "promoter"
    ("immediate relative", 0.6),
    ("promoter", 1.0),
    ("director", 0.5),
    ("kmp", 0.4),
    ("key managerial", 0.4),
    ("designated", 0.3),
    ("employee", 0.3),
]
CATEGORY_DEFAULT = 0.2

MODE_WEIGHTS = [
    ("market purchase", 1.0),
    ("market sale", 1.0),
    ("off market", 0.8),
    ("block deal", 1.0),
    ("bulk deal", 1.0),
    ("inter-se", 0.4),
    ("inter se", 0.4),
    ("esop", 0.1),
    ("inheritance", 0.0),
    ("transmission", 0.0),
    ("gift", 0.0),
]
MODE_DEFAULT = 0.3

DIRECTION_MAP = {
    "buy": 1.0,
    "sell": -1.0,
    "acquisition": 1.0,
    "disposal": -1.0,
}


def _lookup_weight(text: Optional[str], table: list, default: float) -> float:
    """Case-insensitive substring lookup. Returns default if no match."""
    if not text:
        return default
    t = text.lower().strip()
    if not t or t == "-":
        return default
    for substring, weight in table:
        if substring in t:
            return weight
    return default


def _direction(transaction_type: Optional[str]) -> float:
    """Resolve transaction_type to direction. Returns 0 for unparseable."""
    if not transaction_type:
        return 0.0
    t = transaction_type.lower().strip()
    return DIRECTION_MAP.get(t, 0.0)


def _magnitude(pct_before: Optional[float], pct_after: Optional[float],
               secVal: Optional[float]) -> float:
    """
    Composite magnitude: max of pct-change signal and value signal.
      pct: |after - before| / 2.0  → 2% holding change saturates at 1.0
      val: log10(secVal/1cr) / 2.0  → ₹1 cr = 0, ₹10 cr = 0.5, ₹100 cr = 1.0
    """
    pct_mag = 0.0
    if pct_before is not None and pct_after is not None:
        pct_mag = min(1.0, abs(pct_after - pct_before) / 2.0)

    val_mag = 0.0
    if secVal is not None and secVal > 1e7:  # > ₹1 cr
        val_mag = min(1.0, max(0.0, math.log10(secVal / 1e7) / 2.0))

    return max(pct_mag, val_mag)


class PromotersScorer(VectorScorer):
    vector_id = 1
    vector_name = "Promoters"

    LOOKBACK_DAYS = 90
    CLUSTER_WINDOW_DAYS = 30
    CLUSTER_MIN_INSIDERS = 3
    CLUSTER_MULTIPLIER = 1.3
    SQUASH_GAIN = 1.5
    RECENCY_DAYS = 14

    def score_one(self, stock: Stock, asof: date_type) -> Optional[ScoreResult]:
        cutoff = asof - timedelta(days=self.LOOKBACK_DAYS)
        trades = (
            self.session.query(InsiderTrade)
            .filter(InsiderTrade.stock_id == stock.id)
            .filter(InsiderTrade.transaction_date >= cutoff)
            .filter(InsiderTrade.transaction_date <= asof)
            .order_by(InsiderTrade.transaction_date.desc())
            .all()
        )

        if not trades:
            return None  # Mode C

        contributions = []
        recent_trade_present = False
        promoter_trade_present = False
        cluster_buyers = set()
        cluster_sellers = set()
        cluster_cutoff = asof - timedelta(days=self.CLUSTER_WINDOW_DAYS)
        contribution_details = []

        for t in trades:
            direction = _direction(t.transaction_type)
            if direction == 0.0:
                continue

            cat_w = _lookup_weight(t.person_category, CATEGORY_WEIGHTS, CATEGORY_DEFAULT)
            mode_w = _lookup_weight(t.acq_mode, MODE_WEIGHTS, MODE_DEFAULT)
            mag = _magnitude(t.pct_before, t.pct_after, t.securities_value)
            days_old = (asof - t.transaction_date).days
            time_decay = max(0.1, 1.0 - days_old / self.LOOKBACK_DAYS)

            contribution = direction * cat_w * mode_w * mag * time_decay

            if days_old <= self.RECENCY_DAYS:
                recent_trade_present = True
            if "promoter" in (t.person_category or "").lower():
                promoter_trade_present = True

            if t.transaction_date >= cluster_cutoff and cat_w >= 0.5 and mode_w >= 0.5:
                if direction > 0:
                    cluster_buyers.add(t.person_name)
                else:
                    cluster_sellers.add(t.person_name)

            if contribution != 0.0:
                contributions.append(contribution)
                contribution_details.append({
                    "person": t.person_name,
                    "category": t.person_category,
                    "mode": t.acq_mode,
                    "type": t.transaction_type,
                    "date": t.transaction_date.isoformat(),
                    "pct_before": t.pct_before,
                    "pct_after": t.pct_after,
                    "value": t.securities_value,
                    "contribution": round(contribution, 4),
                })

        if not contributions:
            # Mode B: trades exist but produced zero contribution. Diagnose why.
            n_unparseable_type = sum(1 for t in trades if _direction(t.transaction_type) == 0)
            n_routine_mode = sum(
                1 for t in trades
                if _lookup_weight(t.acq_mode, MODE_WEIGHTS, MODE_DEFAULT) < 0.2
            )
            reason_parts = []
            if n_unparseable_type:
                reason_parts.append(f"{n_unparseable_type} unparseable transaction_type")
            if n_routine_mode:
                reason_parts.append(f"{n_routine_mode} routine acq_mode (ESOP/Gift)")
            reason = "; ".join(reason_parts) or "all zero-magnitude or zero-weight"

            return ScoreResult(
                score=0.0,
                confidence=0.1,
                rationale=(
                    f"{len(trades)} insider trade(s) in last {self.LOOKBACK_DAYS}d "
                    f"but no contributing signal: {reason}."
                ),
                components={
                    "n_trades_in_window": len(trades),
                    "n_contributing": 0,
                    "n_unparseable_type": n_unparseable_type,
                    "n_routine_mode": n_routine_mode,
                    "mode": "B",
                },
            )

        raw_score = sum(contributions)

        cluster_applied = None
        if len(cluster_buyers) >= self.CLUSTER_MIN_INSIDERS and raw_score > 0:
            raw_score *= self.CLUSTER_MULTIPLIER
            cluster_applied = f"buy cluster: {len(cluster_buyers)} insiders in {self.CLUSTER_WINDOW_DAYS}d"
        elif len(cluster_sellers) >= self.CLUSTER_MIN_INSIDERS and raw_score < 0:
            raw_score *= self.CLUSTER_MULTIPLIER
            cluster_applied = f"sell cluster: {len(cluster_sellers)} insiders in {self.CLUSTER_WINDOW_DAYS}d"

        score = math.tanh(raw_score * self.SQUASH_GAIN)

        n_distinct_insiders = len({d["person"] for d in contribution_details})
        confidence = 0.3
        if promoter_trade_present:
            confidence += 0.2
        if n_distinct_insiders >= 2:
            confidence += 0.2
        if recent_trade_present:
            confidence += 0.2
        confidence = min(1.0, confidence)

        direction_word = "bullish" if score > 0.1 else "bearish" if score < -0.1 else "neutral"
        n_buys = sum(1 for c in contributions if c > 0)
        n_sells = sum(1 for c in contributions if c < 0)
        rationale = (
            f"Insider {direction_word}: {n_distinct_insiders} insider(s), "
            f"{n_buys} buy / {n_sells} sell contribution(s) in last "
            f"{self.LOOKBACK_DAYS}d."
        )
        if cluster_applied:
            rationale += f" {cluster_applied} (1.3x boost)."
        if promoter_trade_present:
            rationale += " Promoter trade present."

        components = {
            "n_trades_in_window": len(trades),
            "n_contributing": len(contributions),
            "n_distinct_insiders": n_distinct_insiders,
            "n_buys": n_buys,
            "n_sells": n_sells,
            "raw_score": round(raw_score, 4),
            "cluster_buyers": len(cluster_buyers),
            "cluster_sellers": len(cluster_sellers),
            "cluster_applied": cluster_applied,
            "promoter_trade_present": promoter_trade_present,
            "recent_trade_present": recent_trade_present,
            "mode": "A",
            "trades": contribution_details,
        }

        return ScoreResult(
            score=score,
            confidence=confidence,
            rationale=rationale,
            components=components,
        )