"""Command-line interface.

    iav3 backtest        --symbols AAPL,MSFT,SPY --strategy momentum --start 2018-01-01
    iav3 options-backtest --symbol AAPL --start 2015-01-01   # MODEL, not P&L
    iav3 dashboard       [--port 8501]                       # web UI
    iav3 paper           [--loop]
    iav3 report
    iav3 strategies
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.table import Table

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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
