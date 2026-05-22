import numpy as np
import pandas as pd

from iav3.dashboard.data import (
    align_benchmark,
    drawdown_series,
    kpi_summary,
    load_orders,
    load_session_equity,
)
from iav3.portfolio import init_journal, record_order, record_session


def test_missing_journal_returns_empty_not_error(tmp_path):
    missing = str(tmp_path / "nope.db")
    assert load_session_equity(missing).empty
    assert load_orders(missing).empty


def test_session_equity_roundtrip(tmp_path):
    db = str(tmp_path / "j.db")
    init_journal(db)
    record_session(100_000, 50_000, 0.0, "s1", db_path=db)
    record_session(101_500, 49_000, 1.5, "s2", db_path=db)
    eq = load_session_equity(db)
    assert len(eq) == 2
    assert eq.iloc[0] == 100_000 and eq.iloc[-1] == 101_500
    assert isinstance(eq.index, pd.DatetimeIndex)


def test_orders_roundtrip(tmp_path):
    db = str(tmp_path / "j.db")
    init_journal(db)
    record_order("AAPL", "BUY", 10, 100.0, 90.0, 120.0, "filled", "entry", db_path=db)
    df = load_orders(db)
    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "AAPL" and df.iloc[0]["status"] == "filled"


def test_drawdown_series_known():
    eq = pd.Series([100, 120, 60, 90], index=pd.date_range("2021", periods=4))
    dd = drawdown_series(eq)
    assert dd.iloc[0] == 0.0          # at a new peak
    assert dd.iloc[1] == 0.0          # still the peak
    assert abs(dd.iloc[2] - (-0.5)) < 1e-9  # 120 -> 60
    assert dd.empty is False
    assert drawdown_series(pd.Series(dtype=float)).empty


def test_align_benchmark_normalizes_to_equity_start():
    idx = pd.date_range("2022-01-01", periods=5, freq="D")
    equity = pd.Series([100_000, 101_000, 99_000, 103_000, 105_000.0], index=idx)
    spy = pd.Series([400, 404, 396, 412, 420.0], index=idx)
    out = align_benchmark(equity, spy)
    assert list(out.columns) == ["strategy", "benchmark"]
    # Benchmark rebased to the agent's starting equity.
    assert abs(out["benchmark"].iloc[0] - 100_000.0) < 1e-6
    assert len(out) == len(equity)


def test_align_benchmark_handles_empty_benchmark():
    idx = pd.date_range("2022-01-01", periods=3, freq="D")
    equity = pd.Series([1.0, 2.0, 3.0], index=idx)
    out = align_benchmark(equity, pd.Series(dtype=float))
    assert out["benchmark"].isna().all()


def test_kpi_summary_insufficient_then_ok():
    short = pd.Series([100_000.0], index=pd.date_range("2022", periods=1))
    assert kpi_summary(short)["status"] == "insufficient_history"

    idx = pd.date_range("2020-01-01", periods=505, freq="B")
    eq = pd.Series(np.linspace(100_000, 120_000, 505), index=idx)
    bench = pd.Series(np.linspace(100_000, 110_000, 505), index=idx)
    s = kpi_summary(eq, bench)
    assert s["status"] == "ok"
    assert s["total_return_pct"] == 20.0
    assert s["benchmark_return_pct"] == 10.0
    assert s["vs_benchmark_pct"] == 10.0
    assert "max_drawdown_pct" in s
