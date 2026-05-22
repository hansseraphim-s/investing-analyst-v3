"""Pre-trade risk checks — pure functions, fully unit-tested.

Design note vs. v1: the v1 risk manager queried SQLite with
`datetime('now','-1 hour')` (UTC) while writing timestamps in local time, so
the trade-frequency breaker was silently wrong by the UTC offset. Here the
risk layer takes everything it needs as explicit arguments and does no I/O,
no clock reads, and no DB access. The caller owns time; the checker is pure
and deterministic, which is exactly why it can be tested exhaustively.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import RiskConfig
from ..greeks.aggregator import GreeksLimits, PortfolioGreeks, check_greeks_limits
from .cvar import check_cvar_breaker


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: int
    market_value: float


@dataclass(frozen=True)
class PortfolioView:
    equity: float
    cash: float
    day_pnl_pct: float          # e.g. -2.5 for -2.5%
    positions: tuple[Position, ...] = ()
    trades_today: int = 0

    def position_value(self, symbol: str) -> float:
        return sum(p.market_value for p in self.positions if p.symbol == symbol.upper())

    def owns(self, symbol: str) -> Position | None:
        for p in self.positions:
            if p.symbol == symbol.upper():
                return p
        return None


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    check: str
    reason: str


def pre_trade_check(
    action: str,
    symbol: str,
    qty: int,
    price: float,
    portfolio: PortfolioView,
    risk: RiskConfig,
    *,
    returns_history: list[float] | None = None,
    projected_greeks: PortfolioGreeks | None = None,
    greeks_limits: GreeksLimits | None = None,
) -> RiskDecision:
    """Return an approve/deny decision. Never raises on ordinary inputs.

    Optional new gates (backwards compatible — callers that omit them get
    the v1 behavior unchanged):

    * `returns_history`: list of daily-return FRACTIONS (e.g. -0.012 for
      -1.2%). When provided, the CVaR breaker is consulted alongside the
      absolute max_daily_loss_pct gate; whichever fires first blocks.
      Needs >= 30 observations to activate; below that it's silently
      inactive.
    * `projected_greeks` + `greeks_limits`: when BOTH are provided (i.e.
      this is an option order or a portfolio-Greeks-sensitive check),
      run the Greeks hard-cap checks AFTER the equity gates. The check
      uses the PROJECTED portfolio Greeks (after the proposed order)
      against the configured limits.
    """
    action = action.upper()
    symbol = symbol.upper()

    if qty <= 0:
        return RiskDecision(False, "INVALID_QTY", f"qty must be > 0; got {qty}")
    if price <= 0:
        return RiskDecision(False, "INVALID_PRICE", f"price must be > 0; got {price}")
    if portfolio.equity <= 0:
        return RiskDecision(False, "NO_EQUITY", "Portfolio equity is zero or negative.")

    order_value = qty * price

    # SELL is a risk-REDUCING action (exit / de-risk). A safety system must
    # never block an exit, so SELL is validated only for ownership/size — the
    # risk-adding gates below apply to BUY exclusively.
    if action == "SELL":
        held = portfolio.owns(symbol)
        if held is None:
            return RiskDecision(
                False, "NO_POSITION", f"Cannot SELL {symbol}: no open position."
            )
        if qty > held.qty:
            return RiskDecision(
                False, "OVERSELL", f"Selling {qty} but only {held.qty} held."
            )
        return RiskDecision(True, "ALL_PASSED", "Exit/de-risk order allowed.")

    if action != "BUY":
        return RiskDecision(False, "INVALID_ACTION", f"Unknown action {action!r}")

    # ---- risk-adding gates (BUY only) --------------------------------------
    # 1. Daily-loss circuit breaker (absolute) — halt NEW entries for the day.
    if portfolio.day_pnl_pct <= -(risk.max_daily_loss_pct * 100):
        return RiskDecision(
            False,
            "DAILY_LOSS_BREAKER",
            f"Day P&L {portfolio.day_pnl_pct:.2f}% hit the "
            f"-{risk.max_daily_loss_pct * 100:.0f}% limit. New entries halted.",
        )

    # 1b. CVaR-based daily-loss breaker (regime-adaptive). Active only when
    # >= 30 days of return history exist; below that the absolute gate above
    # is the only daily-loss protection.
    if returns_history is not None:
        cvar = check_cvar_breaker(portfolio.day_pnl_pct, returns_history)
        if cvar.breached:
            return RiskDecision(False, "CVAR_BREAKER", cvar.detail)

    # 2. Prohibited instruments.
    if symbol in {s.upper() for s in risk.prohibited_symbols}:
        return RiskDecision(
            False, "PROHIBITED_SYMBOL", f"{symbol} is on the prohibited list."
        )

    # 3. Per-order hard cap.
    if order_value > risk.max_order_value:
        return RiskDecision(
            False,
            "ORDER_TOO_LARGE",
            f"Order ${order_value:,.0f} exceeds the ${risk.max_order_value:,.0f} cap.",
        )

    # 4. Trade-frequency breaker.
    if portfolio.trades_today >= risk.max_trades_per_day:
        return RiskDecision(
            False,
            "FREQUENCY_LIMIT",
            f"{portfolio.trades_today} trades today >= "
            f"{risk.max_trades_per_day} limit.",
        )

    # 5. Concentration.
    projected = portfolio.position_value(symbol) + order_value
    concentration = projected / portfolio.equity
    if concentration > risk.max_position_pct:
        return RiskDecision(
            False,
            "CONCENTRATION_LIMIT",
            f"{symbol} would be {concentration * 100:.1f}% of equity "
            f"(limit {risk.max_position_pct * 100:.0f}%).",
        )

    # 6. Buying power.
    if order_value > portfolio.cash:
        return RiskDecision(
            False,
            "INSUFFICIENT_CASH",
            f"Needs ${order_value:,.0f}, only ${portfolio.cash:,.0f} cash.",
        )

    # 7. Minimum cash reserve.
    remaining_cash_pct = (portfolio.cash - order_value) / portfolio.equity
    if remaining_cash_pct < risk.min_cash_reserve_pct:
        return RiskDecision(
            False,
            "CASH_RESERVE",
            f"Would leave {remaining_cash_pct * 100:.1f}% cash, below the "
            f"{risk.min_cash_reserve_pct * 100:.0f}% minimum.",
        )

    # 8. Portfolio Greeks gate (options orders only). Caller supplies the
    # PROJECTED portfolio Greeks (after this order) and the configured limits.
    # When either is None we skip this check — equity-only callers stay
    # unaffected.
    if projected_greeks is not None and greeks_limits is not None:
        g = check_greeks_limits(projected_greeks, greeks_limits, equity=portfolio.equity)
        if not g.approved:
            return RiskDecision(
                False,
                f"GREEKS_{g.breached_limit}",
                g.detail,
            )

    return RiskDecision(True, "ALL_PASSED", "All risk checks passed.")
