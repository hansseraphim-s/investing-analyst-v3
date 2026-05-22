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

import re
from dataclasses import dataclass

from .broker import PaperBroker, get_broker
from .broker.base import BrokerPosition
from .config import Settings
from .data import YFinanceData
from .greeks.aggregator import aggregate_greeks
from .portfolio import (
    init_journal,
    portfolio_returns_last_n,
    record_order,
    record_session,
    trades_today,
)
from .risk import PortfolioView, Position, pre_trade_check
from .strategy import get_strategy

# OCC option-symbol pattern; used to classify broker positions for Greeks aggregation.
_OCC_PATTERN = re.compile(r"^[A-Z]+\d{6}[CP]\d{8}$")


@dataclass(frozen=True)
class _GreeksPosition:
    """Greeks-aggregator-compatible view of a broker position.

    The broker layer returns BrokerPosition (symbol, qty, prices) without
    distinguishing equity vs option. This adapter classifies by OCC
    symbol shape and exposes the fields aggregate_greeks expects.
    """
    symbol: str
    asset_class: str
    qty: float
    market_value: float


def _to_greeks_positions(broker_positions) -> list[_GreeksPosition]:
    out = []
    for p in broker_positions:
        is_option = bool(_OCC_PATTERN.match(p.symbol))
        out.append(_GreeksPosition(
            symbol=p.symbol,
            asset_class="option" if is_option else "equity",
            qty=float(p.qty),
            market_value=float(p.market_value),
        ))
    return out


def _projected_greeks(
    broker,
    *,
    new_symbol: str,
    new_qty: int,
    new_price: float,
    equity: float,
    cash: float,
):
    """Aggregate current portfolio Greeks plus a proposed new equity BUY.

    For equity-only books this is essentially `current_delta + new_qty`.
    When options join the book (phase 3.3 wiring), the option_quotes dict
    needs to be populated from the AlpacaOptionsFeed; for now it's empty
    and option positions contribute zero Greeks (but still count notional).
    """
    positions = _to_greeks_positions(broker.get_positions())
    positions.append(_GreeksPosition(
        symbol=new_symbol,
        asset_class="option" if bool(_OCC_PATTERN.match(new_symbol)) else "equity",
        qty=float(new_qty),
        market_value=float(new_qty) * float(new_price),
    ))
    return aggregate_greeks(
        positions=positions,
        option_quotes={},
        equity=equity,
        cash=max(cash - new_qty * new_price, 0.0),
    )

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

    # Dynamic universe scan (optional). When ENABLE_DYNAMIC_SCAN=true the
    # engine fetches ~4,500 Alpaca-tradeable equities, ranks by multi-factor
    # composite z-score (20d + 60d momentum + trend + vol confirmation), and
    # uses the top-N as THIS CYCLE's watchlist instead of the static
    # WATCHLIST from .env. Adds ~30-60 seconds to cycle wall-clock time.
    if os.environ.get("ENABLE_DYNAMIC_SCAN", "false").lower() == "true":
        try:
            from .data import fetch_us_equity_universe, scan_top_n
            top_n = int(os.environ.get("SCAN_TOP_N", "100"))
            log(f"[dim]Dynamic scan: fetching universe + ranking top {top_n}…[/dim]")
            uni = fetch_us_equity_universe()
            top = scan_top_n(uni, top_n=top_n)
            if top:
                import dataclasses
                dynamic_watchlist = [r.symbol for r in top]
                settings = dataclasses.replace(settings, watchlist=dynamic_watchlist)
                log(f"[dim]Scan picked {len(dynamic_watchlist)} symbols; top 5: "
                    f"{', '.join(r.symbol for r in top[:5])}[/dim]")
            else:
                log("[yellow]Scan returned 0 symbols; falling back to static watchlist[/yellow]")
        except Exception as e:
            log(f"[yellow]Scan failed: {e}; falling back to static watchlist[/yellow]")

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
            # Phase 2/3 gates: CVaR (regime-adaptive daily-loss) + portfolio Greeks.
            # Equity-only book today, so option_quotes is empty inside the projected
            # Greeks aggregation; when wheel_live / long_call_overlay actually place
            # option orders, the projection here will need to include the new option
            # too — but for the equity BUY path we're in right now, this is correct.
            returns_history = portfolio_returns_last_n(252)
            projected_greeks = _projected_greeks(
                broker,
                new_symbol=symbol, new_qty=qty, new_price=price,
                equity=pv.equity, cash=pv.cash,
            )
            decision = pre_trade_check(
                "BUY", symbol, qty, price, pv, settings.risk,
                returns_history=returns_history,
                projected_greeks=projected_greeks,
                greeks_limits=settings.risk.greeks_limits(),
            )
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

        # ---- options overlay (live submission) -----------------------------
        # When ENABLE_OPTIONS_OVERLAY=true AND the trend filter is ON for this
        # symbol, consult LongCallOverlay AND actually submit the order via
        # broker.submit_option_order. The overlay's own gates (IV rank, DTE,
        # liquidity, sizing) run first; the broker then attempts the limit
        # order at mid. PaperBroker raises NotImplementedError — only the
        # Alpaca-backed paper/live path supports option orders.
        if settings.enable_options_overlay and target == 1:
            try:
                from .data import AlpacaOptionsFeed
                from .options import LongCallOverlay, LongCallOverlayConfig

                # Overlay config from env so the aggressive stack can tune
                # IV-rank ceiling + sizing without code changes
                overlay_cfg = LongCallOverlayConfig(
                    iv_rank_max=float(os.environ.get("OVERLAY_IV_RANK_MAX", "30")),
                    overlay_pct_of_equity=float(os.environ.get("OVERLAY_PCT_OF_EQUITY", "0.05")),
                    target_dte=int(os.environ.get("OVERLAY_TARGET_DTE", "75")),
                    target_delta=float(os.environ.get("OVERLAY_TARGET_DELTA", "0.50")),
                )
                overlay = LongCallOverlay(AlpacaOptionsFeed(), config=overlay_cfg)
                oc_decision = overlay.decide(
                    underlying=symbol, spot=price, trend_on=True,
                    available_cash=pv.cash, equity=pv.equity,
                )
                if oc_decision.is_opening and oc_decision.contract and oc_decision.quote:
                    contract = oc_decision.contract
                    quote = oc_decision.quote
                    # Limit at mid. Tighter than buying at ask, still likely to
                    # fill in liquid names; if it doesn't fill, no harm done
                    # (DAY TIF, order auto-cancels at close).
                    try:
                        broker_result = broker.submit_option_order(
                            symbol=contract.symbol,
                            qty=oc_decision.contracts,
                            side="BUY",
                            limit_price=round(quote.mid, 2),
                        )
                        status = broker_result.status
                        broker_order_id = broker_result.order_id
                        log(f"  [cyan]OVERLAY[/cyan] {symbol}: BUY "
                            f"{oc_decision.contracts}x {contract.symbol} @ "
                            f"${quote.mid:.2f} (limit) — order {broker_order_id}, "
                            f"status {status}")
                    except NotImplementedError:
                        # PaperBroker path — log only, don't fail the cycle
                        status = "blocked_paper_broker"
                        broker_order_id = None
                        log(f"  [yellow]OVERLAY[/yellow] {symbol}: would buy "
                            f"{oc_decision.contracts}x calls but in-process "
                            f"PaperBroker doesn't support options. Use Alpaca.")
                    except Exception as oe:
                        status = "rejected"
                        broker_order_id = None
                        log(f"  [red]OVERLAY[/red] {symbol}: option order "
                            f"rejected — {oe}")

                    # Always persist the attempt to Neon for audit
                    if neon and session_id is not None:
                        _safe_neon(
                            lambda r=oc_decision.rationale, st=status,
                                bid=broker_order_id, c=contract, q=quote,
                                n=oc_decision.contracts: neon.record_order(
                                session_id=session_id,
                                symbol=symbol, asset_class="option", side="BUY",
                                option_type="call",
                                strike=c.strike, expiry=c.expiry,
                                qty=n, price=q.mid,
                                status=st, reason="long_call_overlay",
                                broker_order_id=bid,
                                advisor_rationale=r,
                            ),
                            log,
                            f"overlay_record({symbol})",
                        )
                elif oc_decision.is_block:
                    log(f"  [dim]overlay {symbol}: {oc_decision.action} — "
                        f"{oc_decision.rationale}[/dim]")
            except Exception as e:
                log(f"  [yellow]overlay({symbol}) failed: {e}[/yellow]")

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
