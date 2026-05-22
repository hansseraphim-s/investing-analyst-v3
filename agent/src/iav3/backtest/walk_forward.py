"""Walk-forward validation with rolling-origin windows and a purge gap.

Why this exists: hand-tuned strategy parameters look great in-sample and
fail out-of-sample. Walk-forward fits parameters on a rolling in-sample
window via grid search on Sharpe, then scores the best params on the
immediately following out-of-sample window. Strategies that don't pass
the aggregate OOS gate don't get promoted to paper, and strategies that
don't pass paper soak don't get promoted to live.

Purge gap: when feature lookback is N days, OOS starts N+1 days after IS
ends to prevent IS labels leaking into OOS features through overlapping
lookback. Pass the strategy's `warmup` as `purge_days`.

Promotion gate (default, configurable):
    - aggregate OOS Sharpe > 0.7
    - max OOS drawdown < 25%
    - no single window with OOS Sharpe below -0.5

The result object carries every per-window score so failure modes can be
inspected: an aggregate that just barely passes but rests on one
outlier window is not the same as broad-based positive OOS performance,
and the per-window breakdown surfaces the difference.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import product
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd

from ..strategy.base import Strategy
from .engine import run_backtest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WalkForwardWindow:
    is_start: date
    is_end: date
    oos_start: date
    oos_end: date


@dataclass(frozen=True)
class WalkForwardWindowResult:
    symbol: str
    window: WalkForwardWindow
    best_params: dict[str, Any]
    is_sharpe: float
    oos_sharpe: float
    oos_return_pct: float
    oos_max_dd_pct: float


@dataclass(frozen=True)
class PromotionGates:
    promotion_oos_sharpe: float = 0.7
    promotion_max_dd_pct: float = 25.0
    worst_window_sharpe_floor: float = -0.5


@dataclass(frozen=True)
class WalkForwardResult:
    run_id: uuid.UUID
    strategy_name: str
    windows: list[WalkForwardWindowResult]
    aggregate_oos_sharpe: float
    aggregate_oos_max_dd_pct: float
    worst_oos_sharpe: float
    promotion_eligible: bool
    rationale: str
    gates: PromotionGates


# ----------------------------------------------------------------------
# Pure helpers (easy to unit-test)
# ----------------------------------------------------------------------


def _add_months(d: date, months: int) -> date:
    """Add `months` months to a date using pandas DateOffset (clamps to month-end)."""
    return (pd.Timestamp(d) + pd.DateOffset(months=months)).date()


def generate_windows(
    start: date,
    end: date,
    *,
    is_months: int = 36,
    oos_months: int = 6,
    step_months: int = 6,
    purge_days: int = 0,
) -> list[WalkForwardWindow]:
    """Rolling-origin walk-forward windows.

    Each cursor produces a window of (IS=`is_months`, OOS=`oos_months`).
    The cursor advances by `step_months` until the OOS endpoint would
    exceed `end`. `purge_days` shifts OOS start forward to prevent
    lookback overlap with the IS window.
    """
    if is_months <= 0 or oos_months <= 0 or step_months <= 0:
        raise ValueError("is_months, oos_months, step_months must all be > 0")
    if start >= end:
        return []

    windows: list[WalkForwardWindow] = []
    cursor = start
    while True:
        is_end = _add_months(cursor, is_months)
        oos_start = is_end + timedelta(days=max(purge_days, 0) + 1)
        oos_end = _add_months(oos_start, oos_months)
        if oos_end > end:
            break
        windows.append(WalkForwardWindow(cursor, is_end, oos_start, oos_end))
        cursor = _add_months(cursor, step_months)
    return windows


def iter_param_grid(grid: dict[str, list[Any]]) -> Iterable[dict[str, Any]]:
    """Yield the cartesian product of param values as kwarg dicts.

    Empty grid yields exactly one empty dict (so `walk_forward` can be
    called with no grid to score a strategy at its defaults).
    """
    if not grid:
        yield {}
        return
    keys = list(grid.keys())
    for values in product(*[grid[k] for k in keys]):
        yield dict(zip(keys, values, strict=True))


def aggregate(
    results: list[WalkForwardWindowResult],
    *,
    gates: PromotionGates | None = None,
) -> tuple[float, float, float, bool, str]:
    """Aggregate per-window OOS metrics and evaluate the promotion gate.

    Returns (aggregate_sharpe, aggregate_max_dd_pct, worst_sharpe,
    promotion_eligible, rationale). `max_drawdown_pct` in PerformanceMetrics
    is negative; we use its absolute value here so the aggregate is the
    deepest drawdown seen across all windows.
    """
    g = gates or PromotionGates()
    if not results:
        return 0.0, 0.0, 0.0, False, (
            "No usable (symbol, window) combinations produced backtests."
        )

    oos_sharpes = [r.oos_sharpe for r in results]
    oos_max_dds = [abs(r.oos_max_dd_pct) for r in results]
    agg_sharpe = float(np.mean(oos_sharpes))
    agg_max_dd = float(max(oos_max_dds))
    worst_sharpe = float(min(oos_sharpes))

    sharpe_pass = agg_sharpe > g.promotion_oos_sharpe
    dd_pass = agg_max_dd < g.promotion_max_dd_pct
    worst_pass = worst_sharpe > g.worst_window_sharpe_floor
    promo = sharpe_pass and dd_pass and worst_pass

    rationale = "; ".join([
        f"aggregate OOS Sharpe {agg_sharpe:.2f} "
        f"({'>' if sharpe_pass else '<='} gate {g.promotion_oos_sharpe:.2f})",
        f"max OOS drawdown {agg_max_dd:.1f}% "
        f"({'<' if dd_pass else '>='} gate {g.promotion_max_dd_pct:.1f}%)",
        f"worst window Sharpe {worst_sharpe:.2f} "
        f"({'>' if worst_pass else '<='} floor {g.worst_window_sharpe_floor:.2f})",
    ])
    return agg_sharpe, agg_max_dd, worst_sharpe, promo, rationale


# ----------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------


def _slice_window(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Slice df.index (DatetimeIndex) to the closed interval [start, end]."""
    if df.empty:
        return df
    return df.loc[pd.Timestamp(start) : pd.Timestamp(end)]


def walk_forward(
    *,
    strategy_factory: Callable[..., Strategy],
    param_grid: dict[str, list[Any]],
    data: dict[str, pd.DataFrame],
    windows: list[WalkForwardWindow],
    starting_cash: float = 100_000.0,
    gates: PromotionGates | None = None,
    min_is_bars: int = 50,
    min_oos_bars: int = 5,
) -> WalkForwardResult:
    """Run walk-forward across every (symbol, window) pair.

    For each window: grid-search params on IS by Sharpe, score the best
    params on the immediately following OOS window, accumulate per-window
    results. Aggregate evaluates the promotion gate.

    `min_is_bars` / `min_oos_bars` skip degenerate slices (e.g. when the
    underlying didn't trade for part of the window).
    """
    if not windows:
        raise ValueError("walk_forward requires at least one window")
    if not data:
        raise ValueError("walk_forward requires at least one symbol's data")

    run_id = uuid.uuid4()
    strategy_name = strategy_factory().name
    results: list[WalkForwardWindowResult] = []

    for symbol, df in data.items():
        for window in windows:
            is_df = _slice_window(df, window.is_start, window.is_end)
            oos_df = _slice_window(df, window.oos_start, window.oos_end)
            if len(is_df) < min_is_bars or len(oos_df) < min_oos_bars:
                logger.info(
                    "skip (%s, IS=%s..%s, OOS=%s..%s): is=%d, oos=%d",
                    symbol, window.is_start, window.is_end,
                    window.oos_start, window.oos_end, len(is_df), len(oos_df),
                )
                continue

            best_params: dict[str, Any] | None = None
            best_is_sharpe = -float("inf")
            for params in iter_param_grid(param_grid):
                try:
                    strat = strategy_factory(**params)
                    is_res = run_backtest(symbol, is_df, strat, starting_cash=starting_cash)
                except Exception as e:
                    logger.warning(
                        "IS backtest failed (%s, %s, params=%s): %s",
                        symbol, window.is_start, params, e,
                    )
                    continue
                if is_res.metrics.sharpe > best_is_sharpe:
                    best_is_sharpe = is_res.metrics.sharpe
                    best_params = params

            if best_params is None:
                logger.warning("no usable params for (%s, %s)", symbol, window.is_start)
                continue

            try:
                oos_strat = strategy_factory(**best_params)
                oos_res = run_backtest(symbol, oos_df, oos_strat, starting_cash=starting_cash)
            except Exception as e:
                logger.warning("OOS backtest failed (%s, %s): %s", symbol, window.oos_start, e)
                continue

            results.append(WalkForwardWindowResult(
                symbol=symbol,
                window=window,
                best_params=best_params,
                is_sharpe=best_is_sharpe,
                oos_sharpe=oos_res.metrics.sharpe,
                oos_return_pct=oos_res.metrics.total_return_pct,
                oos_max_dd_pct=oos_res.metrics.max_drawdown_pct,
            ))

    agg_sharpe, agg_max_dd, worst_sharpe, promo, rationale = aggregate(results, gates=gates)
    return WalkForwardResult(
        run_id=run_id,
        strategy_name=strategy_name,
        windows=results,
        aggregate_oos_sharpe=agg_sharpe,
        aggregate_oos_max_dd_pct=agg_max_dd,
        worst_oos_sharpe=worst_sharpe,
        promotion_eligible=promo,
        rationale=rationale,
        gates=gates or PromotionGates(),
    )
