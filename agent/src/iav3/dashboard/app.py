"""Streamlit dashboard — `iav3 dashboard`.

Thin view over dashboard/data.py and the backtest engine. Two tabs:

  * Live / Paper — tracks the running agent from its journal (graceful when
    there is no history yet).
  * Backtest — always works with zero keys and zero history, so you can
    evaluate any strategy immediately.

Honesty is built into the layout: every performance number sits next to a
buy-&-hold benchmark and a drawdown chart, the PAPER/LIVE state is shown in
the header, and a not-financial-advice caveat is always visible.
"""

from __future__ import annotations

import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ..backtest import run_backtest
from ..backtest.metrics import compute_metrics
from ..config import load_settings
from ..data import YFinanceData
from ..strategy import available_strategies, get_strategy
from .data import (
    align_benchmark,
    drawdown_series,
    kpi_summary,
    load_orders,
    load_session_equity,
)

CAVEAT = (
    "Not financial advice. Past performance does not predict future results. "
    "No strategy guarantees profit; trading risks loss of capital."
)


def _equity_chart(df: pd.DataFrame, title: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=df.index, y=df["strategy"], name="Agent", line=dict(width=2))
    )
    if "benchmark" in df and df["benchmark"].notna().any():
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["benchmark"],
                name="Buy & hold (SPY)",
                line=dict(width=1.5, dash="dot"),
            )
        )
    fig.update_layout(
        title=title, height=380, margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", y=1.12), yaxis_title="Equity ($)",
    )
    return fig


def _drawdown_chart(equity: pd.Series) -> go.Figure:
    dd = drawdown_series(equity) * 100.0
    fig = go.Figure(
        go.Scatter(x=dd.index, y=dd, fill="tozeroy", line=dict(color="#d62728"))
    )
    fig.update_layout(
        title="Drawdown (%)", height=240, margin=dict(l=10, r=10, t=40, b=10),
        yaxis_title="% from peak",
    )
    return fig


def _kpi_row(summary: dict) -> None:
    if summary.get("status") != "ok":
        st.info(summary.get("note", "Not enough history yet."))
        return
    c = st.columns(5)
    c[0].metric("Total return", f"{summary['total_return_pct']:+.2f}%")
    c[1].metric("CAGR", f"{summary['cagr_pct']:+.2f}%")
    c[2].metric("Sharpe", f"{summary['sharpe']:.2f}")
    c[3].metric("Max drawdown", f"{summary['max_drawdown_pct']:.2f}%")
    delta = summary.get("vs_benchmark_pct")
    c[4].metric(
        "vs buy & hold",
        f"{delta:+.2f}%" if delta is not None else "n/a",
        help="Agent total return minus SPY buy-&-hold over the same window. "
        "Negative means a passive index did better.",
    )


def _live_tab(settings) -> None:
    equity = load_session_equity()
    bench = None
    if not equity.empty:
        try:
            spy = YFinanceData().history(
                "SPY", start=str(equity.index[0].date())
            )["close"]
            aligned = align_benchmark(equity, spy)
            bench = aligned["benchmark"]
        except Exception:
            aligned = align_benchmark(equity, pd.Series(dtype=float))
    else:
        aligned = align_benchmark(equity, pd.Series(dtype=float))

    _kpi_row(kpi_summary(equity, bench))

    if equity.empty:
        st.warning(
            "No session history yet. Start the agent with "
            "`iav3 paper` (or `iav3 paper --loop`), then refresh. "
            "Meanwhile, use the **Backtest** tab."
        )
    else:
        st.plotly_chart(
            _equity_chart(aligned, "Agent equity vs buy & hold"),
            use_container_width=True,
        )
        st.plotly_chart(_drawdown_chart(equity), use_container_width=True)

    st.subheader("Open positions")
    shown = False
    has_keys = bool(settings.alpaca_api_key and settings.alpaca_secret_key)
    if has_keys or os.path.exists("paper_state.db"):
        try:
            from ..broker import get_broker

            broker = get_broker(settings)
            pos = broker.get_positions()
            if pos:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "symbol": p.symbol,
                                "qty": p.qty,
                                "avg_price": round(p.avg_price, 2),
                                "price": round(p.current_price, 2),
                                "mkt_value": round(p.market_value, 2),
                            }
                            for p in pos
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
                shown = True
        except Exception as e:
            st.caption(f"Broker not reachable: {e}")
    if not shown:
        st.caption("No open positions / no broker state yet.")

    st.subheader("Trade journal")
    orders = load_orders()
    if orders.empty:
        st.caption("No orders recorded yet.")
    else:
        st.dataframe(orders, use_container_width=True, hide_index=True)


def _backtest_tab() -> None:
    st.caption(
        "Runs on free yfinance data — no keys or live history needed. "
        "Conservative fills; long/flat only."
    )
    col1, col2 = st.columns(2)
    symbols = col1.text_input("Symbols (comma-sep)", "AAPL,MSFT,SPY")
    strategy = col2.selectbox("Strategy", available_strategies())
    col3, col4, col5 = st.columns(3)
    start = col3.text_input("Start", "2015-01-01")
    cash = col4.number_input("Starting cash", value=100_000, step=10_000)
    slip = col5.number_input("Slippage (bps)", value=5.0, step=1.0)

    if not st.button("Run backtest", type="primary"):
        return

    feed = YFinanceData()
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    for sym in syms:
        try:
            df = feed.history(sym, start=start)
        except Exception as e:
            st.error(f"{sym}: {e}")
            continue
        res = run_backtest(
            sym, df, get_strategy(strategy),
            starting_cash=float(cash), slippage_bps=float(slip),
        )
        m = res.metrics
        frame = pd.DataFrame({"strategy": res.equity})
        if res.benchmark_equity is not None:
            frame["benchmark"] = res.benchmark_equity.reindex(res.equity.index)
        st.markdown(f"### {sym} — {strategy}")
        cc = st.columns(5)
        cc[0].metric("Total", f"{m.total_return_pct:+.2f}%")
        cc[1].metric("CAGR", f"{m.cagr_pct:+.2f}%")
        cc[2].metric("Sharpe", f"{m.sharpe:.2f}")
        cc[3].metric("Max DD", f"{m.max_drawdown_pct:.2f}%")
        bench_m = (
            compute_metrics(res.benchmark_equity, [], 1.0)
            if res.benchmark_equity is not None
            else None
        )
        cc[4].metric(
            "vs buy & hold",
            f"{m.total_return_pct - bench_m.total_return_pct:+.2f}%"
            if bench_m
            else "n/a",
        )
        st.plotly_chart(
            _equity_chart(frame, f"{sym} equity vs buy & hold"),
            use_container_width=True,
        )
        st.plotly_chart(_drawdown_chart(res.equity), use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="AI-IB-Analyst", layout="wide")
    settings = load_settings()
    mode = "LIVE" if not settings.is_paper else "PAPER"
    badge = "🔴 LIVE — REAL CAPITAL" if mode == "LIVE" else "🟢 PAPER (simulated)"

    st.title("AI-IB-Analyst — performance dashboard")
    st.markdown(
        f"**Mode:** {badge}  ·  **Strategy:** `{settings.strategy}`  ·  "
        f"_{CAVEAT}_"
    )

    tab_live, tab_bt = st.tabs(["Live / Paper", "Backtest"])
    with tab_live:
        _live_tab(settings)
    with tab_bt:
        _backtest_tab()

    st.divider()
    st.caption(CAVEAT)


if __name__ == "__main__":
    main()
