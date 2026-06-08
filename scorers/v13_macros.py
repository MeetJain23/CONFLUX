"""
Vector 13: Geopolitics & Macros.

Sector exposure profiles encode how each sector responds to macro variable moves.
"""

from datetime import date as date_type
import logging

from scorers.base import VectorScorer, ScoreResult
from data.schema import Stock

logger = logging.getLogger(__name__)


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


class MacroScorer(VectorScorer):
    vector_id = 13
    vector_name = "Geopolitics & Macros"

    def score_one(self, stock: Stock, asof: date_type) -> ScoreResult | None:
        exposure = SECTOR_MACRO_EXPOSURE.get(stock.sector, DEFAULT_EXPOSURE)
        # TODO: pull macro momentum, compute contributions
        return ScoreResult(score=0.0, confidence=0.0,
                           rationale=f"exposure profile for {stock.sector}",
                           components={"exposure": exposure})
