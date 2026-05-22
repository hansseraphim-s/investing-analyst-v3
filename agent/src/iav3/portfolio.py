"""Lightweight SQLite journal: executed orders + cycle sessions.

The `trades_today` count uses an explicit UTC day boundary computed in
Python and passed as a bound parameter. This is the deliberate fix for the
v1 bug where timestamps were written in local time but filtered with SQLite's
UTC `datetime('now','-1 hour')`, silently breaking the frequency breaker.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

JOURNAL_DB = "trader_journal.db"


def init_journal(db_path: str = JOURNAL_DB) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS orders (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               ts_utc TEXT NOT NULL,
               symbol TEXT, side TEXT, qty INTEGER,
               entry REAL, stop REAL, target REAL,
               status TEXT, reason TEXT)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sessions (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               ts_utc TEXT NOT NULL,
               equity REAL, cash REAL, day_pnl_pct REAL, summary TEXT)"""
    )
    conn.commit()
    conn.close()


def record_order(
    symbol: str,
    side: str,
    qty: int,
    entry: float,
    stop: float,
    target: float,
    status: str,
    reason: str = "",
    db_path: str = JOURNAL_DB,
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO orders (ts_utc, symbol, side, qty, entry, stop, target, status, reason)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (
            datetime.now(timezone.utc).isoformat(),
            symbol, side, qty, entry, stop, target, status, reason,
        ),
    )
    conn.commit()
    conn.close()


def trades_today(db_path: str = JOURNAL_DB) -> int:
    """Count filled orders since 00:00 UTC today (timezone-correct)."""
    start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    conn = sqlite3.connect(db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE ts_utc >= ? AND status = 'filled'",
        (start.isoformat(),),
    ).fetchone()[0]
    conn.close()
    return int(n)


def record_session(
    equity: float,
    cash: float,
    day_pnl_pct: float,
    summary: str,
    db_path: str = JOURNAL_DB,
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sessions (ts_utc, equity, cash, day_pnl_pct, summary) VALUES (?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), equity, cash, day_pnl_pct, summary),
    )
    conn.commit()
    conn.close()


def recent_sessions(limit: int = 10, db_path: str = JOURNAL_DB) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM sessions ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
