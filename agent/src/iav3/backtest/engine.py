"""Event-driven, long-only backtester.

Conservative by construction so results are not optimistically biased:

  * Signals are computed on closed bar *i* and acted on at bar *i+1* open
    (no look-ahead).
  * Slippage is applied adversely to every fill; commission is charged per
    fill.
  * If an entry bar gaps through the ATR stop or target, the worse of
    {gap open, level} is used.
  * If a bar touches both the stop and the target, the STOP is assumed to
    fill first (pessimistic).

It is long/flat only. Shorting is intentionally omitted rather than
hand-waved (borrow, margin, and locate mechanics materially change results).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..config import RiskConfig
from ..strategy.base import Strategy
from .metrics import PerformanceMetrics, compute_metrics


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    equity: pd.Series
    trades: list[dict]
    metrics: PerformanceMetrics
    benchmark_equity: pd.Series | None = field(default=None)


def _apply_slippage(price: float, side: str, slippage_bps: float) -> float:
    adj = price * slippage_bps / 10_000.0
    return price + adj if side == "BUY" else price - adj


def run_backtest(
    symbol: str,
    df: pd.DataFrame,
    strategy: Strategy,
    *,
    starting_cash: float = 100_000.0,
    risk: RiskConfig | None = None,
    commission_per_trade: float = 1.0,
    slippage_bps: float = 5.0,
    invest_fraction: float = 0.95,
) -> BacktestResult:
    """Backtest a single symbol. `df` must be OHLCV; strategy adds indicators."""
    risk = risk or RiskConfig()
    data = strategy.generate(df).copy()
    if "target" not in data or "atr" not in data:
        raise ValueError("Strategy.generate must produce 'target' and 'atr' columns")

    cash = starting_cash
    shares = 0
    entry_price = stop = take = 0.0
    bars_in_market = 0
    trades: list[dict] = []
    equity_curve: list[tuple[pd.Timestamp, float]] = []

    opens = data["open"].to_numpy()
    highs = data["high"].to_numpy()
    lows = data["low"].to_numpy()
    closes = data["close"].to_numpy()
    atrs = data["atr"].to_numpy()
    targets = data["target"].to_numpy()
    # Optional fractional sizing. Strategies that emit `target_weight` (e.g.
    # volatility targeting) size each entry at equity*weight; weight is
    # clamped to [0, 1] — this engine takes NO leverage, ever. Strategies
    # without the column fall back to the flat invest_fraction (unchanged
    # behavior, so existing strategies and tests are untouched).
    weights = (
        data["target_weight"].clip(lower=0.0, upper=1.0).to_numpy()
        if "target_weight" in data.columns
        else None
    )
    index = data.index

    def close_position(ts, exit_price: float, reason: str) -> None:
        nonlocal cash, shares, entry_price, stop, take
        proceeds = shares * exit_price - commission_per_trade
        pnl = proceeds - shares * entry_price
        trades[-1].update(
            exit_time=str(ts),
            exit_price=round(exit_price, 4),
            exit_reason=reason,
            pnl=round(pnl, 2),
            return_pct=round(exit_price / entry_price - 1.0, 6),
        )
        cash += proceeds
        shares = 0
        entry_price = stop = take = 0.0

    for i in range(len(data)):
        ts = index[i]
        price_now = closes[i]

        # --- manage an open position on this bar (stop/target intrabar) ---
        if shares > 0:
            bars_in_market += 1
            o, hi, lo = opens[i], highs[i], lows[i]
            exit_done = False
            # Stop first (pessimistic). Gap-through fills at the open.
            if lo <= stop:
                fill = min(o, stop) if o < stop else stop
                close_position(ts, _apply_slippage(fill, "SELL", slippage_bps), "stop")
                exit_done = True
            elif hi >= take:
                fill = max(o, take) if o > take else take
                close_position(ts, _apply_slippage(fill, "SELL", slippage_bps), "target")
                exit_done = True
            if exit_done:
                equity_curve.append((ts, cash))
                continue

        # --- act on the prior bar's signal at THIS bar's open ---
        if i > 0:
            desired = targets[i - 1]
            if shares > 0 and desired == 0:
                close_position(
                    ts, _apply_slippage(opens[i], "SELL", slippage_bps), "signal_exit"
                )
            elif shares == 0 and desired == 1:
                atr_val = atrs[i - 1]
                if atr_val and atr_val > 0:
                    fill = _apply_slippage(opens[i], "BUY", slippage_bps)
                    if weights is not None:
                        w = weights[i - 1]
                        # Flat at entry, so equity == cash; never exceed cash.
                        budget = min(cash * w, cash * invest_fraction)
                    else:
                        budget = cash * invest_fraction
                    qty = int(budget // fill)
                    if qty >= 1 and qty * fill <= cash - commission_per_trade:
                        cash -= qty * fill + commission_per_trade
                        shares = qty
                        entry_price = fill
                        stop = fill - risk.atr_stop_mult * atr_val
                        take = fill + risk.atr_target_mult * atr_val
                        trades.append(
                            {
                                "symbol": symbol,
                                "entry_time": str(ts),
                                "entry_price": round(fill, 4),
                                "shares": qty,
                                "stop": round(stop, 4),
                                "target": round(take, 4),
                                "exit_price": None,
                            }
                        )

        equity = cash + shares * price_now
        equity_curve.append((ts, equity))

    # Mark-to-market any position still open at the end.
    if shares > 0:
        close_position(index[-1], closes[-1], "end_of_data")
        equity_curve[-1] = (index[-1], cash)

    eq = pd.Series(
        [v for _, v in equity_curve], index=pd.DatetimeIndex([t for t, _ in equity_curve])
    )
    exposure = bars_in_market / len(data) if len(data) else 0.0
    metrics = compute_metrics(eq, trades, exposure)

    bench = (data["close"] / data["close"].iloc[0]) * starting_cash
    return BacktestResult(
        symbol=symbol,
        strategy=strategy.name,
        equity=eq,
        trades=trades,
        metrics=metrics,
        benchmark_equity=bench,
    )


def run_portfolio_backtest(
    bars_by_symbol: dict[str, pd.DataFrame],
    strategy_factory,
    *,
    starting_cash: float = 100_000.0,
    risk: RiskConfig | None = None,
    **kwargs,
) -> tuple[pd.Series, dict[str, BacktestResult]]:
    """Equal-capital, independent allocation across symbols.

    Each symbol gets starting_cash/N and is backtested independently; the
    portfolio equity curve is the date-aligned sum. This models a simple
    equal-weight book without cross-symbol cash competition (a deliberate
    simplification, documented rather than hidden).
    """
    symbols = list(bars_by_symbol)
    if not symbols:
        raise ValueError("No symbols to backtest")
    per_symbol_cash = starting_cash / len(symbols)

    results: dict[str, BacktestResult] = {}
    for sym, df in bars_by_symbol.items():
        results[sym] = run_backtest(
            sym,
            df,
            strategy_factory(),
            starting_cash=per_symbol_cash,
            risk=risk,
            **kwargs,
        )

    combined = None
    for res in results.values():
        combined = res.equity if combined is None else combined.add(res.equity, fill_value=None)
    combined = combined.dropna()
    return combined, results
