"""
Vector 4: Input Material Cost.

Hypothesis: when a company's key input commodity prices fall, gross margins expand,
which is bullish for the stock (and vice versa). Strength of signal depends on:
- the % of COGS that input represents (weight_pct in metadata)
- the magnitude and persistence of the price move

Score logic (per stock per date):
  for each input commodity linked to the stock:
      pct_change_3m  = (price_today / price_90d_ago) - 1
      contribution   = -pct_change_3m * (weight_pct / 100)
      # negative sign: commodity UP = bad for company = negative score
  raw_score = sum of contributions across all input commodities
  score     = tanh(raw_score * 3)   # squash to [-1, +1] with reasonable slope

Confidence:
  - 1.0 if we have all input commodities mapped and >=80% of weight populated
  - lower if metadata is partial
  - 0.0 if no input commodities mapped (stock skipped)

This is a directional signal, not a price target. It says: "tailwind/headwind on margins."
"""

from datetime import date as date_type, timedelta
import math
import logging

from sqlalchemy import and_

from scorers.base import VectorScorer, ScoreResult
from data.schema import (
    Stock, Commodity, StockInputCommodity, CommodityDaily,
)

logger = logging.getLogger(__name__)


class InputMaterialCostScorer(VectorScorer):
    vector_id = 4
    vector_name = "Input Material Cost"

    LOOKBACK_DAYS = 90      # 3-month change window
    SQUASH_GAIN = 3.0       # tanh steepness

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
        # Get input commodity links for this stock
        links = (
            self.session.query(StockInputCommodity)
            .filter(StockInputCommodity.stock_id == stock.id)
            .all()
        )

        if not links:
            # No metadata → cannot score. Don't fabricate.
            return None

        contributions = []
        components = {}
        total_weight = 0.0
        n_with_data = 0

        for link in links:
            commodity = self.session.get(Commodity, link.commodity_id)
            if commodity is None:
                continue

            price_now = self._get_commodity_price(commodity.id, asof)
            price_then = self._get_commodity_price(commodity.id, asof - timedelta(days=self.LOOKBACK_DAYS))

            if price_now is None or price_then is None or price_then == 0:
                components[commodity.code] = {"status": "missing_data"}
                continue

            pct_change = (price_now / price_then) - 1.0
            weight = (link.weight_pct or 0.0) / 100.0
            # Negative sign: commodity UP = headwind = negative score
            contribution = -pct_change * weight

            contributions.append(contribution)
            total_weight += weight
            n_with_data += 1
            components[commodity.code] = {
                "pct_change_3m": round(pct_change, 4),
                "weight": round(weight, 3),
                "contribution": round(contribution, 4),
            }

        if not contributions:
            return None

        raw_score = sum(contributions)
        score = math.tanh(raw_score * self.SQUASH_GAIN)

        # Confidence: how much of the input cost structure do we actually cover?
        confidence = min(1.0, total_weight) * (n_with_data / max(len(links), 1))

        # Human-readable rationale
        direction = "tailwind" if score > 0.1 else "headwind" if score < -0.1 else "neutral"
        top_driver = max(
            components.items(),
            key=lambda kv: abs(kv[1].get("contribution", 0)) if isinstance(kv[1], dict) and "contribution" in kv[1] else 0,
            default=(None, None),
        )
        rationale = (
            f"Input cost {direction}: 3-month change across {n_with_data} input commodities "
            f"yields raw contribution {raw_score:+.3f}."
        )
        if top_driver[0]:
            rationale += f" Top driver: {top_driver[0]}."

        return ScoreResult(
            score=score,
            confidence=confidence,
            rationale=rationale,
            components=components,
        )
