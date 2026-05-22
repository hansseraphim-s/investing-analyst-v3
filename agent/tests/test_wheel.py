import numpy as np
import pandas as pd

from iav3.options import MODEL_CAVEAT, run_wheel_backtest


def _bars(close: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range("2016-01-01", periods=len(close), freq="B")
    c = pd.Series(close, index=idx, dtype=float)
    return pd.DataFrame(
        {"open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
         "volume": 1_000_000}
    )


def test_caveat_is_attached_and_loud():
    df = _bars(100 + np.cumsum(np.random.default_rng(0).normal(0, 1, 400)))
    res = run_wheel_backtest("X", df)
    assert "MODEL, NOT P&L" in MODEL_CAVEAT
    assert res.caveat == MODEL_CAVEAT


def test_summary_contract_and_equity_curve():
    df = _bars(100 + np.cumsum(np.random.default_rng(2).normal(0.05, 1.0, 600)))
    res = run_wheel_backtest("X", df, starting_cash=100_000.0)
    for key in (
        "cycles", "settled", "assigned", "called_away",
        "total_premium_collected", "total_return_pct", "max_drawdown_pct",
        "final_equity",
    ):
        assert key in res.summary
    assert len(res.equity) == len(df)
    assert not res.equity.isna().any()
    assert res.summary["total_premium_collected"] >= 0.0


def test_steady_uptrend_puts_expire_worthless():
    # Price only rises -> cash-secured puts should never be assigned.
    df = _bars(np.linspace(100, 300, 500))
    res = run_wheel_backtest("X", df, starting_cash=100_000.0)
    assert res.summary["assigned"] == 0
    assert res.summary["cycles"] >= 1


def test_crash_triggers_assignment():
    # A hard, persistent drop must assign the put (end up holding shares).
    df = _bars(np.concatenate([np.full(60, 100.0), np.linspace(100, 30, 440)]))
    res = run_wheel_backtest("X", df, starting_cash=100_000.0)
    assert res.summary["assigned"] >= 1


def test_iv_assumption_moves_results_monotonically():
    # Higher assumed IV -> richer premiums -> higher modeled return. This test
    # exists to make the core caveat undeniable: the headline number is a
    # direct function of an unverifiable assumption.
    # Realistic noisy uptrend so realized vol (hence premium) is non-trivial.
    # Same price path for both runs -> assignment outcomes identical; only the
    # premium scale differs, so return must strictly increase with the mult.
    rng = np.random.default_rng(5)
    df = _bars(100 + np.cumsum(rng.normal(0.12, 1.2, 500)))
    lo = run_wheel_backtest("X", df, iv_premium_mult=1.0).summary["total_return_pct"]
    hi = run_wheel_backtest("X", df, iv_premium_mult=1.5).summary["total_return_pct"]
    assert hi > lo
