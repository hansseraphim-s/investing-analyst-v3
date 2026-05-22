"""Strategy abstraction.

A strategy is a pure function of price history. `generate` returns the input
frame enriched with indicator columns plus a `target` column:

    target == 1  -> want to be long
    target == 0  -> want to be flat

It MUST NOT use future information: the value of `target` at row *i* may only
depend on data available at or before row *i*. The backtester enforces this
further by acting on `target.shift(1)` (decide on close, act next open).

`atr` must also be present so the engine can size ATR-based stops/targets.

Optionally, a strategy may also emit a `target_weight` column (a fraction in
[0, 1]) for volatility-targeted / risk-scaled position sizing. When present,
the backtester sizes each entry at equity * target_weight instead of the flat
invest_fraction. The engine never takes leverage: weight is clamped to 1.0.
Strategies that omit the column keep the original fixed-fraction behavior.
"""

from __future__ import annotations

import abc

import pandas as pd

from ..data.indicators import atr


class Strategy(abc.ABC):
    name: str = "base"
    #: minimum bars required before the strategy can emit a non-NaN target
    warmup: int = 200

    @abc.abstractmethod
    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return `df` plus indicator columns, `atr`, and an int `target` (0/1)."""

    @staticmethod
    def _with_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        out = df.copy()
        out["atr"] = atr(out["high"], out["low"], out["close"], period)
        return out

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"<Strategy {self.name}>"
