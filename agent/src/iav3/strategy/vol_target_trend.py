"""Volatility-targeted trend following.

Two independent pieces, deliberately separated:

  1. DIRECTION (binary trend filter): long only when the fast EMA is above the
     slow EMA *and* price is above the 200-SMA regime line; otherwise flat.
  2. SIZE (continuous): scale the position inversely to recent realized
     volatility so the book targets a constant annualized volatility. Weight
     is clamped to [0, 1] — this strategy never uses leverage.

Why this one: volatility targeting is the best-documented improver of
*risk-adjusted* return (Sharpe / drawdown) in the trend-following literature
(e.g. Moskowitz–Ooi–Pedersen, "Time Series Momentum", 2012; AQR vol-scaling
work). It does NOT reliably increase raw return and does NOT "beat the
market" — its honest, repeatable effect is smaller drawdowns for a given
return. The backtester will show that trade-off rather than hide it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.indicators import annualized_volatility, ema, sma
from .base import Strategy


class VolTargetTrendStrategy(Strategy):
    name = "vol_target_trend"
    warmup = 200

    def __init__(
        self,
        fast: int = 20,
        slow: int = 100,
        regime: int = 200,
        vol_window: int = 20,
        target_vol: float = 0.15,   # 15% annualized
        max_weight: float = 1.0,    # hard no-leverage cap
    ) -> None:
        self.fast, self.slow, self.regime = fast, slow, regime
        self.vol_window = vol_window
        self.target_vol = target_vol
        self.max_weight = max_weight

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._with_atr(df)
        out["ema_fast"] = ema(out["close"], self.fast)
        out["ema_slow"] = ema(out["close"], self.slow)
        out["sma_regime"] = sma(out["close"], self.regime)
        out["realized_vol"] = annualized_volatility(out["close"], self.vol_window)

        trend_on = (
            (out["ema_fast"] > out["ema_slow"])
            & (out["close"] > out["sma_regime"])
        )
        out["target"] = trend_on.fillna(False).astype(int)

        # Inverse-vol sizing. Guard zero/NaN vol -> weight 0 (no fabricated
        # leverage from a near-zero denominator).
        rv = out["realized_vol"].replace(0.0, np.nan)
        weight = (self.target_vol / rv).clip(lower=0.0, upper=self.max_weight)
        weight = weight.where(out["target"] == 1, 0.0)
        out["target_weight"] = weight.replace([np.inf, -np.inf], 0.0).fillna(0.0)
        return out
