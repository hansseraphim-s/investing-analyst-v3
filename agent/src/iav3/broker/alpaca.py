"""Alpaca adapter (paper or live, selected by base URL / `paper` flag).

Uses a single SDK (`alpaca-py`). Entry+stop+target go out as ONE bracket
order so the position is never unprotected. Market-open is taken from
Alpaca's own clock, which already accounts for holidays and early closes —
fixing the v1 gap where only weekday/time was checked.

`alpaca-py` is imported lazily so backtests and the paper broker work with
no brokerage dependency installed.
"""

from __future__ import annotations

from .base import Account, BrokerPosition, OrderResult


class AlpacaBroker:
    def __init__(self, api_key: str, secret_key: str, *, paper: bool = True):
        if not api_key or not secret_key:
            raise ValueError("AlpacaBroker requires ALPACA_API_KEY and ALPACA_SECRET_KEY")
        from alpaca.trading.client import TradingClient

        self._client = TradingClient(api_key, secret_key, paper=paper)
        self._paper = paper

    def is_market_open(self) -> bool:
        return bool(self._client.get_clock().is_open)

    def get_account(self) -> Account:
        a = self._client.get_account()
        return Account(
            cash=float(a.cash),
            equity=float(a.equity),
            last_equity=float(a.last_equity),
        )

    def get_positions(self) -> list[BrokerPosition]:
        out = []
        for p in self._client.get_all_positions():
            out.append(
                BrokerPosition(
                    symbol=p.symbol,
                    qty=int(float(p.qty)),
                    avg_price=float(p.avg_entry_price),
                    current_price=float(p.current_price),
                )
            )
        return out

    def submit_bracket_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        entry_ref: float,
        stop_price: float,
        take_profit: float,
    ) -> OrderResult:
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import (
            MarketOrderRequest,
            StopLossRequest,
            TakeProfitRequest,
        )

        if side.upper() != "BUY":
            raise ValueError("Bracket entry is long-only in this build")
        if not (stop_price < entry_ref < take_profit):
            raise ValueError(
                f"Bracket levels invalid: stop {stop_price} < entry {entry_ref} "
                f"< target {take_profit} must hold"
            )

        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(take_profit, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
        )
        order = self._client.submit_order(req)
        return OrderResult(
            order_id=str(order.id),
            symbol=symbol,
            qty=qty,
            side="BUY",
            status=str(order.status),
        )

    def close_position(self, symbol: str) -> OrderResult:
        order = self._client.close_position(symbol)
        return OrderResult(
            order_id=str(getattr(order, "id", "")),
            symbol=symbol,
            qty=int(float(getattr(order, "qty", 0) or 0)),
            side="SELL",
            status=str(getattr(order, "status", "submitted")),
        )

    def cancel_all_orders(self) -> None:
        self._client.cancel_orders()
