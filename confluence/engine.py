"""
Confluence Engine.

For each stock on each date, aggregates whatever vector scores exist into a single
composite. The rule: confidence-weighted mean of vector scores.

Outputs go to confluence_scores table so the dashboard reads instantly.

Phase 1 active vectors: V4, V13.
As more vectors come online, they automatically get pulled in — no engine changes needed.
That's the point of the unified VectorScore table.
"""

from datetime import date as date_type
from collections import defaultdict
import json
import logging

from data.schema import (
    Stock, VectorScore, ConfluenceScore, get_session,
)

logger = logging.getLogger(__name__)


# Per-vector weights for the composite. Default 1.0 each.
# Tweak as evidence accumulates about which vectors actually generate alpha.
VECTOR_WEIGHTS = {
    1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0,
    6: 1.0, 7: 1.0, 8: 1.0, 9: 1.0, 10: 1.0,
    11: 1.0, 12: 1.0, 13: 1.0, 14: 1.0, 15: 1.0,
}

POS_THRESHOLD = 0.15        # |score| above this counts as "active positive/negative"


def classify_direction(composite: float) -> str:
    if composite >= 0.20:
        return "bullish"
    if composite <= -0.20:
        return "bearish"
    return "neutral"


def compute_confluence(asof: date_type, session=None):
    session = session or get_session()

    # Pull all vector scores for the date
    rows = (
        session.query(VectorScore)
        .filter(VectorScore.date == asof)
        .all()
    )

    if not rows:
        logger.warning(f"No vector scores found for {asof}; nothing to compute")
        return 0

    by_stock = defaultdict(list)
    for row in rows:
        by_stock[row.stock_id].append(row)

    written = 0
    for stock_id, scores in by_stock.items():
        weighted_sum = 0.0
        weight_total = 0.0
        n_pos = n_neg = 0
        breakdown = {}

        for s in scores:
            w = VECTOR_WEIGHTS.get(s.vector_id, 1.0) * s.confidence
            weighted_sum += s.score * w
            weight_total += w
            if s.score >= POS_THRESHOLD:
                n_pos += 1
            elif s.score <= -POS_THRESHOLD:
                n_neg += 1
            breakdown[s.vector_id] = {
                "score": round(s.score, 3),
                "confidence": round(s.confidence, 3),
                "rationale": s.rationale,
            }

        composite = weighted_sum / weight_total if weight_total > 0 else 0.0

        # Upsert
        existing = (
            session.query(ConfluenceScore)
            .filter_by(stock_id=stock_id, date=asof)
            .first()
        )
        if existing:
            existing.composite = composite
            existing.n_vectors_positive = n_pos
            existing.n_vectors_negative = n_neg
            existing.n_vectors_active = len(scores)
            existing.direction = classify_direction(composite)
            existing.vector_breakdown_json = json.dumps(breakdown, default=str)
        else:
            session.add(
                ConfluenceScore(
                    stock_id=stock_id,
                    date=asof,
                    composite=composite,
                    n_vectors_positive=n_pos,
                    n_vectors_negative=n_neg,
                    n_vectors_active=len(scores),
                    direction=classify_direction(composite),
                    vector_breakdown_json=json.dumps(breakdown, default=str),
                )
            )
        written += 1

    session.commit()
    logger.info(f"Confluence: wrote {written} composite scores for {asof}")
    return written
