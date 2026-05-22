"""CVaR-based daily loss breaker.

Replaces v2's absolute max_daily_loss_pct with a regime-adaptive cap:
the breaker fires when day P&L breaches the historical 5th-percentile
day return over a rolling lookback. This adjusts to volatility regime
automatically — a -3% day in a calm regime trips, a -3% day in a vol
regime may not.

Phase 2 implementation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CVaRDecision:
    breached: bool
    day_pnl_pct: float
    cvar_threshold_pct: float
    detail: str


def cvar_threshold(returns_history: list[float], *, alpha: float = 0.05) -> float:
    """Conditional value-at-risk: mean of returns below the alpha-quantile.

    Returns a NEGATIVE number when there are sufficient negative-return
    observations in history. Returns 0.0 when history is too short.
    """
    raise NotImplementedError("Phase 2: implement CVaR from returns history.")


def check_cvar_breaker(
    day_pnl_pct: float,
    returns_history: list[float],
    *,
    alpha: float = 0.05,
) -> CVaRDecision:
    """Decide whether to halt new entries for the day."""
    raise NotImplementedError("Phase 2: implement CVaR breaker.")
