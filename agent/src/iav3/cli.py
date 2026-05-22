"""Command-line interface.

    iav3 backtest         --symbols AAPL,MSFT,SPY --strategy momentum --start 2018-01-01
    iav3 walk-forward     --symbols AAPL,SPY --strategy vol_target_trend --start 2015-01-01
    iav3 options-backtest --symbol AAPL --start 2015-01-01   # MODEL, not P&L
    iav3 options-chain    AAPL --dte 30 --type call          # live Alpaca chain
    iav3 paper            [--loop]
    iav3 dashboard        [--port 8501]                       # local Streamlit UI
    iav3 report
    iav3 strategies
    iav3 validate-env                                         # ping all APIs
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

# Load .env BEFORE any command function reads os.environ. python-dotenv walks
# upward from the CWD looking for a .env file, so this works whether the user
# runs iav3 from the repo root, the agent dir, or anywhere in between.
load_dotenv()

from .backtest import run_backtest, run_portfolio_backtest
from .backtest.metrics import compute_metrics
from .config import load_settings
from .data import YFinanceData
from .strategy import available_strategies, get_strategy

console = Console()


def _metrics_table(title: str, m) -> Table:
    t = Table(title=title)
    t.add_column("Metric", style="cyan")
    t.add_column("Value", justify="right")
    rows = [
        ("Period", f"{m.start} → {m.end} ({m.days}d)"),
        ("Total return", f"{m.total_return_pct:+.2f}%"),
        ("CAGR", f"{m.cagr_pct:+.2f}%"),
        ("Annual vol", f"{m.annual_vol_pct:.2f}%"),
        ("Sharpe", f"{m.sharpe:.2f}"),
        ("Sortino", f"{m.sortino:.2f}"),
        ("Max drawdown", f"{m.max_drawdown_pct:.2f}%"),
        ("Calmar", f"{m.calmar:.2f}"),
        ("Exposure", f"{m.exposure_pct:.1f}%"),
        ("Trades", str(m.num_trades)),
        ("Win rate", f"{m.win_rate_pct:.1f}%"),
        ("Avg win / loss", f"{m.avg_win_pct:+.2f}% / {m.avg_loss_pct:+.2f}%"),
        ("Profit factor", f"{m.profit_factor}"),
        ("Final equity", f"${m.final_equity:,.2f}"),
    ]
    for k, v in rows:
        t.add_row(k, v)
    return t


def cmd_backtest(args: argparse.Namespace) -> int:
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    feed = YFinanceData()
    console.print(
        f"[dim]Backtesting {args.strategy} on {', '.join(symbols)} "
        f"from {args.start}{' to ' + args.end if args.end else ''} "
        f"(${args.cash:,.0f}, slippage {args.slippage}bps)[/dim]\n"
    )

    bars = {}
    for sym in symbols:
        try:
            bars[sym] = feed.history(sym, start=args.start, end=args.end)
        except Exception as e:
            console.print(f"[red]{sym}: {e}[/red]")
    if not bars:
        console.print("[red]No usable data. Aborting.[/red]")
        return 1

    for sym, df in bars.items():
        res = run_backtest(
            sym, df, get_strategy(args.strategy),
            starting_cash=args.cash, slippage_bps=args.slippage,
        )
        console.print(_metrics_table(f"{sym} — {args.strategy}", res.metrics))
        bench_m = compute_metrics(res.benchmark_equity, [], 1.0)
        console.print(
            f"[dim]  buy & hold {sym}: {bench_m.total_return_pct:+.2f}% "
            f"total, {bench_m.max_drawdown_pct:.2f}% max DD[/dim]\n"
        )

    if len(bars) > 1:
        combined, results = run_portfolio_backtest(
            bars, lambda: get_strategy(args.strategy),
            starting_cash=args.cash, slippage_bps=args.slippage,
        )
        # Aggregate real trades + capital-weighted exposure across legs so the
        # portfolio row reports true trade stats (not 0/0, which was misleading).
        all_trades = [t for r in results.values() for t in r.trades]
        avg_exposure = sum(
            r.metrics.exposure_pct for r in results.values()
        ) / len(results) / 100.0
        pm = compute_metrics(combined, all_trades, avg_exposure)
        console.print(_metrics_table(
            f"PORTFOLIO (equal-weight, {len(bars)} symbols)", pm
        ))

    console.print(
        "\n[yellow]Past performance does not predict future results. "
        "Backtests omit some real-world frictions and survivorship effects.[/yellow]"
    )
    return 0


def cmd_paper(args: argparse.Namespace) -> int:
    import dataclasses

    settings = load_settings()
    if args.strategy:
        settings = dataclasses.replace(settings, strategy=args.strategy)
    from .engine import run_cycle

    if not args.loop:
        run_cycle(settings)
        return 0

    from apscheduler.schedulers.blocking import BlockingScheduler

    sched = BlockingScheduler()
    sched.add_job(
        lambda: run_cycle(settings),
        "interval",
        minutes=settings.cycle_interval_minutes,
        max_instances=1,
    )
    console.print(
        f"[green]Paper loop every {settings.cycle_interval_minutes}m. "
        f"Ctrl+C to stop.[/green]"
    )
    run_cycle(settings)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        console.print("\n[yellow]Stopped.[/yellow]")
    return 0


def cmd_report(_args: argparse.Namespace) -> int:
    from .portfolio import recent_sessions

    sessions = recent_sessions()
    if not sessions:
        console.print("No sessions recorded yet. Run `iav3 paper` first.")
        return 0
    t = Table(title="Recent Sessions")
    for col in ("ts_utc", "equity", "cash", "day_pnl_pct"):
        t.add_column(col)
    for s in sessions:
        t.add_row(
            s["ts_utc"][:19],
            f"${s['equity']:,.2f}",
            f"${s['cash']:,.2f}",
            f"{s['day_pnl_pct']:+.2f}%",
        )
    console.print(t)
    return 0


def cmd_options(args: argparse.Namespace) -> int:
    from .options import MODEL_CAVEAT, run_wheel_backtest

    console.print(f"[bold red]⚠  {MODEL_CAVEAT}[/bold red]\n")
    feed = YFinanceData()
    try:
        df = feed.history(args.symbol, start=args.start, end=args.end)
    except Exception as e:
        console.print(f"[red]{args.symbol}: {e}[/red]")
        return 1

    res = run_wheel_backtest(
        args.symbol, df, starting_cash=args.cash,
        iv_premium_mult=args.iv_mult, put_otm_pct=args.otm,
        call_otm_pct=args.otm,
    )
    t = Table(title=f"{args.symbol} — defined-risk wheel (MODEL)")
    t.add_column("Metric", style="cyan")
    t.add_column("Value", justify="right")
    for k, v in res.summary.items():
        t.add_row(k, str(v))
    console.print(t)
    console.print(
        f"\n[bold red]Reminder: {MODEL_CAVEAT}\n"
        f"IV multiplier used: {args.iv_mult} — change with --iv-mult to see how "
        f"sensitive the result is to this single unverifiable assumption.[/bold red]"
    )
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    import subprocess

    try:
        import streamlit  # noqa: F401
    except ImportError:
        console.print(
            "[red]Streamlit not installed.[/red] Install the dashboard extra:\n"
            '  pip install -e ".[dashboard]"'
        )
        return 1
    from .dashboard import app as _app

    app_path = _app.__file__
    console.print(
        f"[green]Launching dashboard on http://localhost:{args.port}[/green] "
        "(Ctrl+C to stop)"
    )
    return subprocess.call(
        [
            "streamlit", "run", app_path,
            "--server.port", str(args.port),
            "--server.headless", "true",
        ]
    )


def cmd_strategies(_args: argparse.Namespace) -> int:
    console.print("Available strategies: " + ", ".join(available_strategies()))
    return 0


def cmd_walk_forward(args: argparse.Namespace) -> int:
    """Rolling-origin walk-forward validation + promotion gate."""
    import json
    from datetime import date

    from .backtest import (
        PromotionGates,
        generate_windows,
        walk_forward,
    )

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        console.print("[red]No symbols.[/red]")
        return 1

    try:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end) if args.end else date.today()
    except ValueError as e:
        console.print(f"[red]Date parse error: {e}[/red]")
        return 1

    param_grid: dict[str, list] = {}
    if args.param_grid:
        try:
            param_grid = json.loads(args.param_grid)
        except json.JSONDecodeError as e:
            console.print(f"[red]--param-grid must be valid JSON: {e}[/red]")
            return 1

    feed = YFinanceData()
    bars: dict[str, "pd.DataFrame"] = {}
    for sym in symbols:
        try:
            bars[sym] = feed.history(sym, start=args.start, end=args.end)
        except Exception as e:
            console.print(f"[red]{sym}: {e}[/red]")
    if not bars:
        return 1

    windows = generate_windows(
        start, end,
        is_months=args.is_months,
        oos_months=args.oos_months,
        step_months=args.step_months,
        purge_days=args.purge_days,
    )
    if not windows:
        console.print(
            f"[red]No usable walk-forward windows in [{start}, {end}] "
            f"with is={args.is_months}m oos={args.oos_months}m step={args.step_months}m.[/red]"
        )
        return 1

    console.print(
        f"[dim]Walk-forward {args.strategy}: {len(symbols)} symbols × "
        f"{len(windows)} windows. Grid size: "
        f"{max(1, sum(1 for _ in _grid_iter(param_grid)))} param combos.[/dim]\n"
    )

    gates = PromotionGates(
        promotion_oos_sharpe=args.promo_sharpe,
        promotion_max_dd_pct=args.promo_max_dd,
        worst_window_sharpe_floor=args.promo_worst,
    )
    # Strategy classes accept kwargs in their __init__; pull the class off a
    # no-arg instance so the factory can construct per-grid-point instances
    # with the swept params.
    strategy_cls = type(get_strategy(args.strategy))
    result = walk_forward(
        strategy_factory=lambda **k: strategy_cls(**k),
        param_grid=param_grid,
        data=bars,
        windows=windows,
        gates=gates,
    )

    # Per-window table
    t = Table(title=f"{args.strategy} — walk-forward OOS results")
    for col in ("Symbol", "IS window", "OOS window", "IS Sharpe", "OOS Sharpe", "OOS Ret%", "OOS DD%"):
        t.add_column(col, justify="right" if col not in ("Symbol", "IS window", "OOS window") else "left")
    for r in result.windows:
        t.add_row(
            r.symbol,
            f"{r.window.is_start}..{r.window.is_end}",
            f"{r.window.oos_start}..{r.window.oos_end}",
            f"{r.is_sharpe:.2f}",
            f"{r.oos_sharpe:.2f}",
            f"{r.oos_return_pct:+.1f}",
            f"{r.oos_max_dd_pct:.1f}",
        )
    console.print(t)

    tone = "green" if result.promotion_eligible else "yellow"
    console.print(
        f"\n[bold {tone}]{'PROMOTION ELIGIBLE' if result.promotion_eligible else 'PROMOTION BLOCKED'}[/bold {tone}]\n"
        f"  {result.rationale}\n"
        f"  aggregate OOS Sharpe = {result.aggregate_oos_sharpe:.2f}\n"
        f"  max OOS drawdown    = {result.aggregate_oos_max_dd_pct:.1f}%\n"
        f"  worst window Sharpe = {result.worst_oos_sharpe:.2f}"
    )
    return 0 if result.promotion_eligible else 2


def _grid_iter(grid):
    from .backtest import iter_param_grid
    return iter_param_grid(grid)


def cmd_options_chain(args: argparse.Namespace) -> int:
    """Print today's option chain near the money — sanity check Alpaca wiring."""
    try:
        from .data import AlpacaOptionsFeed
        feed = AlpacaOptionsFeed()
    except Exception as e:
        console.print(f"[red]Alpaca options feed unavailable: {e}[/red]")
        return 1

    sym = args.symbol.upper()

    try:
        expiry = feed.nearest_expiry(sym, args.dte)
    except Exception as e:
        console.print(f"[red]No expiries for {sym}: {e}[/red]")
        return 1

    try:
        # Get the underlying spot via a chain probe — we don't have a price feed
        # in this command (cheap shortcut: just pull a wide chain and infer from
        # the most-traded strike). For a phase-1 sanity check, take the chain as-is.
        chain = feed.chain(sym, expiry=expiry, option_type=args.type)
    except Exception as e:
        console.print(f"[red]Chain fetch failed: {e}[/red]")
        return 1

    if not chain:
        console.print(f"[yellow]Empty chain for {sym} {expiry} {args.type}.[/yellow]")
        return 0

    # Sort by strike
    chain = sorted(chain, key=lambda q: q.contract.strike)

    t = Table(title=f"{sym} {args.type.upper()} chain — exp {expiry} ({(expiry - args.as_of_date()).days}d)")
    for col in ("Strike", "Bid", "Ask", "Mid", "IV", "Δ", "Volume", "OI"):
        t.add_column(col, justify="right")
    for q in chain[: args.limit]:
        t.add_row(
            f"${q.contract.strike:.2f}",
            f"${q.bid:.2f}",
            f"${q.ask:.2f}",
            f"${q.mid:.2f}",
            f"{(q.iv or 0) * 100:.0f}%" if q.iv is not None else "—",
            f"{q.delta:+.2f}" if q.delta is not None else "—",
            str(q.volume),
            str(q.open_interest),
        )
    console.print(t)
    console.print(
        f"[dim]Showing {min(len(chain), args.limit)} of {len(chain)} strikes. "
        f"Use --limit to widen.[/dim]"
    )
    return 0


def cmd_validate_env(_args: argparse.Namespace) -> int:
    """Ping every external API and report PASS/FAIL for each."""
    import os

    results: list[tuple[str, bool, str]] = []

    # Alpaca
    try:
        import requests
        key = os.environ.get("ALPACA_API_KEY")
        sec = os.environ.get("ALPACA_API_SECRET")
        url = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        if not key or not sec:
            results.append(("Alpaca", False, "ALPACA_API_KEY/SECRET not set"))
        else:
            r = requests.get(
                f"{url}/v2/account",
                headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec},
                timeout=10,
            )
            if r.status_code == 200:
                j = r.json()
                results.append((
                    "Alpaca", True,
                    f"ACTIVE, equity ${float(j.get('equity', 0)):,.2f}, "
                    f"options level {j.get('options_approved_level', '?')}",
                ))
            else:
                results.append(("Alpaca", False, f"HTTP {r.status_code}"))
    except Exception as e:
        results.append(("Alpaca", False, str(e)))

    # Finnhub
    try:
        import requests
        key = os.environ.get("FINNHUB_API_KEY")
        if not key:
            results.append(("Finnhub", False, "FINNHUB_API_KEY not set"))
        else:
            r = requests.get(
                f"https://finnhub.io/api/v1/quote?symbol=AAPL&token={key}",
                timeout=10,
            )
            if r.status_code == 200 and isinstance(r.json(), dict) and "c" in r.json():
                results.append(("Finnhub", True, f"AAPL spot ${r.json()['c']:.2f}"))
            else:
                results.append(("Finnhub", False, f"HTTP {r.status_code}"))
    except Exception as e:
        results.append(("Finnhub", False, str(e)))

    # Anthropic
    try:
        import requests
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            results.append(("Anthropic", False, "ANTHROPIC_API_KEY not set"))
        else:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5",
                    "max_tokens": 5,
                    "messages": [{"role": "user", "content": "OK"}],
                },
                timeout=10,
            )
            results.append(("Anthropic", r.status_code == 200, f"HTTP {r.status_code}"))
    except Exception as e:
        results.append(("Anthropic", False, str(e)))

    # Neon
    try:
        if not os.environ.get("NEON_DATABASE_URL"):
            results.append(("Neon", False, "NEON_DATABASE_URL not set (dashboard offline)"))
        else:
            from .db import NeonJournal
            with NeonJournal() as nj:
                pong = nj.ping()
                schema = nj.schema_applied()
                if pong and schema:
                    results.append(("Neon", True, "connected; schema applied"))
                elif pong:
                    results.append(("Neon", False, "connected but schema not applied (run `make db-migrate`)"))
                else:
                    results.append(("Neon", False, "ping failed"))
    except Exception as e:
        results.append(("Neon", False, str(e)))

    t = Table(title="External API health")
    t.add_column("Service", style="cyan")
    t.add_column("Status")
    t.add_column("Detail", style="dim")
    all_pass = True
    for name, ok, detail in results:
        if not ok:
            all_pass = False
        t.add_row(name, "[green]PASS[/green]" if ok else "[red]FAIL[/red]", detail)
    console.print(t)
    return 0 if all_pass else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="iav3", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backtest", help="Validate a strategy on historical data")
    b.add_argument("--symbols", default="AAPL,MSFT,NVDA,SPY")
    b.add_argument("--strategy", default="momentum")
    b.add_argument("--start", default="2018-01-01")
    b.add_argument("--end", default=None)
    b.add_argument("--cash", type=float, default=100_000.0)
    b.add_argument("--slippage", type=float, default=5.0, help="bps per fill")
    b.set_defaults(func=cmd_backtest)

    o = sub.add_parser(
        "options-backtest",
        help="MODEL (not P&L) defined-risk wheel — see the caveat it prints",
    )
    o.add_argument("--symbol", default="AAPL")
    o.add_argument("--start", default="2015-01-01")
    o.add_argument("--end", default=None)
    o.add_argument("--cash", type=float, default=100_000.0)
    o.add_argument("--otm", type=float, default=0.05, help="strike %% OTM")
    o.add_argument(
        "--iv-mult", dest="iv_mult", type=float, default=1.15,
        help="implied vol = realized_vol * this (the key unverifiable assumption)",
    )
    o.set_defaults(func=cmd_options)

    pa = sub.add_parser("paper", help="Run paper trading cycle(s)")
    pa.add_argument("--strategy", default=None)
    pa.add_argument("--loop", action="store_true", help="Run on a schedule")
    pa.set_defaults(func=cmd_paper)

    d = sub.add_parser("dashboard", help="Launch the Streamlit web dashboard")
    d.add_argument("--port", type=int, default=8501)
    d.set_defaults(func=cmd_dashboard)

    r = sub.add_parser("report", help="Show recent session journal")
    r.set_defaults(func=cmd_report)

    s = sub.add_parser("strategies", help="List available strategies")
    s.set_defaults(func=cmd_strategies)

    wf = sub.add_parser(
        "walk-forward",
        help="Walk-forward validation + promotion gate (rolling-origin)",
    )
    wf.add_argument("--symbols", default="AAPL,MSFT,NVDA,SPY")
    wf.add_argument("--strategy", default="vol_target_trend")
    wf.add_argument("--start", default="2015-01-01")
    wf.add_argument("--end", default=None)
    wf.add_argument("--is-months", dest="is_months", type=int, default=36)
    wf.add_argument("--oos-months", dest="oos_months", type=int, default=6)
    wf.add_argument("--step-months", dest="step_months", type=int, default=6)
    wf.add_argument("--purge-days", dest="purge_days", type=int, default=0)
    wf.add_argument(
        "--param-grid", dest="param_grid", default=None,
        help='JSON dict of param->[values], e.g. \'{"fast":[10,20],"slow":[80,120]}\'',
    )
    wf.add_argument("--promo-sharpe", dest="promo_sharpe", type=float, default=0.7)
    wf.add_argument("--promo-max-dd", dest="promo_max_dd", type=float, default=25.0)
    wf.add_argument("--promo-worst", dest="promo_worst", type=float, default=-0.5)
    wf.set_defaults(func=cmd_walk_forward)

    oc = sub.add_parser(
        "options-chain",
        help="Live Alpaca option chain for a symbol (sanity check)",
    )
    oc.add_argument("symbol")
    oc.add_argument("--dte", type=int, default=30, help="target days to expiry")
    oc.add_argument("--type", choices=["call", "put"], default="call")
    oc.add_argument("--limit", type=int, default=40, help="rows to display")
    oc.set_defaults(func=cmd_options_chain)
    # Convenience: a callable on the namespace so cmd_options_chain can get today
    # without re-importing datetime. argparse doesn't natively support this so we
    # attach via set_defaults instead.
    from datetime import date as _date
    oc.set_defaults(as_of_date=lambda: _date.today())

    ve = sub.add_parser(
        "validate-env",
        help="Ping Alpaca, Finnhub, Anthropic, and Neon; report PASS/FAIL each",
    )
    ve.set_defaults(func=cmd_validate_env)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
