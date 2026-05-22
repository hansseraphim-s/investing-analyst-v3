"""Live/paper trading engine.

One cycle = data -> deterministic signal -> risk check -> bracket order.
No LLM in this path. Long/flat only, ATR-bracketed entries. The same code
runs against PaperBroker (no keys) or AlpacaBroker (paper or live).

v3 dual-write: every cycle writes to BOTH the local SQLite journal
(for risk-layer reads like trades_today, which is hot-path) AND to
Neon Postgres (for the dashboard). Neon writes are best-effort — any
exception is logged and the cycle continues. The SQLite journal is the
authority for risk checks; Neon is for observability.

Neon writes are enabled when NEON_DATABASE_URL is set in the environment.
With it unset, the engine runs in single-write mode (SQLite only) and
the dashboard simply shows 'no data yet' — a clean degraded state.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from rich.console import Console

from .broker import PaperBroker, get_broker
from .config import Settings
from .data import YFinanceData
from .portfolio import init_journal, record_order, record_session, trades_today
from .risk import PortfolioView, Position, pre_trade_check
from .strategy import get_strategy

logger = logging.getLogger(__name__)
console = Console()

AGENT_VERSION = "0.1.0"


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


def _maybe_open_neon_session(
    *,
    trading_mode: str,
    strategy_name: str,
    equity_start: float,
    log,
):
    """Open a Neon session if NEON_DATABASE_URL is set; else return (None, None).

    Failure is non-fatal — the cycle continues with SQLite-only journal.
    """
    if not os.environ.get("NEON_DATABASE_URL"):
        return None, None
    try:
        from .db import NeonJournal

        neon = NeonJournal()
        session_id = neon.open_session(
            trading_mode=trading_mode,
            strategy=strategy_name,
            equity_start=equity_start,
            agent_version=AGENT_VERSION,
        )
        return neon, session_id
    except Exception as e:
        log(f"  [yellow]Neon journal unavailable: {e}[/yellow]")
        return None, None


def _safe_neon(call, log, what: str) -> None:
    """Wrap a Neon write with non-fatal exception handling."""
    try:
        call()
    except Exception as e:
        log(f"  [yellow]Neon {what} failed: {e}[/yellow]")


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

    # Open Neon session up front so we can stamp every write with session_id.
    acct = broker.get_account()
    neon, session_id = _maybe_open_neon_session(
        trading_mode=settings.trading_mode,
        strategy_name=strategy.name,
        equity_start=acct.equity,
        log=log,
    )

    if not broker.is_market_open():
        log("[yellow]Market closed (weekend/holiday/after-hours) — no actions.[/yellow]")
        return _finish(broker, lines, settings, neon, session_id)

    pv = _portfolio_view(broker, settings)
    held = {p.symbol for p in pv.positions}
    alloc = pv.equity / max(len(settings.watchlist), 1)
    latest_prices: dict[str, float] = {}
    start = (datetime.now(timezone.utc) - timedelta(days=800)).date().isoformat()

    for symbol in settings.watchlist:
        try:
            hist = feed.history(symbol, start=start)
        except Exception as e:
            log(f"  {symbol}: data error — skipped ({e})")
            continue

        enriched = strategy.generate(hist)
        last = enriched.iloc[-1]
        target = int(last["target"])
        atr_val = float(last["atr"]) if last["atr"] == last["atr"] else 0.0
        price = float(last["close"])
        latest_prices[symbol] = price

        rvol = (
            float(last.get("realized_vol", 0.0) or 0.0)
            if hasattr(last, "get") or "realized_vol" in enriched.columns
            else None
        )

        if neon and session_id is not None:
            _safe_neon(
                lambda s=symbol, t=target, p=price, a=atr_val, r=rvol: neon.record_signal(
                    session_id=session_id,
                    symbol=s,
                    strategy=strategy.name,
                    target=t,
                    price=p,
                    atr=a if a > 0 else None,
                    realized_vol=r if r else None,
                ),
                log,
                f"signal({symbol})",
            )

        if symbol in held and target == 0:
            broker.close_position(symbol)
            record_order(symbol, "SELL", pv.owns(symbol).qty, price, 0, 0,
                         "filled", "signal_exit")
            if neon and session_id is not None:
                _safe_neon(
                    lambda s=symbol, q=pv.owns(s).qty, p=price: neon.record_order(
                        session_id=session_id,
                        symbol=s, asset_class="equity", side="SELL",
                        qty=int(q), price=p, status="filled", reason="signal_exit",
                    ),
                    log,
                    f"order_exit({symbol})",
                )
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
                if neon and session_id is not None:
                    _safe_neon(
                        lambda s=symbol, q=qty, p=price, st=stop, tk=take, r=decision.check: neon.record_order(
                            session_id=session_id,
                            symbol=s, asset_class="equity", side="BUY",
                            qty=q, price=p, stop_price=st, take_profit=tk,
                            status="blocked", reason=r,
                        ),
                        log,
                        f"order_blocked({symbol})",
                    )
                continue
            broker.submit_bracket_order(symbol, qty, "BUY", price, stop, take)
            record_order(symbol, "BUY", qty, price, stop, take, "filled", "entry")
            if neon and session_id is not None:
                _safe_neon(
                    lambda s=symbol, q=qty, p=price, st=stop, tk=take: neon.record_order(
                        session_id=session_id,
                        symbol=s, asset_class="equity", side="BUY",
                        qty=q, price=p, stop_price=st, take_profit=tk,
                        status="filled", reason="entry",
                    ),
                    log,
                    f"order_entry({symbol})",
                )
            log(f"  [green]ENTRY[/green] {symbol} {qty}@${price:.2f} "
                f"stop ${stop:.2f} / target ${take:.2f}")
        else:
            log(f"  HOLD {symbol} (target={target}, "
                f"{'in position' if symbol in held else 'flat'})")

    # Paper broker: evaluate brackets against the latest prices.
    if isinstance(broker, PaperBroker) and latest_prices:
        for sym in broker.mark(latest_prices):
            log(f"  [magenta]BRACKET HIT[/magenta] {sym} auto-closed")

    # Snapshot current positions to Neon (post any auto-closes).
    if neon and session_id is not None:
        try:
            for p in broker.get_positions():
                _safe_neon(
                    lambda pos=p: neon.record_position_snapshot(
                        session_id=session_id,
                        symbol=pos.symbol, asset_class="equity",
                        qty=float(pos.qty), market_value=float(pos.market_value),
                    ),
                    log,
                    f"position({p.symbol})",
                )
        except Exception as e:
            log(f"  [yellow]Neon position snapshot loop failed: {e}[/yellow]")

    return _finish(broker, lines, settings, neon, session_id)


def _finish(broker, lines: list[str], settings: Settings, neon, session_id) -> str:
    acct = broker.get_account()
    summary = "\n".join(lines)
    record_session(acct.equity, acct.cash, acct.day_pnl_pct, summary)

    advisor_review: str | None = None
    if settings.enable_advisor and settings.anthropic_api_key:
        from .advisor import ClaudeAdvisor

        advisor_review = ClaudeAdvisor(settings.anthropic_api_key).review(summary)
        console.print(f"\n[dim]Advisor:[/dim] {advisor_review}")
        summary += f"\n\n[advisor]\n{advisor_review}"

    if neon and session_id is not None:
        try:
            neon.record_equity_point(
                session_id=session_id,
                equity=acct.equity, cash=acct.cash,
            )
            neon.close_session(
                session_id,
                equity_end=acct.equity, cash_end=acct.cash,
                day_pnl_pct=acct.day_pnl_pct,
                summary=summary, advisor_review=advisor_review,
            )
        except Exception as e:
            console.print(f"[yellow]Neon close-session failed: {e}[/yellow]")
        finally:
            try:
                neon.close()
            except Exception:
                pass

    console.print(
        f"\n[bold]Equity ${acct.equity:,.2f}[/bold] · "
        f"cash ${acct.cash:,.2f} · day P&L {acct.day_pnl_pct:+.2f}%"
    )
    return summary
