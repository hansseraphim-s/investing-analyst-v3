"""Walk-forward validation with purged k-fold.

Why this exists: hand-tuned strategy parameters look great in-sample and
fail out-of-sample. Walk-forward fits parameters on a rolling in-sample
window and reports performance only on the immediately following
out-of-sample window. Purged k-fold prevents look-ahead bias when
features have lookback dependencies.

Gate (enforced before paper -> live promotion):
    - oos_sharpe > 0.7 net of fees
    - oos_max_dd_pct < 25%
    - consistency: no OOS window with sharpe < -0.5

Phase 0: interface contract only. Phase 1: implementation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

import pandas as pd

from ..strategy.base import Strategy


@dataclass(frozen=True)
class WalkForwardWindow:
    is_start: date
    is_end: date
    oos_start: date
    oos_end: date


@dataclass(frozen=True)
class WalkForwardResult:
    run_id: uuid.UUID
    strategy_name: str
    symbol: str
    windows: list[WalkForwardWindow]
    in_sample_sharpe: list[float]
    out_of_sample_sharpe: list[float]
    out_of_sample_return_pct: list[float]
    out_of_sample_max_dd_pct: list[float]
    best_params_per_window: list[dict[str, Any]]
    aggregate_oos_sharpe: float
    aggregate_oos_max_dd_pct: float
    promotion_eligible: bool


def generate_windows(
    start: date,
    end: date,
    *,
    is_months: int = 36,
    oos_months: int = 6,
    step_months: int = 6,
) -> list[WalkForwardWindow]:
    """Rolling-origin windows for walk-forward validation."""
    raise NotImplementedError("Phase 1: implement rolling-origin window generation.")


def walk_forward(
    *,
    strategy_factory: Callable[..., Strategy],
    param_grid: dict[str, list[Any]],
    data: dict[str, pd.DataFrame],
    windows: list[WalkForwardWindow],
    promotion_oos_sharpe: float = 0.7,
    promotion_max_dd_pct: float = 25.0,
) -> WalkForwardResult:
    """Run walk-forward across all (symbol, window) pairs.

    For each window:
      1. Fit param_grid on the in-sample window via grid search on Sharpe.
      2. Score the best params on the immediately following OOS window.
      3. Persist the OOS metrics to walk_forward_runs.

    Returns aggregate metrics and a promotion_eligible flag against the
    documented gates.
    """
    raise NotImplementedError("Phase 1: implement walk-forward loop.")
