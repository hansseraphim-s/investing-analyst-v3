import numpy as np
import pandas as pd
import pytest

from iav3.strategy import (
    MeanReversionStrategy,
    MomentumStrategy,
    VolTargetTrendStrategy,
    available_strategies,
    get_strategy,
)


def _bars(close: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range("2018-01-01", periods=len(close), freq="B")
    c = pd.Series(close, index=idx, dtype=float)
    return pd.DataFrame(
        {
            "open": c,
            "high": c * 1.02,
            "low": c * 0.98,
            "close": c,
            "volume": 2_000_000,
        }
    )


@pytest.mark.parametrize(
    "strat",
    [MomentumStrategy(), MeanReversionStrategy(), VolTargetTrendStrategy()],
)
def test_contract_target_and_atr(strat):
    rng = np.random.default_rng(7)
    df = _bars(100 + np.cumsum(rng.normal(0.05, 1.0, 600)))
    out = strat.generate(df)
    assert "target" in out and "atr" in out
    assert len(out) == len(df)
    assert set(pd.unique(out["target"])).issubset({0, 1})
    # Warm-up region must be flat (no fabricated signals before indicators exist).
    assert (out["target"].iloc[: strat.warmup] == 0).all()


def test_registry_roundtrip():
    assert set(available_strategies()) == {
        "momentum", "mean_reversion", "vol_target_trend"
    }
    assert isinstance(get_strategy("MOMENTUM"), MomentumStrategy)
    assert isinstance(get_strategy("vol_target_trend"), VolTargetTrendStrategy)
    with pytest.raises(ValueError):
        get_strategy("does_not_exist")


def test_momentum_goes_long_in_strong_uptrend():
    # A *realistic* uptrend: positive drift WITH noise, so RSI oscillates
    # into the (50, 72) band. A perfectly linear ramp would give RSI==100
    # (zero losses) and be correctly excluded by the overbought filter.
    rng = np.random.default_rng(11)
    df = _bars(50 + np.cumsum(rng.normal(0.35, 1.0, 500)).clip(min=-40))
    out = MomentumStrategy().generate(df)
    assert out["target"].iloc[250:].sum() > 0


def test_mean_reversion_stays_flat_in_downtrend():
    df = _bars(np.linspace(250, 50, 500))  # persistent downtrend
    out = MeanReversionStrategy().generate(df)
    # Regime filter (close > 200SMA) must keep it out of a falling market.
    assert out["target"].sum() == 0
