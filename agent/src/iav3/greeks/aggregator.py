"""Portfolio Greeks aggregator + hard-cap checker.

Aggregates per-position Greeks (delta=1/share for equity, full Greeks
from option chain for options, sign-flipped for short positions) into
portfolio-level totals. The risk layer consults check_greeks_limits
before approving any new options order — Delta cap prevents over-
exposure to direction, Vega cap prevents over-exposure to vol, Theta
floor prevents excessive daily bleed.

Required before any options strategy can promote to live (per the
README go-live checklist).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol


@dataclass(frozen=True)
class PortfolioGreeks:
    delta: float                # signed share-equivalents (e.g. +100 = +100Δ, like long 100 shares of an underlying)
    gamma: float
    vega: float                 # dollars per 1% IV change (Vercel convention)
    theta: float                # dollars per day (negative for net theta-decay)
    rho: float
    notional_exposure: float    # absolute dollar exposure (equity market value + option notional)
    cash_pct: float             # cash / equity * 100


@dataclass(frozen=True)
class GreeksLimits:
    max_delta: float                       # portfolio |delta| cap (absolute value)
    max_vega: float                        # max long-vol $ exposure (absolute value)
    min_theta: float                       # most-negative theta allowed (e.g. -50.0 dollars/day)
    max_notional_exposure_pct: float       # fraction of equity (e.g. 1.5 = 150%)


@dataclass(frozen=True)
class GreeksDecision:
    approved: bool
    breached_limit: str | None
    detail: str


class PositionLike(Protocol):
    """The subset of position fields we need to compute Greeks.

    Both the agent's Position dataclass and Alpaca's PositionResponse
    satisfy this shape.
    """
    symbol: str
    asset_class: str       # 'equity' or 'option'
    qty: float             # signed: positive = long, negative = short
    market_value: float


class OptionQuoteLike(Protocol):
    delta: float | None
    gamma: float | None
    vega: float | None
    theta: float | None
    rho: float | None


CONTRACT_MULTIPLIER = 100.0


def aggregate_greeks(
    *,
    positions: Iterable[PositionLike],
    option_quotes: dict[str, OptionQuoteLike],
    equity: float,
    cash: float,
) -> PortfolioGreeks:
    """Compute portfolio-level Greeks from current positions.

    For equity positions: each share contributes delta=1 (long) or
    delta=-1 (short). No gamma/vega/theta/rho for equity.

    For option positions: the contract's Greeks are scaled by
    100 shares/contract * |qty| contracts, then sign-flipped if short
    (since selling a call means we're short delta, not long).

    Missing option quotes (Alpaca returned no quote for the contract)
    contribute zero — the function does not raise. Callers SHOULD log
    when quotes are missing for held option positions, since that means
    Greeks are silently understated.
    """
    delta = gamma = vega = theta = rho = 0.0
    notional = 0.0

    for pos in positions:
        qty = float(pos.qty)
        market_value = float(pos.market_value)
        if pos.asset_class == "equity":
            # 1 share = 1 delta. Short positions have negative qty already.
            delta += qty
            notional += abs(market_value)
            continue
        if pos.asset_class == "option":
            quote = option_quotes.get(pos.symbol)
            if quote is None:
                # Missing quote: contribute zero Greeks but still count notional.
                notional += abs(market_value)
                continue
            # Sign: long option (qty > 0) keeps sign; short option (qty < 0) flips.
            # The Greeks from Alpaca are quoted for a long contract; we multiply
            # by the signed contract count to get the per-position contribution.
            multiplier = CONTRACT_MULTIPLIER * qty
            if quote.delta is not None:
                delta += multiplier * quote.delta
            if quote.gamma is not None:
                gamma += multiplier * quote.gamma
            if quote.vega is not None:
                vega += multiplier * quote.vega
            if quote.theta is not None:
                theta += multiplier * quote.theta
            if quote.rho is not None:
                rho += multiplier * quote.rho
            notional += abs(market_value)
            continue
        # Unknown asset class: count notional but no Greeks.
        notional += abs(market_value)

    cash_pct = 100.0 * cash / equity if equity > 0 else 0.0
    return PortfolioGreeks(
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta,
        rho=rho,
        notional_exposure=notional,
        cash_pct=cash_pct,
    )


def check_greeks_limits(
    proposed: PortfolioGreeks,
    limits: GreeksLimits,
    *,
    equity: float,
) -> GreeksDecision:
    """Approve/deny a portfolio Greeks snapshot against the configured caps.

    Order matters for explainability — we return on the FIRST breach so
    the user knows which gate fired. If multiple limits would have
    failed, only the first is reported. Run the function again after
    adjusting to surface subsequent failures.

    The `equity` argument is needed to translate max_notional_exposure_pct
    (a fraction) into a dollar comparison against proposed.notional_exposure.
    """
    if abs(proposed.delta) > limits.max_delta:
        return GreeksDecision(
            approved=False,
            breached_limit="MAX_DELTA",
            detail=(
                f"|delta| {abs(proposed.delta):.2f} > limit {limits.max_delta:.2f} "
                f"(direction exposure too high)"
            ),
        )
    if abs(proposed.vega) > limits.max_vega:
        return GreeksDecision(
            approved=False,
            breached_limit="MAX_VEGA",
            detail=(
                f"|vega| {abs(proposed.vega):.2f} > limit {limits.max_vega:.2f} "
                f"(vol exposure too high)"
            ),
        )
    if proposed.theta < limits.min_theta:
        return GreeksDecision(
            approved=False,
            breached_limit="MIN_THETA",
            detail=(
                f"theta {proposed.theta:.2f}/day below floor {limits.min_theta:.2f} "
                f"(daily decay too steep)"
            ),
        )
    if equity > 0:
        max_notional_dollars = limits.max_notional_exposure_pct * equity
        if proposed.notional_exposure > max_notional_dollars:
            return GreeksDecision(
                approved=False,
                breached_limit="MAX_NOTIONAL",
                detail=(
                    f"notional ${proposed.notional_exposure:,.0f} > limit "
                    f"${max_notional_dollars:,.0f} "
                    f"({limits.max_notional_exposure_pct:.0%} of ${equity:,.0f} equity)"
                ),
            )
    return GreeksDecision(approved=True, breached_limit=None, detail="All Greeks within limits.")
