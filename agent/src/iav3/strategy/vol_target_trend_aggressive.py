"""Aggressive variant of vol_target_trend — faster signals, higher vol target.

Same DIRECTION + SIZE separation as the baseline, but tuned for higher
expected return at substantially higher drawdown risk:

  - fast EMA 10 (was 20): captures shorter trends, more turnover
  - slow EMA 50 (was 100): faster regime detection
  - target_vol 0.20 (was 0.15): 33% more sizing for the same realized vol
  - max_weight 1.0 (unchanged): NO leverage — clamp stays in

Walk-forward NOT yet run on this variant. Expected behavior is higher
average return AND higher dispersion of OOS outcomes — more good
windows AND more bad windows than the baseline. Paper-trade observation
is the validation, not historical backtest of these exact params.

Promotion to live still requires the walk-forward gate to pass with
loosened thresholds (oos_sharpe > 0.5, max_dd < 35%, worst_window > -1.0).
"""

from __future__ import annotations

from .vol_target_trend import VolTargetTrendStrategy


class VolTargetTrendAggressiveStrategy(VolTargetTrendStrategy):
    name = "vol_target_trend_aggressive"
    warmup = 200  # unchanged — 200-bar regime SMA still in use

    def __init__(self) -> None:
        super().__init__(
            fast=10,
            slow=50,
            regime=200,
            vol_window=20,
            target_vol=0.20,
            max_weight=1.0,
        )
