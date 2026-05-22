"""Finnhub adapter — earnings calendar, news, sentiment.

Free tier: 60 calls/min. Used for the earnings-event wheel strategy
(detect upcoming earnings -> sell short-dated CSPs into elevated IV ->
let post-earnings IV crush capture the premium decay).

Thin wrapper around the official finnhub-python SDK. Returns typed
dataclasses instead of raw JSON dicts so the strategy layer has stable
field names and IDE-friendly access.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import finnhub

logger = logging.getLogger(__name__)

# Finnhub's `hour` codes for earnings announcement time
EarningsHour = Literal["bmo", "amc", "dmh", ""]  # before-mkt-open / after-mkt-close / during / unknown


@dataclass(frozen=True)
class EarningsEvent:
    symbol: str
    date: date
    hour: EarningsHour
    eps_estimate: float | None
    revenue_estimate: float | None


@dataclass(frozen=True)
class Quote:
    symbol: str
    current: float
    change: float
    percent_change: float
    high: float
    low: float
    open: float
    previous_close: float
    quoted_at: datetime


class FinnhubFeed:
    """Thin typed wrapper around finnhub-python."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("FINNHUB_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "FINNHUB_API_KEY not set. Configure it in .env (see .env.example)."
            )
        self._client = finnhub.Client(api_key=self.api_key)

    def earnings_calendar(
        self,
        symbol: str,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[EarningsEvent]:
        """Earnings events for a symbol in a date window (default: next 90 days).

        Returns [] on API error so callers don't have to wrap. The
        earnings-event wheel logs and skips, treating "no earnings" the
        same as "API failed" — both block opening a new pre-earnings CSP.
        """
        f = from_date or date.today()
        t = to_date or (f + timedelta(days=90))
        try:
            response = self._client.earnings_calendar(
                _from=f.isoformat(), to=t.isoformat(), symbol=symbol.upper()
            )
        except Exception as e:
            logger.warning("Finnhub earnings_calendar(%s) failed: %s", symbol, e)
            return []
        items = response.get("earningsCalendar", []) if isinstance(response, dict) else []
        events: list[EarningsEvent] = []
        for item in items or []:
            try:
                events.append(EarningsEvent(
                    symbol=str(item["symbol"]).upper(),
                    date=date.fromisoformat(item["date"]),
                    hour=str(item.get("hour", "") or ""),  # type: ignore[arg-type]
                    eps_estimate=_safe_float(item.get("epsEstimate")),
                    revenue_estimate=_safe_float(item.get("revenueEstimate")),
                ))
            except (KeyError, ValueError, TypeError) as e:
                logger.debug("skipping malformed earnings row: %s (%s)", item, e)
                continue
        return sorted(events, key=lambda e: e.date)

    def next_earnings(self, symbol: str, *, as_of: date | None = None) -> EarningsEvent | None:
        ref = as_of or date.today()
        events = self.earnings_calendar(symbol, from_date=ref)
        future = [e for e in events if e.date >= ref]
        return min(future, key=lambda e: e.date) if future else None

    def quote(self, symbol: str) -> Quote | None:
        try:
            q = self._client.quote(symbol.upper())
        except Exception as e:
            logger.warning("Finnhub quote(%s) failed: %s", symbol, e)
            return None
        if not isinstance(q, dict) or "c" not in q:
            return None
        try:
            return Quote(
                symbol=symbol.upper(),
                current=float(q["c"]),
                change=float(q.get("d") or 0.0),
                percent_change=float(q.get("dp") or 0.0),
                high=float(q.get("h") or 0.0),
                low=float(q.get("l") or 0.0),
                open=float(q.get("o") or 0.0),
                previous_close=float(q.get("pc") or 0.0),
                quoted_at=datetime.fromtimestamp(int(q.get("t") or 0), tz=timezone.utc),
            )
        except (TypeError, ValueError):
            return None


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None
