import numpy as np
import pandas as pd

from iav3.data import atr, ema, rsi, sma, true_range


def test_sma_warmup_is_nan_then_correct():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    out = sma(s, 3)
    assert out.iloc[:2].isna().all()
    assert out.iloc[2] == 2.0  # mean(1,2,3)
    assert out.iloc[4] == 4.0  # mean(3,4,5)


def test_ema_warmup_and_value():
    s = pd.Series(range(1, 21), dtype=float)
    out = ema(s, 5)
    assert out.iloc[:4].isna().all()
    assert not np.isnan(out.iloc[4])
    # EMA of a rising series stays below the latest price.
    assert out.iloc[-1] < s.iloc[-1]


def test_rsi_bounds_and_warmup():
    rng = np.random.default_rng(42)
    s = pd.Series(100 + np.cumsum(rng.normal(0, 1, 300)))
    r = rsi(s, 14)
    assert r.iloc[:14].isna().all()
    valid = r.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_rsi_all_gains_approaches_100():
    s = pd.Series(np.arange(1, 60, dtype=float))  # strictly increasing
    r = rsi(s, 14).dropna()
    assert r.iloc[-1] > 99.0  # only gains -> RSI pinned near 100


def test_rsi_all_losses_approaches_0():
    s = pd.Series(np.arange(60, 1, -1, dtype=float))  # strictly decreasing
    r = rsi(s, 14).dropna()
    assert r.iloc[-1] < 1.0


def test_rsi_flat_series_is_neutral():
    s = pd.Series([50.0] * 40)
    r = rsi(s, 14).dropna()
    # No gains and no losses -> defined as neutral 50, not NaN/inf.
    assert (r == 50.0).all()


def test_true_range_and_atr_positive():
    n = 50
    rng = np.random.default_rng(0)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    high = close + 1.0
    low = close - 1.0
    tr = true_range(high, low, close)
    assert (tr.dropna() >= 0).all()
    a = atr(high, low, close, 14)
    # true_range[0] is valid (high-low), so the Wilder/EMA ATR's first value
    # lands at index period-1 == 13, not 14.
    assert a.iloc[:13].isna().all()
    assert not np.isnan(a.iloc[13])
    assert (a.dropna() > 0).all()
