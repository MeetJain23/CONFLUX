"""
Vector 13: Geopolitics & Macros — skeleton.

Will compute per-stock score from sector exposure to macro variables (FX, rates, etc.).
"""

from datetime import date as date_type
import logging

from scorers.base import VectorScorer, ScoreResult
from data.schema import Stock

logger = logging.getLogger(__name__)


class MacroScorer(VectorScorer):
    vector_id = 13
    vector_name = "Geopolitics & Macros"

    def score_one(self, stock: Stock, asof: date_type) -> ScoreResult | None:
        if not stock.sector:
            return None
        # TODO: lookup sector exposure, compute macro momentum, aggregate
        return ScoreResult(score=0.0, confidence=0.0, rationale="not implemented")
