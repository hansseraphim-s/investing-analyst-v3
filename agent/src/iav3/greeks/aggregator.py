"""Portfolio Greeks aggregator + hard-cap checker.

Aggregates per-position Greeks (from Alpaca's option-quote feed for
options; delta=1 per share for long equity, delta=-1 per share for short
equity) into portfolio-level totals. The risk layer consults
check_greeks_limits before approving any new options order.

Phase 2 implementation. Phase 0 locks the interface.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PortfolioGreeks:
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float
    notional_exposure: float
    cash_pct: float


@dataclass(frozen=True)
class GreeksLimits:
    max_delta: float                # portfolio delta cap (positive number; abs() is checked)
    max_vega: float                 # max long-vol exposure
    min_theta: float                # most-negative theta allowed (i.e. max bleed per day)
    max_notional_exposure_pct: float  # of equity


@dataclass(frozen=True)
class GreeksDecision:
    approved: bool
    breached_limit: str | None
    detail: str


def aggregate_greeks(
    *,
    positions: list,        # list of Position-like; equities + option positions
    option_quotes: dict,    # symbol -> OptionQuote with Greeks
    equity: float,
    cash: float,
) -> PortfolioGreeks:
    """Compute portfolio-level Greeks from current positions."""
    raise NotImplementedError("Phase 2: implement Greeks aggregation.")


def check_greeks_limits(
    proposed: PortfolioGreeks,
    limits: GreeksLimits,
) -> GreeksDecision:
    """Approve/deny based on the configured hard caps."""
    raise NotImplementedError("Phase 2: implement limit-check.")
