"""Broker abstraction.

The engine only ever talks to this interface, so paper and live execution are
identical code paths. The key safety upgrade over v1: `submit_bracket_order`
places the entry, stop-loss, and take-profit as ONE atomic order. In v1 the
stop-loss was a separate Claude tool call after entry, leaving a window where
the position was unprotected (and a stop could be placed on an unfilled
order). A bracket order removes that window entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Account:
    cash: float
    equity: float
    last_equity: float

    @property
    def day_pnl(self) -> float:
        return self.equity - self.last_equity

    @property
    def day_pnl_pct(self) -> float:
        # Zero-guard: v1 crashed here on a fresh account (last_equity == 0).
        if self.last_equity <= 0:
            return 0.0
        return round((self.equity - self.last_equity) / self.last_equity * 100.0, 3)


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    qty: int
    avg_price: float
    current_price: float

    @property
    def market_value(self) -> float:
        return self.qty * self.current_price


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    symbol: str
    qty: int
    side: str
    status: str


class Broker(Protocol):
    def is_market_open(self) -> bool: ...

    def get_account(self) -> Account: ...

    def get_positions(self) -> list[BrokerPosition]: ...

    def submit_bracket_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        entry_ref: float,
        stop_price: float,
        take_profit: float,
    ) -> OrderResult:
        """Atomic entry + stop-loss + take-profit.

        `entry_ref` is the price the order was sized against. The paper broker
        fills at it; the Alpaca adapter uses a market entry and treats it as a
        sanity/logging reference.
        """
        ...

    def close_position(self, symbol: str) -> OrderResult: ...

    def cancel_all_orders(self) -> None: ...

    def submit_option_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        limit_price: float,
    ) -> OrderResult:
        """Single-leg option order at a limit price.

        `symbol` is the OCC-formatted contract identifier (e.g.
        AAPL250620C00200000). `side` is BUY for opening long calls/puts or
        closing short legs, SELL for opening short legs (cash-secured /
        covered only) or closing longs. Time-in-force is always DAY for
        options (Alpaca rejects GTC). No bracket — long-call max loss is
        bounded by premium paid; short-leg defined-risk safety is enforced
        upstream in WheelLive via cash/share coverage checks.
        """
        ...
