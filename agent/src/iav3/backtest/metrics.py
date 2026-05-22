"""Performance metrics computed from an equity curve.

All metrics describe historical behavior of a strategy on past data. They are
not predictive. Max drawdown is reported alongside return on purpose: a
return figure without its drawdown is marketing, not analysis.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass(frozen=True)
class PerformanceMetrics:
    start: str
    end: str
    days: int
    total_return_pct: float
    cagr_pct: float
    annual_vol_pct: float
    sharpe: float
    sortino: float
    max_drawdown_pct: float
    calmar: float
    exposure_pct: float
    num_trades: int
    win_rate_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    profit_factor: float
    final_equity: float

    def as_dict(self) -> dict:
        return asdict(self)


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b not in (0, 0.0) and not np.isnan(b) else default


def max_drawdown(equity: pd.Series) -> float:
    """Maximum peak-to-trough decline as a negative fraction (e.g. -0.23)."""
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    return float(drawdown.min())


def compute_metrics(
    equity: pd.Series,
    trades: list[dict],
    exposure_fraction: float,
) -> PerformanceMetrics:
    equity = equity.dropna()
    if len(equity) < 2:
        raise ValueError("Equity curve needs at least 2 points for metrics")

    start, end = equity.index[0], equity.index[-1]
    days = max((end - start).days, 1)
    years = days / 365.25

    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0) if years > 0 else 0.0

    rets = equity.pct_change().dropna()
    ann_vol = float(rets.std() * np.sqrt(TRADING_DAYS)) if len(rets) > 1 else 0.0
    sharpe = _safe_div(float(rets.mean()) * TRADING_DAYS, ann_vol)

    downside = rets[rets < 0]
    downside_dev = float(downside.std() * np.sqrt(TRADING_DAYS)) if len(downside) > 1 else 0.0
    sortino = _safe_div(float(rets.mean()) * TRADING_DAYS, downside_dev)

    mdd = max_drawdown(equity)
    calmar = _safe_div(cagr, abs(mdd))

    closed = [t for t in trades if t.get("exit_price") is not None]
    wins = [t for t in closed if t["return_pct"] > 0]
    losses = [t for t in closed if t["return_pct"] <= 0]
    win_rate = _safe_div(len(wins), len(closed)) * 100.0
    avg_win = float(np.mean([t["return_pct"] for t in wins])) if wins else 0.0
    avg_loss = float(np.mean([t["return_pct"] for t in losses])) if losses else 0.0
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = _safe_div(gross_profit, gross_loss, default=float("inf") if gross_profit else 0.0)

    return PerformanceMetrics(
        start=str(start.date()),
        end=str(end.date()),
        days=days,
        total_return_pct=round(total_return * 100, 2),
        cagr_pct=round(cagr * 100, 2),
        annual_vol_pct=round(ann_vol * 100, 2),
        sharpe=round(sharpe, 2),
        sortino=round(sortino, 2),
        max_drawdown_pct=round(mdd * 100, 2),
        calmar=round(calmar, 2),
        exposure_pct=round(exposure_fraction * 100, 2),
        num_trades=len(closed),
        win_rate_pct=round(win_rate, 2),
        avg_win_pct=round(avg_win * 100, 2),
        avg_loss_pct=round(avg_loss * 100, 2),
        profit_factor=round(profit_factor, 2) if profit_factor != float("inf") else profit_factor,
        final_equity=round(float(equity.iloc[-1]), 2),
    )
