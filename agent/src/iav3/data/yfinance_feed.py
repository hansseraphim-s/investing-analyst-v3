"""yfinance-backed market data (free, no API key).

Used for backtests and as the default quote source for paper trading.
Bars are cached in-process per (symbol, start, end, interval) to avoid
hammering the API during multi-symbol backtests.
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf

from .base import validate_ohlcv


class YFinanceData:
    def __init__(self) -> None:
        self._cache: dict[tuple, pd.DataFrame] = {}

    def history(
        self, symbol: str, start: str, end: str | None = None, interval: str = "1d"
    ) -> pd.DataFrame:
        key = (symbol.upper(), start, end, interval)
        if key in self._cache:
            return self._cache[key]
        raw = yf.Ticker(symbol).history(
            start=start, end=end, interval=interval, auto_adjust=True
        )
        if raw.empty:
            raise ValueError(
                f"yfinance returned no rows for {symbol!r} "
                f"(start={start}, end={end}, interval={interval})"
            )
        # Drop tz so the index is comparable across symbols/sources.
        if isinstance(raw.index, pd.DatetimeIndex) and raw.index.tz is not None:
            raw.index = raw.index.tz_localize(None)
        df = validate_ohlcv(raw, symbol)
        self._cache[key] = df
        return df

    def latest_price(self, symbol: str) -> float:
        t = yf.Ticker(symbol)
        try:
            price = t.fast_info["last_price"]
            if price:
                return float(price)
        except Exception:
            pass
        hist = t.history(period="1d")
        if hist.empty:
            raise ValueError(f"Could not fetch a latest price for {symbol!r}")
        return float(hist["Close"].iloc[-1])
