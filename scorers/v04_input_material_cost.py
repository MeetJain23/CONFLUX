"""
Vector 4: Input Material Cost — skeleton.

Will compute confidence-weighted score based on 3-month change in input commodity
prices, weighted by their share of COGS.
"""

from datetime import date as date_type
import logging

from scorers.base import VectorScorer, ScoreResult
from data.schema import Stock, StockInputCommodity

logger = logging.getLogger(__name__)


class InputMaterialCostScorer(VectorScorer):
    vector_id = 4
    vector_name = "Input Material Cost"

    LOOKBACK_DAYS = 90

    def score_one(self, stock: Stock, asof: date_type) -> ScoreResult | None:
        links = (
            self.session.query(StockInputCommodity)
            .filter(StockInputCommodity.stock_id == stock.id)
            .all()
        )
        if not links:
            return None
        # TODO: compute contributions from each commodity's price change
        return ScoreResult(score=0.0, confidence=0.0, rationale="not implemented")
