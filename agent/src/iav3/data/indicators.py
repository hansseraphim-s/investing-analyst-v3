"""Vectorized technical indicators.

These are deliberately textbook-correct (the v1 codebase used a non-standard
RSI that diverged from every charting platform). All functions:
  * operate on pandas Series/DataFrame,
  * return a Series aligned to the input index,
  * leave the warm-up region as NaN rather than fabricating values.

Callers must check `pd.isna(...)` before acting on the latest value.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index using Wilder's smoothing (the standard).

    Wilder's RSI smooths gains/losses with an EMA of alpha = 1/period.
    Returns values in [0, 100]; the first `period` rows are NaN.
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 -> rs == inf -> RSI 100 (all gains). Make that explicit
    # instead of relying on inf arithmetic.
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(~((avg_gain == 0.0) & (avg_loss == 0.0)), 50.0)
    return out


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


def atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """Average True Range with Wilder smoothing. NaN for the first `period` rows."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def daily_returns(close: pd.Series) -> pd.Series:
    return close.pct_change()


def annualized_volatility(close: pd.Series, window: int = 20) -> pd.Series:
    """Rolling annualized volatility (252 trading days)."""
    return daily_returns(close).rolling(window, min_periods=window).std() * np.sqrt(252)
