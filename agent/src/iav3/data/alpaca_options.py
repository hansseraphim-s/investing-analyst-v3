"""Alpaca options market data adapter.

Real option chains, NBBO quotes, market-implied vol, and Greeks via the
Alpaca Markets Data API (free tier, included with paper or live broker
accounts). Replaces v2's Black-Scholes-only model with actual quoted
options for backtests and live trading.

Account level note: options data is gated by the broker account's options
trading approval. Level 1+ exposes chains. Level 2+ exposes Greeks.
This adapter assumes level 2+ — which the project's go-live checklist
requires anyway. Calls degrade gracefully (return None for Greeks) when
they're absent rather than raising.

References:
    https://docs.alpaca.markets/docs/options-trading
    https://docs.alpaca.markets/reference/optionchain
    https://github.com/alpacahq/alpaca-py
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest, OptionLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.trading.requests import GetOptionContractsRequest

logger = logging.getLogger(__name__)

OptionType = Literal["call", "put"]

# OCC option symbol: e.g. AAPL250620C00200000
#   AAPL    underlying
#   250620  expiry yymmdd
#   C       C(all) or P(ut)
#   00200000 strike * 1000, 8-digit zero-padded
_OCC = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


@dataclass(frozen=True)
class OptionContract:
    symbol: str           # OCC-formatted
    underlying: str
    option_type: OptionType
    strike: float
    expiry: date


@dataclass(frozen=True)
class OptionQuote:
    contract: OptionContract
    bid: float
    ask: float
    last: float
    mid: float
    iv: float | None              # market-implied vol from the chain
    delta: float | None
    gamma: float | None
    vega: float | None
    theta: float | None
    rho: float | None
    open_interest: int
    volume: int
    quoted_at: datetime

    @property
    def spread_pct(self) -> float:
        if self.mid <= 0:
            return float("inf")
        return (self.ask - self.bid) / self.mid


def parse_occ(symbol: str) -> OptionContract:
    """Parse an OCC-style option symbol into its parts.

    Raises ValueError if the symbol does not match OCC format. Used to
    normalize chain responses, which return option symbols as keys.
    """
    m = _OCC.match(symbol)
    if not m:
        raise ValueError(f"Not an OCC option symbol: {symbol!r}")
    underlying, yymmdd, cp, strike_milli = m.groups()
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    # Two-digit year: assume 2000+yy. OCC will wrap to 2100 in 75 years.
    expiry = date(2000 + yy, mm, dd)
    option_type: OptionType = "call" if cp == "C" else "put"
    strike = int(strike_milli) / 1000.0
    return OptionContract(
        symbol=symbol,
        underlying=underlying,
        option_type=option_type,
        strike=strike,
        expiry=expiry,
    )


class AlpacaOptionsFeed:
    """Live + on-the-fly historical option-chain access via Alpaca.

    Construct once per process. The underlying alpaca-py clients manage
    their own connection pools; this wrapper adds the OCC parser, the
    OptionQuote normalization, and the convenience methods the strategy
    layer expects (nearest_expiry, atm_quote, iv_rank).
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        paper: bool = True,
    ) -> None:
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY")
        self.api_secret = api_secret or os.environ.get("ALPACA_API_SECRET")
        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "Alpaca credentials missing. Set ALPACA_API_KEY and "
                "ALPACA_API_SECRET in .env (see .env.example)."
            )
        self.paper = paper
        self._data = OptionHistoricalDataClient(self.api_key, self.api_secret)
        self._trading = TradingClient(self.api_key, self.api_secret, paper=paper)

    # ------------------------------------------------------------------
    # Chain access
    # ------------------------------------------------------------------

    def chain(
        self,
        underlying: str,
        *,
        expiry: date | None = None,
        option_type: OptionType | None = None,
        min_strike: float | None = None,
        max_strike: float | None = None,
    ) -> list[OptionQuote]:
        """Current chain for an underlying, optionally filtered.

        Returns a list of OptionQuote, one per contract that has at least
        a quote or a trade. Contracts with no market activity in the
        session are excluded (Alpaca returns None for their snapshot).
        """
        req = OptionChainRequest(
            underlying_symbol=underlying.upper(),
            expiration_date=expiry,
            type=_to_contract_type(option_type),
            strike_price_gte=min_strike,
            strike_price_lte=max_strike,
        )
        snapshots = self._data.get_option_chain(req)
        return [
            _build_quote(sym, snap)
            for sym, snap in snapshots.items()
            if snap is not None
        ]

    def latest_quote(self, contract_symbol: str) -> OptionQuote | None:
        """Latest NBBO + Greeks for a single OCC-formatted contract."""
        try:
            req = OptionLatestQuoteRequest(symbol_or_symbols=contract_symbol)
            quotes = self._data.get_option_latest_quote(req)
            quote = quotes.get(contract_symbol)
            if quote is None:
                return None
            # latest_quote alone lacks Greeks/IV; fetch via chain when we need them
            return OptionQuote(
                contract=parse_occ(contract_symbol),
                bid=float(getattr(quote, "bid_price", 0.0) or 0.0),
                ask=float(getattr(quote, "ask_price", 0.0) or 0.0),
                last=0.0,
                mid=_mid(getattr(quote, "bid_price", 0.0), getattr(quote, "ask_price", 0.0)),
                iv=None,
                delta=None,
                gamma=None,
                vega=None,
                theta=None,
                rho=None,
                open_interest=0,
                volume=0,
                quoted_at=_to_dt(getattr(quote, "timestamp", None)),
            )
        except Exception as e:
            logger.warning("latest_quote(%s) failed: %s", contract_symbol, e)
            return None

    # ------------------------------------------------------------------
    # Expiry + ATM helpers (used by strategy layer)
    # ------------------------------------------------------------------

    def available_expiries(self, underlying: str) -> list[date]:
        """All listed expiries for an underlying, sorted ascending."""
        req = GetOptionContractsRequest(
            underlying_symbols=[underlying.upper()],
            status=AssetStatus.ACTIVE,
        )
        page = self._trading.get_option_contracts(req)
        # alpaca-py paginates; for the call we make (active contracts of a
        # single underlying) one page is typically enough, but we walk anyway
        seen: set[date] = set()
        for c in _iter_contracts(page):
            if c.expiration_date is not None:
                seen.add(c.expiration_date)
        return sorted(seen)

    def nearest_expiry(
        self,
        underlying: str,
        target_dte: int,
        *,
        as_of: date | None = None,
    ) -> date:
        """Pick the listed expiry closest to `target_dte` calendar days out.

        Raises LookupError if the underlying has no listed options.
        """
        ref = as_of or datetime.now(timezone.utc).date()
        target = ref + timedelta(days=target_dte)
        expiries = self.available_expiries(underlying)
        if not expiries:
            raise LookupError(f"No listed option expiries for {underlying}")
        # Pick the expiry minimizing absolute distance to the target date
        return min(expiries, key=lambda e: abs((e - target).days))

    def atm_quote(
        self,
        underlying: str,
        spot: float,
        *,
        target_dte: int = 30,
        option_type: OptionType = "call",
    ) -> OptionQuote | None:
        """Get the chain entry whose strike is closest to spot at ~target_dte.

        Used for: (a) IV-rank-style measurements (b) the long-call overlay
        strike selection (c) the earnings-event wheel target picker.
        """
        try:
            expiry = self.nearest_expiry(underlying, target_dte)
        except LookupError:
            return None
        # Fetch a band around spot to keep the response small
        band = max(spot * 0.20, 25.0)
        chain = self.chain(
            underlying,
            expiry=expiry,
            option_type=option_type,
            min_strike=spot - band,
            max_strike=spot + band,
        )
        if not chain:
            return None
        return min(chain, key=lambda q: abs(q.contract.strike - spot))

    # ------------------------------------------------------------------
    # IV rank (with explicit "insufficient history" fall-through)
    # ------------------------------------------------------------------

    def current_atm_iv(
        self,
        underlying: str,
        spot: float,
        *,
        target_dte: int = 30,
    ) -> float | None:
        """Today's ATM ~30-DTE call IV. None when the chain has no IV.

        IV rank is built on a rolling history of THIS number — the agent
        appends a row each cycle in `~/.iav3/iv_history/{SYMBOL}.csv`.
        """
        q = self.atm_quote(underlying, spot, target_dte=target_dte, option_type="call")
        return q.iv if q is not None else None

    def iv_rank(
        self,
        underlying: str,
        *,
        lookback_days: int = 252,
        history_dir: str | None = None,
    ) -> float | None:
        """IV rank in [0, 100] over the lookback window, or None.

        Returns None when fewer than 60 history observations exist —
        callers MUST treat None as "do not gate on IV rank yet". This is
        intentional: bootstrapping a rank from <60 observations would give
        a misleading number that strategies might key off.

        Where history comes from: ~/.iav3/iv_history/{SYMBOL}.csv, populated
        by the agent each cycle via `append_iv_observation`.
        """
        import csv
        from pathlib import Path

        directory = Path(history_dir or os.path.expanduser("~/.iav3/iv_history"))
        path = directory / f"{underlying.upper()}.csv"
        if not path.exists():
            return None

        rows: list[tuple[date, float]] = []
        with path.open() as f:
            reader = csv.reader(f)
            for row in reader:
                if not row or row[0].startswith("#"):
                    continue
                try:
                    d = date.fromisoformat(row[0])
                    iv = float(row[1])
                except (ValueError, IndexError):
                    continue
                rows.append((d, iv))

        cutoff = datetime.now(timezone.utc).date() - timedelta(days=lookback_days)
        window = [iv for d, iv in rows if d >= cutoff]
        if len(window) < 60:
            return None

        current = window[-1]
        lo, hi = min(window), max(window)
        if hi == lo:
            return 50.0
        return 100.0 * (current - lo) / (hi - lo)

    def append_iv_observation(
        self,
        underlying: str,
        spot: float,
        *,
        target_dte: int = 30,
        history_dir: str | None = None,
        as_of: date | None = None,
    ) -> float | None:
        """Append today's ATM IV to the rolling history file. Idempotent per day.

        Returns the observed IV (or None if the chain had no IV). The
        engine calls this each paper/live cycle so iv_rank accumulates
        from day one.
        """
        import csv
        from pathlib import Path

        iv = self.current_atm_iv(underlying, spot, target_dte=target_dte)
        if iv is None:
            return None

        directory = Path(history_dir or os.path.expanduser("~/.iav3/iv_history"))
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{underlying.upper()}.csv"
        today = (as_of or datetime.now(timezone.utc).date()).isoformat()

        # Idempotency: skip if today is already recorded
        if path.exists():
            with path.open() as f:
                for row in csv.reader(f):
                    if row and row[0] == today:
                        return iv

        with path.open("a") as f:
            f.write(f"{today},{iv:.6f}\n")
        return iv


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _to_contract_type(t: OptionType | None) -> ContractType | None:
    if t is None:
        return None
    return ContractType.CALL if t == "call" else ContractType.PUT


def _mid(bid: float | None, ask: float | None) -> float:
    b = float(bid or 0.0)
    a = float(ask or 0.0)
    if b > 0 and a > 0:
        return (b + a) / 2.0
    return a or b or 0.0


def _to_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _iter_contracts(page) -> Iterable:
    """alpaca-py returns either a list or a paged response object. Handle both."""
    contracts = getattr(page, "option_contracts", None)
    if contracts is not None:
        yield from contracts
    elif isinstance(page, list):
        yield from page


def _build_quote(symbol: str, snap) -> OptionQuote:
    contract = parse_occ(symbol)
    lq = getattr(snap, "latest_quote", None)
    lt = getattr(snap, "latest_trade", None)
    greeks = getattr(snap, "greeks", None)

    bid = float(getattr(lq, "bid_price", 0.0) or 0.0) if lq else 0.0
    ask = float(getattr(lq, "ask_price", 0.0) or 0.0) if lq else 0.0
    last = float(getattr(lt, "price", 0.0) or 0.0) if lt else 0.0

    return OptionQuote(
        contract=contract,
        bid=bid,
        ask=ask,
        last=last,
        mid=_mid(bid, ask) if (bid or ask) else last,
        iv=_safe_float(getattr(snap, "implied_volatility", None)),
        delta=_safe_float(getattr(greeks, "delta", None)) if greeks else None,
        gamma=_safe_float(getattr(greeks, "gamma", None)) if greeks else None,
        vega=_safe_float(getattr(greeks, "vega", None)) if greeks else None,
        theta=_safe_float(getattr(greeks, "theta", None)) if greeks else None,
        rho=_safe_float(getattr(greeks, "rho", None)) if greeks else None,
        open_interest=int(getattr(snap, "open_interest", 0) or 0),
        volume=int(getattr(lt, "size", 0) or 0) if lt else 0,
        quoted_at=_to_dt(getattr(lq, "timestamp", None) if lq else None),
    )


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f == f else None  # NaN guard
    except (TypeError, ValueError):
        return None
