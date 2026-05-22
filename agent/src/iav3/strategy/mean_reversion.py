"""Buy-the-dip mean reversion, trend-filtered.

Only buys oversold dips *inside a long-term uptrend* (close > 200-SMA), which
avoids the classic failure mode of catching falling knives in downtrends.

Enter long when RSI(14) < 30 and close > 200-SMA.
Exit to flat when RSI(14) > 55 (mean reversion completed) or close < 200-SMA
(regime broken). Implemented as a stateful pass so the position persists
between the entry and exit triggers rather than only on the single oversold bar.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.indicators import rsi, sma
from .base import Strategy


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"
    warmup = 200

    def __init__(
        self,
        rsi_period: int = 14,
        entry_rsi: float = 30.0,
        exit_rsi: float = 55.0,
        regime: int = 200,
    ) -> None:
        self.rsi_period = rsi_period
        self.entry_rsi = entry_rsi
        self.exit_rsi = exit_rsi
        self.regime = regime

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._with_atr(df)
        out["rsi"] = rsi(out["close"], self.rsi_period)
        out["sma_regime"] = sma(out["close"], self.regime)

        in_uptrend = out["close"] > out["sma_regime"]
        enter = (out["rsi"] < self.entry_rsi) & in_uptrend
        exit_ = (out["rsi"] > self.exit_rsi) | (~in_uptrend)

        # Stateful hold: once entered, stay long until an exit trigger fires.
        target = np.zeros(len(out), dtype=int)
        holding = False
        e = enter.to_numpy()
        x = exit_.to_numpy()
        for i in range(len(out)):
            if holding:
                if x[i]:
                    holding = False
            elif e[i]:
                holding = True
            target[i] = 1 if holding else 0
        out["target"] = target
        return out
