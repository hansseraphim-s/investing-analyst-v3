"""Unit tests for walk-forward validation.

Focuses on the pure-logic surface (window generation, param-grid
iteration, aggregation + promotion gate evaluation). One integration
test exercises the full walk_forward loop end-to-end against a trivial
always-flat strategy + synthetic OHLCV to verify the orchestration
plumbing without testing the backtester (which has its own tests).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from iav3.backtest.walk_forward import (
    PromotionGates,
    WalkForwardWindow,
    WalkForwardWindowResult,
    _add_months,
    aggregate,
    generate_windows,
    iter_param_grid,
    walk_forward,
)
from iav3.strategy.base import Strategy


# ----------------------------------------------------------------------
# generate_windows
# ----------------------------------------------------------------------


class TestGenerateWindows:
    def test_basic_36_6_6(self):
        windows = generate_windows(
            date(2015, 1, 1), date(2025, 1, 1),
            is_months=36, oos_months=6, step_months=6,
        )
        assert len(windows) > 0
        first = windows[0]
        assert first.is_start == date(2015, 1, 1)
        # IS = 36 months, then +1 day purge, then OOS = 6 months
        assert first.is_end == date(2018, 1, 1)
        assert first.oos_start == date(2018, 1, 2)
        assert first.oos_end == date(2018, 7, 2)

    def test_step_advances_by_step_months(self):
        windows = generate_windows(
            date(2020, 1, 1), date(2024, 1, 1),
            is_months=12, oos_months=6, step_months=3,
        )
        # Each window's is_start should be 3 months after the previous
        for prev, cur in zip(windows, windows[1:]):
            assert _add_months(prev.is_start, 3) == cur.is_start

    def test_purge_days_shifts_oos_start(self):
        windows = generate_windows(
            date(2020, 1, 1), date(2025, 1, 1),
            is_months=12, oos_months=6, step_months=6,
            purge_days=5,
        )
        first = windows[0]
        # IS end is 2021-01-01; OOS should start at +5+1 = 2021-01-07
        assert first.is_end == date(2021, 1, 1)
        assert first.oos_start == date(2021, 1, 7)

    def test_empty_when_end_before_start(self):
        assert generate_windows(date(2025, 1, 1), date(2020, 1, 1)) == []

    def test_stops_when_oos_overruns_end(self):
        windows = generate_windows(
            date(2020, 1, 1), date(2021, 6, 1),
            is_months=12, oos_months=6, step_months=6,
        )
        # IS=Jan 2020 - Jan 2021, OOS=Jan 2021 - Jul 2021 -> oos_end > 2021-06-01, so 0 windows
        assert windows == []

    def test_negative_or_zero_months_raises(self):
        with pytest.raises(ValueError):
            generate_windows(date(2020, 1, 1), date(2025, 1, 1), is_months=0)
        with pytest.raises(ValueError):
            generate_windows(date(2020, 1, 1), date(2025, 1, 1), oos_months=-1)
        with pytest.raises(ValueError):
            generate_windows(date(2020, 1, 1), date(2025, 1, 1), step_months=0)


# ----------------------------------------------------------------------
# iter_param_grid
# ----------------------------------------------------------------------


class TestIterParamGrid:
    def test_empty_grid_yields_one_empty_dict(self):
        assert list(iter_param_grid({})) == [{}]

    def test_single_key(self):
        assert list(iter_param_grid({"a": [1, 2, 3]})) == [{"a": 1}, {"a": 2}, {"a": 3}]

    def test_cartesian_product(self):
        result = list(iter_param_grid({"a": [1, 2], "b": [10, 20]}))
        assert len(result) == 4
        assert {"a": 1, "b": 10} in result
        assert {"a": 1, "b": 20} in result
        assert {"a": 2, "b": 10} in result
        assert {"a": 2, "b": 20} in result


# ----------------------------------------------------------------------
# aggregate + promotion gate
# ----------------------------------------------------------------------


def _mk_result(oos_sharpe: float, oos_max_dd: float = -10.0) -> WalkForwardWindowResult:
    return WalkForwardWindowResult(
        symbol="AAPL",
        window=WalkForwardWindow(
            date(2020, 1, 1), date(2023, 1, 1),
            date(2023, 1, 2), date(2023, 7, 2),
        ),
        best_params={},
        is_sharpe=1.0,
        oos_sharpe=oos_sharpe,
        oos_return_pct=0.0,
        oos_max_dd_pct=oos_max_dd,
    )


class TestAggregate:
    def test_empty_results(self):
        agg_s, agg_dd, worst, promo, rationale = aggregate([])
        assert agg_s == 0.0
        assert agg_dd == 0.0
        assert promo is False
        assert "No usable" in rationale

    def test_promotion_when_all_gates_pass(self):
        results = [_mk_result(0.9, -10.0), _mk_result(0.8, -15.0), _mk_result(1.0, -8.0)]
        agg_s, agg_dd, worst, promo, _ = aggregate(results)
        assert agg_s == pytest.approx(0.9, abs=0.01)
        assert agg_dd == 15.0
        assert worst == 0.8
        assert promo is True

    def test_blocks_when_sharpe_below_gate(self):
        results = [_mk_result(0.5, -10.0), _mk_result(0.4, -10.0)]
        _, _, _, promo, rationale = aggregate(results)
        assert promo is False
        assert "<=" in rationale.split(";")[0]

    def test_blocks_when_dd_exceeds_gate(self):
        results = [_mk_result(0.9, -30.0), _mk_result(0.9, -10.0)]
        _, agg_dd, _, promo, _ = aggregate(results)
        assert agg_dd == 30.0
        assert promo is False

    def test_blocks_when_worst_window_below_floor(self):
        results = [_mk_result(0.9, -10.0), _mk_result(-0.8, -10.0)]
        _, _, worst, promo, _ = aggregate(results)
        assert worst == -0.8
        assert promo is False

    def test_custom_gates(self):
        results = [_mk_result(0.5, -10.0), _mk_result(0.5, -10.0)]
        gates = PromotionGates(promotion_oos_sharpe=0.4)
        _, _, _, promo, _ = aggregate(results, gates=gates)
        assert promo is True


# ----------------------------------------------------------------------
# Integration: walk_forward loop with a trivial fake strategy
# ----------------------------------------------------------------------


class _AlwaysFlat(Strategy):
    name = "always_flat"
    warmup = 0

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._with_atr(df)
        out["target"] = 0
        return out


def _synthetic_ohlcv(start="2018-01-01", end="2023-12-31") -> pd.DataFrame:
    idx = pd.bdate_range(start=start, end=end)
    rng = np.random.default_rng(42)
    rets = rng.normal(0.0005, 0.012, size=len(idx))
    close = 100 * np.exp(np.cumsum(rets))
    return pd.DataFrame({
        "open": close * 0.999,
        "high": close * 1.005,
        "low": close * 0.995,
        "close": close,
        "volume": rng.integers(1_000_000, 5_000_000, size=len(idx)),
    }, index=idx)


class TestWalkForwardLoop:
    def test_runs_end_to_end_with_trivial_strategy(self):
        windows = generate_windows(
            date(2018, 1, 1), date(2023, 12, 31),
            is_months=24, oos_months=6, step_months=12,
        )
        assert len(windows) >= 2

        data = {"AAPL": _synthetic_ohlcv()}
        result = walk_forward(
            strategy_factory=_AlwaysFlat,
            param_grid={},
            data=data,
            windows=windows,
        )
        assert result.strategy_name == "always_flat"
        assert len(result.windows) == len(windows)
        # AlwaysFlat takes 0 trades, so Sharpe is 0 and DD is 0 -> not promoted under default gates
        assert result.promotion_eligible is False
        assert result.aggregate_oos_sharpe == pytest.approx(0.0, abs=0.01)

    def test_raises_with_no_windows(self):
        with pytest.raises(ValueError, match="at least one window"):
            walk_forward(
                strategy_factory=_AlwaysFlat,
                param_grid={},
                data={"AAPL": _synthetic_ohlcv()},
                windows=[],
            )

    def test_raises_with_no_data(self):
        windows = generate_windows(date(2018, 1, 1), date(2023, 12, 31))
        with pytest.raises(ValueError, match="at least one symbol"):
            walk_forward(
                strategy_factory=_AlwaysFlat,
                param_grid={},
                data={},
                windows=windows,
            )
