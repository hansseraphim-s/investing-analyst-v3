"""Async Neon Postgres writer for the trade journal.

One connection pool per process. Writes are fire-and-forget from the
engine's perspective (failures are logged but do not abort a cycle —
the journal is observability, not a circuit breaker).

Read side is the Next.js dashboard, which connects via its own pool.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg_pool import AsyncConnectionPool

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
    """Async writer for the journal tables. Construct once per process."""

    def __init__(self, dsn: str | None = None, *, min_size: int = 1, max_size: int = 4) -> None:
        dsn = dsn or os.environ.get("NEON_DATABASE_URL")
        if not dsn:
            raise RuntimeError(
                "NEON_DATABASE_URL is not set. Configure it in .env "
                "(see .env.example) or pass dsn= explicitly."
            )
        self._pool = AsyncConnectionPool(dsn, min_size=min_size, max_size=max_size, open=False)
        self._opened = False

    async def open(self) -> None:
        if not self._opened:
            await self._pool.open()
            self._opened = True

    async def close(self) -> None:
        if self._opened:
            await self._pool.close()
            self._opened = False

    @asynccontextmanager
    async def _conn(self):
        await self.open()
        async with self._pool.connection() as conn:
            yield conn

    async def open_session(
        self,
        *,
        trading_mode: str,
        strategy: str,
        equity_start: float,
        agent_version: str,
        git_sha: str | None = None,
    ) -> int:
        sql = """
            INSERT INTO sessions (trading_mode, strategy, equity_start, agent_version, git_sha)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """
        async with self._conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (trading_mode, strategy, equity_start, agent_version, git_sha))
                row = await cur.fetchone()
                return int(row[0])

    async def close_session(
        self,
        session_id: int,
        *,
        equity_end: float,
        cash_end: float,
        day_pnl_pct: float,
        summary: str,
        advisor_review: str | None = None,
    ) -> None:
        sql = """
            UPDATE sessions
            SET ended_at = now(),
                equity_end = %s,
                cash_end = %s,
                day_pnl_pct = %s,
                summary = %s,
                advisor_review = %s
            WHERE id = %s
        """
        async with self._conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (equity_end, cash_end, day_pnl_pct, summary, advisor_review, session_id))

    async def record_order(
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
        sql = """
            INSERT INTO orders
                (session_id, symbol, asset_class, option_type, strike, expiry,
                 side, qty, price, stop_price, take_profit, status, reason,
                 broker_order_id, advisor_rationale)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """
        params = (session_id, symbol, asset_class, option_type, strike, expiry,
                  side, qty, price, stop_price, take_profit, status, reason,
                  broker_order_id, advisor_rationale)
        async with self._conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
                return int(row[0])

    async def record_signal(
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
        sql = """
            INSERT INTO signals
                (session_id, symbol, strategy, target, target_weight, price,
                 atr, realized_vol, iv_rank, extra_features)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        extra_json = json.dumps(extra, default=_serializable) if extra else None
        async with self._conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (session_id, symbol, strategy, target, target_weight,
                                        price, atr, realized_vol, iv_rank, extra_json))

    async def record_equity_point(
        self,
        *,
        session_id: int,
        equity: float,
        cash: float,
        benchmark_value: float | None = None,
        drawdown_pct: float | None = None,
    ) -> None:
        sql = """
            INSERT INTO equity_curve (session_id, equity, cash, benchmark_value, drawdown_pct)
            VALUES (%s, %s, %s, %s, %s)
        """
        async with self._conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (session_id, equity, cash, benchmark_value, drawdown_pct))

    async def record_greeks(
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
        sql = """
            INSERT INTO greeks_snapshot
                (session_id, portfolio_delta, portfolio_gamma, portfolio_vega,
                 portfolio_theta, portfolio_rho, notional_exposure, cash_pct)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """
        async with self._conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (session_id, delta, gamma, vega, theta, rho,
                                        notional_exposure, cash_pct))

    async def record_walk_forward(
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
        sql = """
            INSERT INTO walk_forward_runs
                (run_id, strategy, symbol, is_start, is_end, oos_start, oos_end,
                 params, is_sharpe, oos_sharpe, oos_return_pct, oos_max_dd_pct, promoted)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        async with self._conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (str(run_id), strategy, symbol, is_start, is_end,
                                        oos_start, oos_end, json.dumps(params, default=_serializable),
                                        is_sharpe, oos_sharpe, oos_return_pct, oos_max_dd_pct, promoted))

    async def record_kill_switch(
        self,
        *,
        session_id: int | None,
        trigger: str,
        detail: str,
        positions_at_trigger: list[dict[str, Any]] | None = None,
    ) -> None:
        sql = """
            INSERT INTO kill_switch_events
                (session_id, trigger, detail, positions_at_trigger)
            VALUES (%s, %s, %s, %s)
        """
        positions_json = (
            json.dumps(positions_at_trigger, default=_serializable)
            if positions_at_trigger is not None else None
        )
        async with self._conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (session_id, trigger, detail, positions_json))

    async def ping(self) -> bool:
        try:
            async with self._conn() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
                    row = await cur.fetchone()
                    return bool(row and row[0] == 1)
        except (psycopg.OperationalError, OSError) as e:
            logger.error("Neon ping failed: %s", e)
            return False
