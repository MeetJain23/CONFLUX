"""
Vector 4: Input Material Cost.

Computes 3-month change in input commodity prices.
"""

from datetime import date as date_type, timedelta
import logging

from scorers.base import VectorScorer, ScoreResult
from data.schema import Stock, Commodity, StockInputCommodity, CommodityDaily

logger = logging.getLogger(__name__)


class InputMaterialCostScorer(VectorScorer):
    vector_id = 4
    vector_name = "Input Material Cost"

    LOOKBACK_DAYS = 90

    def _get_commodity_price(self, commodity_id: int, asof: date_type, tolerance_days: int = 7):
        """Latest available commodity close at-or-before asof, within tolerance."""
        row = (
            self.session.query(CommodityDaily)
            .filter(
                CommodityDaily.commodity_id == commodity_id,
                CommodityDaily.date <= asof,
                CommodityDaily.date >= asof - timedelta(days=tolerance_days),
            )
            .order_by(CommodityDaily.date.desc())
            .first()
        )
        return row.close if row else None

    def score_one(self, stock: Stock, asof: date_type) -> ScoreResult | None:
        links = (
            self.session.query(StockInputCommodity)
            .filter(StockInputCommodity.stock_id == stock.id)
            .all()
        )
        if not links:
            return None

        components = {}
        for link in links:
            commodity = self.session.get(Commodity, link.commodity_id)
            if not commodity:
                continue
            price_now = self._get_commodity_price(commodity.id, asof)
            price_then = self._get_commodity_price(commodity.id, asof - timedelta(days=self.LOOKBACK_DAYS))
            components[commodity.code] = {
                "price_now": price_now,
                "price_then": price_then,
            }
        # TODO: aggregate into final score
        return ScoreResult(score=0.0, confidence=0.0, rationale="lookup only", components=components)
