"""Trend-following momentum strategy.

Long when ALL hold (computed on closed bars only):
  * fast SMA (20) above slow SMA (50)  -> uptrend structure
  * close above the 200-SMA regime filter -> only trade with the long-term trend
  * RSI(14) in (50, 72) -> trending, not yet exhausted

Exit to flat when the fast/slow relationship breaks or price loses the
200-SMA regime. ATR-based stops/targets are applied by the backtester.
"""

from __future__ import annotations

import pandas as pd

from ..data.indicators import rsi, sma
from .base import Strategy


class MomentumStrategy(Strategy):
    name = "momentum"
    warmup = 200

    def __init__(
        self,
        fast: int = 20,
        slow: int = 50,
        regime: int = 200,
        rsi_period: int = 14,
        rsi_lo: float = 50.0,
        rsi_hi: float = 72.0,
    ) -> None:
        self.fast, self.slow, self.regime = fast, slow, regime
        self.rsi_period, self.rsi_lo, self.rsi_hi = rsi_period, rsi_lo, rsi_hi

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._with_atr(df)
        out["sma_fast"] = sma(out["close"], self.fast)
        out["sma_slow"] = sma(out["close"], self.slow)
        out["sma_regime"] = sma(out["close"], self.regime)
        out["rsi"] = rsi(out["close"], self.rsi_period)

        long_cond = (
            (out["sma_fast"] > out["sma_slow"])
            & (out["close"] > out["sma_regime"])
            & (out["rsi"] > self.rsi_lo)
            & (out["rsi"] < self.rsi_hi)
        )
        # NaN in any input -> not eligible (stay flat), never a fabricated 1.
        out["target"] = long_cond.fillna(False).astype(int)
        return out
