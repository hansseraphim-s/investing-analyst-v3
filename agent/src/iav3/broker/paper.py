"""In-process simulated broker.

Lets the live-style engine run end-to-end with NO brokerage account and NO
API keys. Entry fills immediately at the supplied reference price; the bracket
stop/target are stored and evaluated whenever positions are marked to market
via `mark()`. State persists to SQLite so a restarted session resumes.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pandas_market_calendars as mcal

from .base import Account, BrokerPosition, OrderResult

_NYSE = mcal.get_calendar("XNYS")


def _market_open_now() -> bool:
    now = datetime.now(timezone.utc)
    sched = _NYSE.schedule(
        start_date=now.date().isoformat(), end_date=now.date().isoformat()
    )
    if sched.empty:  # weekend or market holiday
        return False
    open_, close_ = sched.iloc[0]["market_open"], sched.iloc[0]["market_close"]
    return open_ <= now <= close_


class PaperBroker:
    def __init__(self, starting_cash: float = 100_000.0, db_path: str = "paper_state.db"):
        self._db = db_path
        self._init_db(starting_cash)

    # --- persistence ---------------------------------------------------------
    def _init_db(self, starting_cash: float) -> None:
        conn = sqlite3.connect(self._db)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS positions (
                   symbol TEXT PRIMARY KEY, qty INTEGER, avg_price REAL,
                   stop REAL, take REAL)"""
        )
        cur = conn.execute("SELECT v FROM meta WHERE k='cash'")
        if cur.fetchone() is None:
            conn.execute("INSERT INTO meta VALUES ('cash', ?)", (str(starting_cash),))
            conn.execute(
                "INSERT INTO meta VALUES ('start_equity', ?)", (str(starting_cash),)
            )
        conn.commit()
        conn.close()

    def _get_cash(self) -> float:
        conn = sqlite3.connect(self._db)
        v = conn.execute("SELECT v FROM meta WHERE k='cash'").fetchone()[0]
        conn.close()
        return float(v)

    def _set_cash(self, cash: float) -> None:
        conn = sqlite3.connect(self._db)
        conn.execute("UPDATE meta SET v=? WHERE k='cash'", (str(cash),))
        conn.commit()
        conn.close()

    def _raw_positions(self) -> list[tuple]:
        conn = sqlite3.connect(self._db)
        rows = conn.execute(
            "SELECT symbol, qty, avg_price, stop, take FROM positions"
        ).fetchall()
        conn.close()
        return rows

    # --- Broker protocol -----------------------------------------------------
    def is_market_open(self) -> bool:
        return _market_open_now()

    def get_account(self) -> Account:
        conn = sqlite3.connect(self._db)
        start_equity = float(
            conn.execute("SELECT v FROM meta WHERE k='start_equity'").fetchone()[0]
        )
        conn.close()
        equity = self._get_cash() + sum(
            q * ap for _, q, ap, _, _ in self._raw_positions()
        )
        return Account(cash=self._get_cash(), equity=equity, last_equity=start_equity)

    def get_positions(self) -> list[BrokerPosition]:
        return [
            BrokerPosition(symbol=s, qty=q, avg_price=ap, current_price=ap)
            for s, q, ap, _, _ in self._raw_positions()
        ]

    def submit_bracket_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        entry_ref: float,
        stop_price: float,
        take_profit: float,
    ) -> OrderResult:
        if side.upper() != "BUY":
            raise ValueError("PaperBroker bracket orders are long-entry only")
        entry = entry_ref
        cost = qty * entry
        cash = self._get_cash()
        if cost > cash:
            raise ValueError("Paper: insufficient cash for order")
        conn = sqlite3.connect(self._db)
        conn.execute(
            "INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?)",
            (symbol.upper(), qty, entry, stop_price, take_profit),
        )
        conn.commit()
        conn.close()
        self._set_cash(cash - cost)
        return OrderResult(
            order_id=f"paper-{symbol}-{datetime.now().timestamp():.0f}",
            symbol=symbol.upper(),
            qty=qty,
            side="BUY",
            status="filled",
        )

    def close_position(self, symbol: str) -> OrderResult:
        rows = {r[0]: r for r in self._raw_positions()}
        r = rows.get(symbol.upper())
        if not r:
            raise ValueError(f"No paper position in {symbol}")
        _, qty, ap, _, _ = r
        self._set_cash(self._get_cash() + qty * ap)
        conn = sqlite3.connect(self._db)
        conn.execute("DELETE FROM positions WHERE symbol=?", (symbol.upper(),))
        conn.commit()
        conn.close()
        return OrderResult(f"paper-close-{symbol}", symbol.upper(), qty, "SELL", "filled")

    def cancel_all_orders(self) -> None:
        # No resting orders in the paper model (fills are immediate).
        return None

    def submit_option_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        limit_price: float,
    ) -> OrderResult:
        # The in-process simulator doesn't model the options chain — for
        # options-aware paper trading, switch to AlpacaBroker pointed at the
        # Alpaca paper endpoint (the default when keys are set in .env).
        raise NotImplementedError(
            "PaperBroker (in-process simulator) does not support option orders. "
            "Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env to use AlpacaBroker "
            "against the paper endpoint, which handles options."
        )

    def mark(self, prices: dict[str, float]) -> list[str]:
        """Apply current prices; auto-close any position whose bracket triggered.

        Returns the list of symbols that were closed this mark.
        """
        closed: list[str] = []
        for sym, qty, _ap, stop, take in self._raw_positions():
            px = prices.get(sym)
            if px is None:
                continue
            if px <= stop or px >= take:
                self._set_cash(self._get_cash() + qty * px)
                conn = sqlite3.connect(self._db)
                conn.execute("DELETE FROM positions WHERE symbol=?", (sym,))
                conn.commit()
                conn.close()
                closed.append(sym)
        return closed
