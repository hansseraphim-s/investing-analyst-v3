"""Sync Neon Postgres writer for the trade journal.

Replaces v2's local SQLite journal as the source of truth for the
dashboard. The agent writes here; the Next.js dashboard reads via
@neondatabase/serverless (JS, async, separate concern). Schema source
of truth: shared/schema.sql.

Why sync: the engine is sync, runs once per cycle, makes ~tens of
journal writes per cycle. There's no benefit to async; sync psycopg
with a connection pool is the simpler shape that integrates cleanly
into the existing engine.

Failure mode: ALL writes are wrapped by the engine in try/except so a
Neon outage degrades observability but never aborts a trading cycle.
The SQLite local journal (portfolio.py) remains the primary source for
risk-layer reads (trades_today) — Neon is for the dashboard.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)


def _serializable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if is_dataclass(value):
        return asdict(value)
    return value


class NeonJournal:
    """Sync writer for the journal tables. Construct once per process."""

    def __init__(self, dsn: str | None = None, *, min_size: int = 1, max_size: int = 4) -> None:
        dsn = dsn or os.environ.get("NEON_DATABASE_URL")
        if not dsn:
            raise RuntimeError(
                "NEON_DATABASE_URL is not set. Configure it in .env "
                "(see .env.example) or pass dsn= explicitly."
            )
        self._pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=False)
        self._opened = False

    def open(self) -> None:
        if not self._opened:
            self._pool.open()
            self._opened = True

    def close(self) -> None:
        if self._opened:
            self._pool.close()
            self._opened = False

    def __enter__(self) -> NeonJournal:
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @contextmanager
    def _conn(self):
        self.open()
        with self._pool.connection() as conn:
            yield conn

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                row = cur.fetchone()
                return bool(row and row[0] == 1)
        except (psycopg.OperationalError, OSError) as e:
            logger.error("Neon ping failed: %s", e)
            return False

    def schema_applied(self) -> bool:
        """True when shared/schema.sql has been applied (sessions table exists)."""
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'sessions')"
                )
                row = cur.fetchone()
                return bool(row and row[0])
        except psycopg.OperationalError as e:
            logger.error("schema_applied check failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def open_session(
        self,
        *,
        trading_mode: str,
        strategy: str,
        equity_start: float,
        agent_version: str,
        git_sha: str | None = None,
    ) -> int:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions (trading_mode, strategy, equity_start, agent_version, git_sha) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (trading_mode, strategy, equity_start, agent_version, git_sha),
            )
            row = cur.fetchone()
            return int(row[0])

    def close_session(
        self,
        session_id: int,
        *,
        equity_end: float,
        cash_end: float,
        day_pnl_pct: float,
        summary: str,
        advisor_review: str | None = None,
    ) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET ended_at = now(), equity_end = %s, cash_end = %s, "
                "day_pnl_pct = %s, summary = %s, advisor_review = %s WHERE id = %s",
                (equity_end, cash_end, day_pnl_pct, summary, advisor_review, session_id),
            )

    # ------------------------------------------------------------------
    # Orders + signals + equity + greeks
    # ------------------------------------------------------------------

    def record_order(
        self,
        *,
        session_id: int | None,
        symbol: str,
        asset_class: str,
        side: str,
        qty: int,
        price: float,
        status: str,
        reason: str,
        option_type: str | None = None,
        strike: float | None = None,
        expiry: date | None = None,
        stop_price: float | None = None,
        take_profit: float | None = None,
        broker_order_id: str | None = None,
        advisor_rationale: str | None = None,
    ) -> int:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO orders (session_id, symbol, asset_class, option_type, strike, expiry, "
                "side, qty, price, stop_price, take_profit, status, reason, broker_order_id, "
                "advisor_rationale) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (session_id, symbol, asset_class, option_type, strike, expiry, side, qty,
                 price, stop_price, take_profit, status, reason, broker_order_id, advisor_rationale),
            )
            row = cur.fetchone()
            return int(row[0])

    def record_signal(
        self,
        *,
        session_id: int,
        symbol: str,
        strategy: str,
        target: int,
        price: float,
        atr: float | None,
        realized_vol: float | None,
        iv_rank: float | None = None,
        target_weight: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        extra_json = json.dumps(extra, default=_serializable) if extra else None
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO signals (session_id, symbol, strategy, target, target_weight, "
                "price, atr, realized_vol, iv_rank, extra_features) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (session_id, symbol, strategy, target, target_weight, price, atr, realized_vol,
                 iv_rank, extra_json),
            )

    def record_equity_point(
        self,
        *,
        session_id: int,
        equity: float,
        cash: float,
        benchmark_value: float | None = None,
        drawdown_pct: float | None = None,
    ) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO equity_curve (session_id, equity, cash, benchmark_value, drawdown_pct) "
                "VALUES (%s, %s, %s, %s, %s)",
                (session_id, equity, cash, benchmark_value, drawdown_pct),
            )

    def record_position_snapshot(
        self,
        *,
        session_id: int,
        symbol: str,
        asset_class: str,
        qty: float,
        market_value: float,
        avg_entry: float | None = None,
        unrealized_pl: float | None = None,
        option_type: str | None = None,
        strike: float | None = None,
        expiry: date | None = None,
    ) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO positions (session_id, symbol, asset_class, qty, avg_entry, "
                "market_value, unrealized_pl, option_type, strike, expiry) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (session_id, symbol, asset_class, qty, avg_entry, market_value, unrealized_pl,
                 option_type, strike, expiry),
            )

    def record_greeks(
        self,
        *,
        session_id: int,
        delta: float,
        gamma: float,
        vega: float,
        theta: float,
        notional_exposure: float,
        cash_pct: float,
        rho: float | None = None,
    ) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO greeks_snapshot (session_id, portfolio_delta, portfolio_gamma, "
                "portfolio_vega, portfolio_theta, portfolio_rho, notional_exposure, cash_pct) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (session_id, delta, gamma, vega, theta, rho, notional_exposure, cash_pct),
            )

    def record_walk_forward(
        self,
        *,
        run_id: uuid.UUID,
        strategy: str,
        symbol: str,
        is_start: date,
        is_end: date,
        oos_start: date,
        oos_end: date,
        params: dict[str, Any],
        is_sharpe: float | None,
        oos_sharpe: float | None,
        oos_return_pct: float | None,
        oos_max_dd_pct: float | None,
        promoted: bool = False,
    ) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO walk_forward_runs (run_id, strategy, symbol, is_start, is_end, "
                "oos_start, oos_end, params, is_sharpe, oos_sharpe, oos_return_pct, oos_max_dd_pct, "
                "promoted) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (str(run_id), strategy, symbol, is_start, is_end, oos_start, oos_end,
                 json.dumps(params, default=_serializable), is_sharpe, oos_sharpe,
                 oos_return_pct, oos_max_dd_pct, promoted),
            )

    def record_kill_switch(
        self,
        *,
        session_id: int | None,
        trigger: str,
        detail: str,
        positions_at_trigger: list[dict[str, Any]] | None = None,
    ) -> None:
        positions_json = (
            json.dumps(positions_at_trigger, default=_serializable)
            if positions_at_trigger is not None else None
        )
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO kill_switch_events (session_id, trigger, detail, positions_at_trigger) "
                "VALUES (%s, %s, %s, %s)",
                (session_id, trigger, detail, positions_json),
            )
