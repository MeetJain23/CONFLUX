"""
Vector 13: Geopolitics & Macros.

Hypothesis: macroeconomic conditions create broad tailwinds/headwinds that affect
stocks differently based on their exposures. Same macro print = different sector impact.

Phase 1 macro signals tracked:
  - USDINR              : INR weakness = exporter tailwind, importer headwind
  - INDIA10Y            : rising rates = headwind for high-debt and rate-sensitive sectors
  - US10Y               : rising = global risk-off, hurts EM equities broadly
  - BRENT_CRUDE         : already covered by V4 for users, but aggregate macro effect
                          is captured here for refiners/oil-marketing/aviation/paint sectors
  - DXY (dollar index)  : risk-off proxy

Per-stock scoring relies on sector-level exposure profiles (configured in
macro_exposures dict below). This is a coarse v1 — Phase 2 will pull granular
% export / debt-load / fuel-cost-share from metadata.

The signal is the DIRECTION and MOMENTUM of macro variables, not absolute level.
"""

from datetime import date as date_type, timedelta
import math
import logging

from scorers.base import VectorScorer, ScoreResult
from data.schema import Stock, MacroDaily

logger = logging.getLogger(__name__)


# Sector exposure to macro variables.
# Each value ∈ [-1, +1]: sign = direction of expected stock impact when macro variable RISES.
# Magnitude = relative sensitivity.
# This is editable v1 — refine as we observe real signal quality.
SECTOR_MACRO_EXPOSURE = {
    "IT":            {"USDINR": +0.8,  "US10Y": -0.4, "INDIA10Y":  0.0,  "BRENT": -0.1, "DXY": +0.2},
    "Pharma":        {"USDINR": +0.6,  "US10Y": -0.3, "INDIA10Y": -0.1,  "BRENT":  0.0, "DXY": +0.1},
    "Auto":          {"USDINR": -0.3,  "US10Y": -0.2, "INDIA10Y": -0.5,  "BRENT": -0.4, "DXY": -0.2},
    "Banks":         {"USDINR": -0.2,  "US10Y": -0.3, "INDIA10Y": -0.4,  "BRENT":  0.0, "DXY": -0.1},
    "NBFC":          {"USDINR": -0.3,  "US10Y": -0.4, "INDIA10Y": -0.6,  "BRENT": -0.1, "DXY": -0.2},
    "FMCG":          {"USDINR": -0.4,  "US10Y": -0.2, "INDIA10Y": -0.2,  "BRENT": -0.3, "DXY": -0.1},
    "Metals":        {"USDINR": +0.4,  "US10Y": -0.4, "INDIA10Y": -0.2,  "BRENT": +0.2, "DXY": -0.5},
    "Oil & Gas":     {"USDINR": +0.2,  "US10Y": -0.2, "INDIA10Y": -0.2,  "BRENT": +0.5, "DXY": -0.2},
    "Refiners":      {"USDINR": -0.5,  "US10Y": -0.2, "INDIA10Y": -0.2,  "BRENT": -0.5, "DXY": -0.2},
    "Aviation":      {"USDINR": -0.6,  "US10Y": -0.2, "INDIA10Y": -0.3,  "BRENT": -0.7, "DXY": -0.2},
    "Paints":        {"USDINR": -0.3,  "US10Y": -0.1, "INDIA10Y": -0.2,  "BRENT": -0.5, "DXY": -0.1},
    "Cement":        {"USDINR": -0.2,  "US10Y": -0.2, "INDIA10Y": -0.4,  "BRENT": -0.3, "DXY": -0.1},
    "Realty":        {"USDINR": -0.2,  "US10Y": -0.3, "INDIA10Y": -0.7,  "BRENT": -0.1, "DXY": -0.2},
    "Capital Goods": {"USDINR": -0.1,  "US10Y": -0.2, "INDIA10Y": -0.3,  "BRENT": -0.2, "DXY": -0.1},
    "Defence":       {"USDINR": +0.1,  "US10Y": -0.1, "INDIA10Y": -0.1,  "BRENT":  0.0, "DXY":  0.0},
    "Chemicals":     {"USDINR": +0.2,  "US10Y": -0.2, "INDIA10Y": -0.2,  "BRENT": -0.2, "DXY": -0.1},
    "Textiles":      {"USDINR": +0.5,  "US10Y": -0.2, "INDIA10Y": -0.2,  "BRENT": -0.2, "DXY": +0.1},
}

DEFAULT_EXPOSURE = {"USDINR": 0.0, "US10Y": -0.2, "INDIA10Y": -0.2, "BRENT": -0.1, "DXY": -0.1}

LOOKBACK_DAYS = 30          # macro momentum window
SQUASH_GAIN = 4.0


class MacroScorer(VectorScorer):
    vector_id = 13
    vector_name = "Geopolitics & Macros"

    def _get_macro_value(self, series_code: str, asof: date_type, tolerance_days: int = 7):
        row = (
            self.session.query(MacroDaily)
            .filter(
                MacroDaily.series_code == series_code,
                MacroDaily.date <= asof,
                MacroDaily.date >= asof - timedelta(days=tolerance_days),
            )
            .order_by(MacroDaily.date.desc())
            .first()
        )
        return row.value if row else None

    def _get_macro_momentum(self, series_code: str, asof: date_type):
        """Returns pct change over LOOKBACK_DAYS, or None if data missing."""
        now = self._get_macro_value(series_code, asof)
        then = self._get_macro_value(series_code, asof - timedelta(days=LOOKBACK_DAYS))
        if now is None or then is None or then == 0:
            return None
        return (now / then) - 1.0

    def score_one(self, stock: Stock, asof: date_type) -> ScoreResult | None:
        exposure = SECTOR_MACRO_EXPOSURE.get(stock.sector, DEFAULT_EXPOSURE)

        contributions = []
        components = {}
        n_with_data = 0

        for series_code, sensitivity in exposure.items():
            momentum = self._get_macro_momentum(series_code, asof)
            if momentum is None:
                components[series_code] = {"status": "missing_data"}
                continue
            contribution = sensitivity * momentum * 10   # scale momentum
            contributions.append(contribution)
            n_with_data += 1
            components[series_code] = {
                "momentum_30d": round(momentum, 4),
                "sensitivity": sensitivity,
                "contribution": round(contribution, 4),
            }

        if not contributions:
            return None

        raw_score = sum(contributions) / len(contributions)
        score = math.tanh(raw_score * SQUASH_GAIN)

        # Confidence: did we get all expected macro series?
        confidence = n_with_data / len(exposure)

        if stock.sector not in SECTOR_MACRO_EXPOSURE:
            confidence *= 0.5            # generic exposure profile = less confident

        direction = "tailwind" if score > 0.1 else "headwind" if score < -0.1 else "neutral"
        rationale = (
            f"Macro {direction} for {stock.sector or 'unmapped sector'}: "
            f"raw {raw_score:+.3f} from {n_with_data}/{len(exposure)} series."
        )

        return ScoreResult(
            score=score,
            confidence=confidence,
            rationale=rationale,
            components=components,
        )
