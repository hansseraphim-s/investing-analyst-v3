"""Defined-risk options "wheel", MODEL-priced.

The wheel: sell a cash-secured put on a name you'd own; if assigned, take the
shares; sell covered calls against them until called away; repeat. Risk is
bounded — the worst case is owning a stock you already wanted, minus the
premium collected. No naked options, ever.

================================ READ THIS ================================
This is NOT a P&L backtest. Free data has no historical option prices, so
every option here is priced with Black-Scholes using

        implied_vol  :=  realized_vol * IV_PREMIUM_MULT   (default 1.15)

That assumption *bakes in the volatility risk premium* — i.e. the model
assumes the very edge the wheel is supposed to harvest. Results will look
favorable largely because of that assumption. Treat the output as a
behavioral / relative-comparison model, not a forecast of money. Real
results depend on the actual IV surface, bid/ask spreads, early assignment,
and dividends, none of which are modeled.
===========================================================================
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..backtest.metrics import compute_metrics, max_drawdown
from ..data.indicators import annualized_volatility
from .black_scholes import bs_price

MODEL_CAVEAT = (
    "MODEL, NOT P&L — options priced via Black-Scholes with "
    "IV := realized_vol * assumed premium multiplier. The model assumes the "
    "edge it measures. Not a forecast. Not financial advice."
)

LOT = 100


@dataclass
class WheelResult:
    symbol: str
    equity: pd.Series
    cycles: list[dict]
    summary: dict
    caveat: str = MODEL_CAVEAT


def run_wheel_backtest(
    symbol: str,
    df: pd.DataFrame,
    *,
    starting_cash: float = 100_000.0,
    dte_trading: int = 21,          # ~1 month of trading days
    put_otm_pct: float = 0.05,
    call_otm_pct: float = 0.05,
    risk_free: float = 0.04,        # ASSUMPTION (labeled)
    iv_premium_mult: float = 1.15,  # ASSUMPTION — see module caveat
    vol_window: int = 20,
) -> WheelResult:
    close = df["close"].to_numpy()
    rvol = annualized_volatility(df["close"], vol_window).to_numpy()
    idx = df.index
    n = len(df)
    t_years = dte_trading / 252.0

    cash = starting_cash
    shares = 0
    option = None  # dict: type,strike,expiry,contracts,premium
    cycles: list[dict] = []
    equity_curve: list[tuple[pd.Timestamp, float]] = []

    def iv_at(i: int) -> float | None:
        v = rvol[i]
        if v != v or v <= 0:  # NaN or non-positive
            return None
        return v * iv_premium_mult

    for i in range(n):
        s = close[i]

        # ---- settle an option that expires on this bar ----------------------
        if option is not None and i >= option["expiry"]:
            k = option["strike"]
            c = option["contracts"]
            if option["type"] == "put":
                if s < k:  # assigned -> buy shares at strike
                    shares += LOT * c
                    cash -= k * LOT * c
                    outcome = "assigned"
                else:
                    outcome = "put_expired"
            else:  # covered call
                if s > k:  # called away -> sell shares at strike
                    shares -= LOT * c
                    cash += k * LOT * c
                    outcome = "called_away"
                else:
                    outcome = "call_expired"
            cycles[-1].update(outcome=outcome, settle_price=round(float(s), 2))
            option = None

        # ---- open a new option if flat-of-options ---------------------------
        if option is None:
            iv = iv_at(i)
            if iv is not None and i + dte_trading < n:
                if shares == 0:
                    # Cash-secured put: reserve K*100 per contract.
                    k = round(s * (1.0 - put_otm_pct), 2)
                    c = int(cash // (k * LOT))
                    if c >= 1:
                        prem = bs_price("put", s, k, t_years, risk_free, iv) * LOT * c
                        cash += prem
                        option = {"type": "put", "strike": k,
                                  "expiry": i + dte_trading, "contracts": c,
                                  "premium": prem}
                        cycles.append({
                            "open_time": str(idx[i]), "type": "csp",
                            "underlying": round(float(s), 2), "strike": k,
                            "contracts": c, "premium": round(prem, 2),
                            "iv_used": round(iv, 4),
                        })
                else:
                    # Covered call against held shares.
                    c = shares // LOT
                    if c >= 1:
                        k = round(s * (1.0 + call_otm_pct), 2)
                        prem = bs_price("call", s, k, t_years, risk_free, iv) * LOT * c
                        cash += prem
                        option = {"type": "call", "strike": k,
                                  "expiry": i + dte_trading, "contracts": c,
                                  "premium": prem}
                        cycles.append({
                            "open_time": str(idx[i]), "type": "covered_call",
                            "underlying": round(float(s), 2), "strike": k,
                            "contracts": c, "premium": round(prem, 2),
                            "iv_used": round(iv, 4),
                        })

        # ---- mark-to-model equity (short option is a model liability) -------
        liability = 0.0
        if option is not None:
            iv = iv_at(i)
            if iv is not None:
                rem = max(option["expiry"] - i, 0) / 252.0
                liability = (
                    bs_price(option["type"], s, option["strike"],
                             rem, risk_free, iv)
                    * LOT * option["contracts"]
                )
        equity_curve.append((idx[i], cash + shares * s - liability))

    eq = pd.Series(
        [v for _, v in equity_curve],
        index=pd.DatetimeIndex([t for t, _ in equity_curve]),
    ).dropna()

    closed = [c for c in cycles if "outcome" in c]
    assigned = sum(1 for c in closed if c["outcome"] == "assigned")
    called = sum(1 for c in closed if c["outcome"] == "called_away")
    total_prem = sum(c["premium"] for c in cycles)
    m = compute_metrics(eq, [], exposure_fraction=1.0)
    summary = {
        "cycles": len(cycles),
        "settled": len(closed),
        "assigned": assigned,
        "called_away": called,
        "total_premium_collected": round(total_prem, 2),
        "total_return_pct": m.total_return_pct,
        "cagr_pct": m.cagr_pct,
        "max_drawdown_pct": m.max_drawdown_pct,
        "sharpe": m.sharpe,
        "final_equity": round(float(eq.iloc[-1]), 2) if len(eq) else starting_cash,
        "max_drawdown_check": round(max_drawdown(eq) * 100, 2),
    }
    return WheelResult(symbol=symbol, equity=eq, cycles=cycles, summary=summary)
