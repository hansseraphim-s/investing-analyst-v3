"""Live/paper trading engine.

One cycle = data -> deterministic signal -> risk check -> bracket order.
No LLM in this path. Long/flat only, ATR-bracketed entries. The same code
runs against PaperBroker (no keys) or AlpacaBroker (paper or live).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rich.console import Console

from .broker import PaperBroker, get_broker
from .config import Settings
from .data import YFinanceData
from .portfolio import init_journal, record_order, record_session, trades_today
from .risk import PortfolioView, Position, pre_trade_check
from .strategy import get_strategy

console = Console()


def _portfolio_view(broker, settings: Settings) -> PortfolioView:
    acct = broker.get_account()
    positions = tuple(
        Position(p.symbol, p.qty, p.market_value) for p in broker.get_positions()
    )
    return PortfolioView(
        equity=acct.equity,
        cash=acct.cash,
        day_pnl_pct=acct.day_pnl_pct,
        positions=positions,
        trades_today=trades_today(),
    )


def run_cycle(settings: Settings, *, verbose: bool = True) -> str:
    init_journal()
    broker = get_broker(settings)
    feed = YFinanceData()
    strategy = get_strategy(settings.strategy)

    lines: list[str] = []

    def log(msg: str) -> None:
        lines.append(msg)
        if verbose:
            console.print(msg)

    log(f"[bold]Cycle {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}[/bold] "
        f"· mode={settings.trading_mode} · strategy={strategy.name}")

    if not broker.is_market_open():
        log("[yellow]Market closed (weekend/holiday/after-hours) — no actions.[/yellow]")
        return _finish(broker, lines, settings)

    pv = _portfolio_view(broker, settings)
    held = {p.symbol for p in pv.positions}
    alloc = pv.equity / max(len(settings.watchlist), 1)
    latest_prices: dict[str, float] = {}
    start = (datetime.now(timezone.utc) - timedelta(days=800)).date().isoformat()

    for symbol in settings.watchlist:
        try:
            hist = feed.history(symbol, start=start)
        except Exception as e:  # data outage on one symbol shouldn't kill the cycle
            log(f"  {symbol}: data error — skipped ({e})")
            continue

        enriched = strategy.generate(hist)
        last = enriched.iloc[-1]
        target = int(last["target"])
        atr_val = float(last["atr"]) if last["atr"] == last["atr"] else 0.0
        price = float(last["close"])
        latest_prices[symbol] = price

        if symbol in held and target == 0:
            broker.close_position(symbol)
            record_order(symbol, "SELL", pv.owns(symbol).qty, price, 0, 0,
                         "filled", "signal_exit")
            log(f"  [red]EXIT[/red] {symbol} @ ~${price:.2f} (signal flat)")
            continue

        if symbol not in held and target == 1:
            if atr_val <= 0:
                log(f"  {symbol}: target=1 but ATR unavailable — skipped")
                continue
            qty = int((alloc * 0.95) // price)
            if qty < 1:
                log(f"  {symbol}: allocation ${alloc:,.0f} < 1 share — skipped")
                continue
            stop = price - settings.risk.atr_stop_mult * atr_val
            take = price + settings.risk.atr_target_mult * atr_val
            decision = pre_trade_check("BUY", symbol, qty, price, pv, settings.risk)
            if not decision.approved:
                log(f"  [yellow]BLOCK[/yellow] {symbol}: {decision.check} — {decision.reason}")
                record_order(symbol, "BUY", qty, price, stop, take,
                             "blocked", decision.check)
                continue
            broker.submit_bracket_order(symbol, qty, "BUY", price, stop, take)
            record_order(symbol, "BUY", qty, price, stop, take, "filled", "entry")
            log(f"  [green]ENTRY[/green] {symbol} {qty}@${price:.2f} "
                f"stop ${stop:.2f} / target ${take:.2f}")
        else:
            log(f"  HOLD {symbol} (target={target}, "
                f"{'in position' if symbol in held else 'flat'})")

    # Paper broker: evaluate brackets against the latest prices.
    if isinstance(broker, PaperBroker) and latest_prices:
        for sym in broker.mark(latest_prices):
            log(f"  [magenta]BRACKET HIT[/magenta] {sym} auto-closed")

    return _finish(broker, lines, settings)


def _finish(broker, lines: list[str], settings: Settings) -> str:
    acct = broker.get_account()
    summary = "\n".join(lines)
    record_session(acct.equity, acct.cash, acct.day_pnl_pct, summary)

    if settings.enable_advisor and settings.anthropic_api_key:
        from .advisor import ClaudeAdvisor

        review = ClaudeAdvisor(settings.anthropic_api_key).review(summary)
        console.print(f"\n[dim]Advisor:[/dim] {review}")
        summary += f"\n\n[advisor]\n{review}"

    console.print(
        f"\n[bold]Equity ${acct.equity:,.2f}[/bold] · "
        f"cash ${acct.cash:,.2f} · day P&L {acct.day_pnl_pct:+.2f}%"
    )
    return summary
