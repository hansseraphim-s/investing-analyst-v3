"""CVaR-based daily-loss breaker.

Why this exists: a flat absolute daily-loss threshold (e.g. "halt at -3%")
overshoots in calm regimes and undershoots in vol regimes. CVaR
(Conditional Value-at-Risk) compares today's day P&L against the mean
of the worst alpha-fraction of historical days, so the threshold
auto-adapts to volatility regime.

Mechanics:
  1. Take historical daily returns of the portfolio.
  2. Sort, pick the worst alpha fraction (default 5%).
  3. CVaR = mean of those worst returns (a NEGATIVE number).
  4. If today's day_pnl_pct breaches CVaR, halt new entries.

Bootstrap: requires >= 30 historical observations. Below that, returns
0.0 threshold and the breaker is INACTIVE (does not block). Callers
should be aware that during the first 30 trading days the absolute
max_daily_loss_pct gate is the only daily-loss protection.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

MIN_HISTORY = 30


@dataclass(frozen=True)
class CVaRDecision:
    breached: bool
    day_pnl_pct: float           # today's day P&L, as a percentage (e.g. -3.5 for -3.5%)
    cvar_threshold_pct: float    # the CVaR threshold (as a percentage; negative when active, 0.0 when inactive)
    history_size: int
    detail: str


def cvar_threshold(returns_history: list[float], *, alpha: float = 0.05) -> float:
    """Mean of returns below the alpha-quantile, as a FRACTION (e.g. -0.024 = -2.4%).

    Returns 0.0 when history is too short (< MIN_HISTORY). Callers MUST
    treat 0.0 as "breaker inactive", not as "threshold reached".

    `returns_history` items must be daily-return fractions, e.g. -0.012 for
    a -1.2% day. NOT percentages — pass -0.012, not -1.2.
    """
    if not returns_history or len(returns_history) < MIN_HISTORY:
        return 0.0
    if alpha <= 0.0 or alpha >= 1.0:
        raise ValueError(f"alpha must be in (0, 1); got {alpha}")
    sorted_rets = sorted(returns_history)
    # Worst alpha fraction. Ensure at least 1 observation lands in the bucket.
    cutoff_idx = max(int(round(len(sorted_rets) * alpha)), 1)
    worst = sorted_rets[:cutoff_idx]
    return float(mean(worst))


def check_cvar_breaker(
    day_pnl_pct: float,
    returns_history: list[float],
    *,
    alpha: float = 0.05,
) -> CVaRDecision:
    """Decide whether to halt new entries for the day.

    `day_pnl_pct` is today's day P&L as a PERCENTAGE (e.g. -3.5 for -3.5%).
    `returns_history` is the list of daily-return FRACTIONS (e.g. -0.012).
    The threshold is compared on the percentage scale (multiplied by 100).
    """
    threshold_frac = cvar_threshold(returns_history, alpha=alpha)
    threshold_pct = threshold_frac * 100.0

    if threshold_frac == 0.0:
        return CVaRDecision(
            breached=False,
            day_pnl_pct=day_pnl_pct,
            cvar_threshold_pct=0.0,
            history_size=len(returns_history) if returns_history else 0,
            detail=(
                f"CVaR breaker inactive: history has "
                f"{len(returns_history) if returns_history else 0} obs "
                f"(need {MIN_HISTORY}+). Falling back to absolute daily-loss gate only."
            ),
        )

    breached = day_pnl_pct < threshold_pct
    if breached:
        detail = (
            f"Day P&L {day_pnl_pct:+.2f}% breached CVaR threshold {threshold_pct:+.2f}% "
            f"(α={alpha:.0%} over {len(returns_history)} days). Halting new entries."
        )
    else:
        detail = (
            f"Day P&L {day_pnl_pct:+.2f}% above CVaR threshold {threshold_pct:+.2f}% "
            f"(α={alpha:.0%} over {len(returns_history)} days)."
        )

    return CVaRDecision(
        breached=breached,
        day_pnl_pct=day_pnl_pct,
        cvar_threshold_pct=threshold_pct,
        history_size=len(returns_history),
        detail=detail,
    )
