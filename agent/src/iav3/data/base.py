"""Market-data abstraction.

A `MarketData` source returns OHLCV bars as a DataFrame indexed by a
timezone-naive (UTC date) DatetimeIndex with columns:
    open, high, low, close, volume

Keeping this behind a Protocol means the backtester, paper engine, and live
engine all consume the same shape — and the data provider can be swapped
without touching strategy code.
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


class MarketData(Protocol):
    def history(
        self, symbol: str, start: str, end: str | None = None, interval: str = "1d"
    ) -> pd.DataFrame:
        """Historical OHLCV bars for `symbol` in [start, end]."""
        ...

    def latest_price(self, symbol: str) -> float:
        """Most recent trade/close price for `symbol`."""
        ...


def validate_ohlcv(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Normalize/validate a bars frame. Raises on unusable data."""
    if df is None or df.empty:
        raise ValueError(f"No market data returned for {symbol!r}")
    df = df.rename(columns=str.lower)
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{symbol}: missing OHLCV columns {missing}")
    df = df[OHLCV_COLUMNS].copy()
    df = df.dropna(subset=["close"])
    if df.empty:
        raise ValueError(f"{symbol}: all rows had NaN close")
    if not df.index.is_monotonic_increasing:
        df = df.sort_index()
    return df
