"""
Vector 4: Input Material Cost.

Computes weighted contribution from each input commodity's 3-month change.
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

    def _get_commodity_price(self, commodity_id, asof, tolerance_days=7):
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
            .filter(StockInputCommodity.stock_id == stock.id).all()
        )
        if not links:
            return None

        contributions = []
        components = {}
        for link in links:
            commodity = self.session.get(Commodity, link.commodity_id)
            if not commodity:
                continue
            price_now = self._get_commodity_price(commodity.id, asof)
            price_then = self._get_commodity_price(commodity.id, asof - timedelta(days=self.LOOKBACK_DAYS))
            if price_now is None or price_then is None or price_then == 0:
                continue
            pct_change = (price_now / price_then) - 1.0
            weight = (link.weight_pct or 0.0) / 100.0
            contribution = -pct_change * weight
            contributions.append(contribution)
            components[commodity.code] = {
                "pct_change_3m": round(pct_change, 4),
                "weight": round(weight, 3),
                "contribution": round(contribution, 4),
            }

        if not contributions:
            return None
        raw_score = sum(contributions)
        # TODO: squash through tanh, compute confidence, add rationale
        return ScoreResult(score=raw_score, confidence=1.0,
                           rationale=f"raw {raw_score:+.3f}", components=components)
