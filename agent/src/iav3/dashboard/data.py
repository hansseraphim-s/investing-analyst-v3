"""Pure data-prep helpers for the dashboard.

Kept separate from the Streamlit view so the logic is unit-testable (the
Streamlit app itself is a thin rendering layer over these functions). Every
function tolerates a missing/empty journal and never raises on absence of
data — a brand-new install with zero sessions must still render.
"""

from __future__ import annotations

import os
import sqlite3

import pandas as pd

from ..backtest.metrics import compute_metrics
from ..portfolio import JOURNAL_DB


def _connect(db_path: str) -> sqlite3.Connection | None:
    return sqlite3.connect(db_path) if os.path.exists(db_path) else None


def load_session_equity(db_path: str = JOURNAL_DB) -> pd.Series:
    """Equity curve of the live/paper agent over time (from session journal)."""
    conn = _connect(db_path)
    if conn is None:
        return pd.Series(dtype=float)
    try:
        df = pd.read_sql(
            "SELECT ts_utc, equity FROM sessions ORDER BY ts_utc", conn
        )
    except Exception:
        return pd.Series(dtype=float)
    finally:
        conn.close()
    if df.empty:
        return pd.Series(dtype=float)
    s = pd.Series(
        df["equity"].astype(float).to_numpy(),
        index=pd.to_datetime(df["ts_utc"]),
        name="equity",
    )
    return s[~s.index.duplicated(keep="last")]


def load_orders(db_path: str = JOURNAL_DB) -> pd.DataFrame:
    conn = _connect(db_path)
    if conn is None:
        return pd.DataFrame()
    try:
        df = pd.read_sql(
            "SELECT ts_utc, symbol, side, qty, entry, stop, target, status, reason "
            "FROM orders ORDER BY ts_utc DESC",
            conn,
        )
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()
    return df


def drawdown_series(equity: pd.Series) -> pd.Series:
    """Underwater curve: equity / running-peak - 1 (<= 0)."""
    if equity.empty:
        return pd.Series(dtype=float)
    return equity / equity.cummax() - 1.0


def align_benchmark(equity: pd.Series, bench_close: pd.Series) -> pd.DataFrame:
    """Normalize a benchmark close series to the agent's starting equity.

    The benchmark is reindexed onto the agent's timestamps (as-of/forward
    fill) so 'success' is always shown *relative to just holding the index*,
    not in isolation.
    """
    out = pd.DataFrame({"strategy": equity})
    if equity.empty or bench_close is None or bench_close.empty:
        out["benchmark"] = pd.NA
        return out
    bench = bench_close.copy()
    bench.index = pd.to_datetime(bench.index)
    if getattr(bench.index, "tz", None) is not None:
        bench.index = bench.index.tz_localize(None)
    eq_idx = equity.index.tz_localize(None) if getattr(
        equity.index, "tz", None
    ) is not None else equity.index
    aligned = bench.reindex(
        bench.index.union(eq_idx)
    ).sort_index().ffill().reindex(eq_idx)
    base = aligned.iloc[0]
    out["benchmark"] = (
        (aligned.to_numpy() / base) * float(equity.iloc[0]) if base else pd.NA
    )
    return out


def kpi_summary(equity: pd.Series, benchmark: pd.Series | None = None) -> dict:
    """Headline metrics. Honest by construction: max drawdown and a
    benchmark delta are always included, never just the return."""
    if len(equity) < 2:
        return {
            "status": "insufficient_history",
            "points": int(len(equity)),
            "note": "Run the agent (iav3 paper) to accumulate history, "
            "or use the Backtest tab.",
        }
    m = compute_metrics(equity, [], exposure_fraction=1.0)
    out = {
        "status": "ok",
        "total_return_pct": m.total_return_pct,
        "cagr_pct": m.cagr_pct,
        "sharpe": m.sharpe,
        "sortino": m.sortino,
        "max_drawdown_pct": m.max_drawdown_pct,
        "final_equity": m.final_equity,
        "start": m.start,
        "end": m.end,
    }
    if benchmark is not None and len(benchmark.dropna()) >= 2:
        b = benchmark.dropna()
        bench_ret = float(b.iloc[-1] / b.iloc[0] - 1.0) * 100.0
        out["benchmark_return_pct"] = round(bench_ret, 2)
        out["vs_benchmark_pct"] = round(m.total_return_pct - bench_ret, 2)
    return out
