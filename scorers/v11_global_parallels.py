"""
Vector 11: Global Parallels.

Hypothesis: when an Indian subsidiary's international parent moves, the
subsidiary tends to follow with a lag. Detect the gap between subsidiary's
actual return and what its β to the parent would predict. Catch-up bullish
when sub lags expected, fade bearish when sub runs ahead.

Score logic (per stock per date):
  Get aligned daily closes of sub (PriceDaily) and parent (MacroDaily under
  PARENT_<ticker>), inner-join on common dates, compute returns.
  β = OLS slope of sub_returns on parent_returns over LOOKBACK_DAYS.
  Over last W=10 trading days:
      parent_cum_return = parent[-1] / parent[-W-1] - 1
      sub_cum_return    = sub[-1]    / sub[-W-1]    - 1
      expected_sub      = β * parent_cum_return
      residual          = sub_cum_return - expected_sub
  Normalize: z = residual / std(historical W-day residuals)
  Score: tanh(-z / SQUASH_GAIN)
    Negative residual (sub lagging) → positive score (catch-up bullish).

Mode A: parent_ticker mapped + sufficient history     → score
Mode B: parent_ticker mapped, insufficient history    → None (NO_SIGNAL)
Mode C: no parent_ticker                              → None (NO_SIGNAL)

V11 universe is intentionally narrow — most of CONFLUX's 86 stocks have
no global parent. Expect ~16 of 86 to score on any given day.
"""

from datetime import date as date_type, timedelta
import math
import logging

import numpy as np
import pandas as pd

from scorers.base import VectorScorer, ScoreResult
from data.schema import Stock, PriceDaily, MacroDaily

logger = logging.getLogger(__name__)


class GlobalParallelsScorer(VectorScorer):
    vector_id = 11
    vector_name = "Global Parallels"

    LOOKBACK_DAYS = 150       # calendar days; ~100 trading days
    MIN_HISTORY_DAYS = 60     # gate: trading days of common history required
    W = 10                    # snapshot window for cumulative returns
    SQUASH_GAIN = 2.0         # tanh steepness

    def _get_sub_prices(self, stock: Stock, asof: date_type) -> pd.Series:
        rows = (
            self.session.query(PriceDaily.date, PriceDaily.close)
            .filter(PriceDaily.stock_id == stock.id)
            .filter(PriceDaily.date <= asof)
            .filter(PriceDaily.date >= asof - timedelta(days=self.LOOKBACK_DAYS))
            .filter(PriceDaily.close.isnot(None))
            .order_by(PriceDaily.date.asc())
            .all()
        )
        if not rows:
            return pd.Series(dtype=float)
        return pd.Series({r.date: float(r.close) for r in rows if r.close is not None})

    def _get_parent_prices(self, parent_ticker: str, asof: date_type) -> pd.Series:
        series_code = f"PARENT_{parent_ticker}"
        rows = (
            self.session.query(MacroDaily.date, MacroDaily.value)
            .filter(MacroDaily.series_code == series_code)
            .filter(MacroDaily.date <= asof)
            .filter(MacroDaily.date >= asof - timedelta(days=self.LOOKBACK_DAYS))
            .filter(MacroDaily.value.isnot(None))
            .order_by(MacroDaily.date.asc())
            .all()
        )
        if not rows:
            return pd.Series(dtype=float)
        return pd.Series({r.date: float(r.value) for r in rows if r.value is not None})

    def score_one(self, stock: Stock, asof: date_type) -> ScoreResult | None:
        parent_ticker = getattr(stock, "parent_ticker", None)
        if not parent_ticker:
            return None  # Mode C

        sub_prices = self._get_sub_prices(stock, asof)
        parent_prices = self._get_parent_prices(parent_ticker, asof)
        if sub_prices.empty or parent_prices.empty:
            return None

        # Inner-join on common dates (handles holiday mismatches)
        df = pd.DataFrame({"sub": sub_prices, "parent": parent_prices}).dropna()
        df["sub_ret"] = df["sub"].pct_change()
        df["parent_ret"] = df["parent"].pct_change()
        df = df.dropna()

        if len(df) < self.MIN_HISTORY_DAYS:
            return None  # Mode B

        # β: OLS slope, sub on parent
        cov_matrix = np.cov(df["sub_ret"].values, df["parent_ret"].values, ddof=1)
        cov = float(cov_matrix[0, 1])
        var_parent = float(cov_matrix[1, 1])
        if var_parent <= 0:
            return None
        beta = cov / var_parent

        if len(df) < self.W + 1:
            return None

        # Snapshot: cumulative returns over last W observations
        parent_cum = float(df["parent"].iloc[-1] / df["parent"].iloc[-self.W - 1] - 1.0)
        sub_cum = float(df["sub"].iloc[-1] / df["sub"].iloc[-self.W - 1] - 1.0)
        expected_sub = beta * parent_cum
        residual_now = sub_cum - expected_sub

        # Historical W-day residuals for z-score normalization.
        # Exclude the last W observations to avoid overlap with the snapshot window.
        residuals = []
        end_idx = len(df) - self.W
        for i in range(self.W, end_idx):
            p = float(df["parent"].iloc[i] / df["parent"].iloc[i - self.W] - 1.0)
            s = float(df["sub"].iloc[i] / df["sub"].iloc[i - self.W] - 1.0)
            residuals.append(s - beta * p)

        if len(residuals) < 10:
            return None
        residual_std = float(np.std(residuals, ddof=1))
        if residual_std <= 0:
            return None

        z = residual_now / residual_std
        score = math.tanh(-z / self.SQUASH_GAIN)

        # Confidence
        history_factor = min(1.0, len(df) / 100.0)
        # Penalize unusual β (relationship may have broken or never existed)
        beta_factor = 1.0 if 0.2 <= abs(beta) <= 2.5 else 0.6
        confidence = round(history_factor * beta_factor, 2)

        direction_word = (
            "catch-up bullish" if score > 0.1
            else "fade bearish" if score < -0.1
            else "in-line"
        )

        rationale = (
            f"Parent {parent_ticker} {parent_cum:+.2%} over {self.W}d "
            f"(β={beta:.2f} → expects {expected_sub:+.2%}). "
            f"{stock.symbol_nse} actual {sub_cum:+.2%}. "
            f"Residual {residual_now * 100:+.1f}pp (z={z:+.2f}). "
            f"{direction_word}."
        )

        components = {
            "parent_ticker": parent_ticker,
            "W_days": self.W,
            "parent_return_W": round(parent_cum, 4),
            "sub_return_W": round(sub_cum, 4),
            "beta": round(beta, 3),
            "expected_sub_return": round(expected_sub, 4),
            "residual": round(residual_now, 4),
            "residual_std": round(residual_std, 4),
            "z_score": round(z, 3),
            "history_days": len(df),
        }

        return ScoreResult(
            score=score,
            confidence=confidence,
            rationale=rationale,
            components=components,
        )