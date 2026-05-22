import numpy as np
import pandas as pd

from iav3.backtest import compute_metrics, max_drawdown, run_backtest
from iav3.strategy.base import Strategy


def _bars(close: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=len(close), freq="B")
    c = pd.Series(close, index=idx, dtype=float)
    return pd.DataFrame(
        {
            "open": c.shift(1).fillna(c.iloc[0]),
            "high": c * 1.01,
            "low": c * 0.99,
            "close": c,
            "volume": 1_000_000,
        }
    )


class AlwaysFlat(Strategy):
    name = "always_flat"
    warmup = 1

    def generate(self, df):
        out = self._with_atr(df)
        out["target"] = 0
        return out


class AlwaysLong(Strategy):
    name = "always_long"
    warmup = 15

    def generate(self, df):
        out = self._with_atr(df)
        out["target"] = 0
        out.iloc[15:, out.columns.get_loc("target")] = 1
        return out


def test_flat_strategy_preserves_cash_exactly():
    df = _bars(100 + np.cumsum(np.random.default_rng(1).normal(0, 1, 200)))
    res = run_backtest("X", df, AlwaysFlat(), starting_cash=50_000.0)
    assert res.metrics.num_trades == 0
    assert abs(res.equity.iloc[-1] - 50_000.0) < 1e-6
    assert res.metrics.exposure_pct == 0.0


def test_long_on_uptrend_makes_money_and_trades():
    df = _bars(np.linspace(100, 200, 250))  # steady uptrend
    res = run_backtest("X", df, AlwaysLong(), starting_cash=100_000.0,
                       slippage_bps=5.0)
    assert res.metrics.num_trades >= 1
    assert res.metrics.total_return_pct > 0
    # Equity series is strictly defined over the bar index, no NaNs.
    assert not res.equity.isna().any()


def test_no_lookahead_first_trade_not_before_signal():
    df = _bars(np.linspace(100, 200, 250))
    res = run_backtest("X", df, AlwaysLong(), starting_cash=100_000.0)
    first_entry = res.trades[0]["entry_time"]
    # AlwaysLong sets target=1 starting at row 15; engine acts on the NEXT
    # bar's open, so the first entry must be at row index >= 16.
    entry_pos = df.index.get_loc(pd.Timestamp(first_entry))
    assert entry_pos >= 16


def test_max_drawdown_known_value():
    eq = pd.Series([100, 120, 60, 90], index=pd.date_range("2021", periods=4))
    # Peak 120 -> trough 60 => -50%.
    assert abs(max_drawdown(eq) - (-0.5)) < 1e-9


def test_compute_metrics_on_synthetic_curve():
    idx = pd.date_range("2020-01-01", periods=505, freq="B")  # ~2y
    eq = pd.Series(np.linspace(100_000, 121_000, 505), index=idx)
    m = compute_metrics(eq, [], 0.5)
    assert m.total_return_pct == 21.0
    assert m.cagr_pct > 0
    assert m.max_drawdown_pct == 0.0  # monotonic rise -> no drawdown
