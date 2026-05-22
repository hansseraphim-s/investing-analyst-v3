import numpy as np
import pandas as pd

from iav3.backtest import run_backtest
from iav3.strategy import VolTargetTrendStrategy


def _bars(close: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range("2017-01-01", periods=len(close), freq="B")
    c = pd.Series(close, index=idx, dtype=float)
    return pd.DataFrame(
        {"open": c, "high": c * 1.02, "low": c * 0.98, "close": c,
         "volume": 1_000_000}
    )


def test_emits_target_weight_and_no_leverage():
    rng = np.random.default_rng(3)
    df = _bars(100 + np.cumsum(rng.normal(0.1, 1.0, 600)))
    out = VolTargetTrendStrategy().generate(df)
    assert {"target", "target_weight", "atr"} <= set(out.columns)
    w = out["target_weight"]
    assert (w >= 0.0).all() and (w <= 1.0).all()  # never leveraged
    assert not w.isna().any()


def test_weight_is_zero_when_trend_off():
    df = _bars(np.linspace(250, 50, 500))  # persistent downtrend
    out = VolTargetTrendStrategy().generate(df)
    assert (out["target"] == 0).all()
    assert (out["target_weight"] == 0.0).all()


def test_lower_vol_gets_larger_weight():
    # Two uptrends, same drift, different noise -> the calmer one should be
    # sized larger by the inverse-vol rule (when both are in an uptrend).
    rng = np.random.default_rng(9)
    calm = _bars(100 + np.cumsum(rng.normal(0.2, 0.4, 600)))
    wild = _bars(100 + np.cumsum(rng.normal(0.2, 2.0, 600)))
    s = VolTargetTrendStrategy()
    wc = s.generate(calm)
    ww = s.generate(wild)
    on_c = wc.loc[wc["target"] == 1, "target_weight"].mean()
    on_w = ww.loc[ww["target"] == 1, "target_weight"].mean()
    assert on_c > on_w


def test_runs_through_backtester_with_fractional_sizing():
    rng = np.random.default_rng(1)
    df = _bars(100 + np.cumsum(rng.normal(0.15, 1.0, 700)))
    res = run_backtest("X", df, VolTargetTrendStrategy(), starting_cash=100_000)
    assert not res.equity.isna().any()
    # Every entry must have been sized within cash (no leverage / no negative).
    assert (res.equity > 0).all()
