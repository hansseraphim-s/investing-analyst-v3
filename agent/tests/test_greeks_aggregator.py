"""Unit tests for the portfolio Greeks aggregator."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from iav3.greeks.aggregator import (
    CONTRACT_MULTIPLIER,
    GreeksLimits,
    PortfolioGreeks,
    aggregate_greeks,
    check_greeks_limits,
)


@dataclass
class FakePosition:
    symbol: str
    asset_class: str
    qty: float
    market_value: float


@dataclass
class FakeQuote:
    delta: float | None = 0.0
    gamma: float | None = 0.0
    vega: float | None = 0.0
    theta: float | None = 0.0
    rho: float | None = 0.0


# ----------------------------------------------------------------------
# aggregate_greeks
# ----------------------------------------------------------------------


class TestAggregateGreeks:
    def test_empty_portfolio(self):
        g = aggregate_greeks(positions=[], option_quotes={}, equity=100_000.0, cash=100_000.0)
        assert g.delta == 0
        assert g.notional_exposure == 0
        assert g.cash_pct == 100.0

    def test_long_equity_contributes_one_delta_per_share(self):
        positions = [FakePosition("AAPL", "equity", 100, 20_000.0)]
        g = aggregate_greeks(positions=positions, option_quotes={}, equity=100_000.0, cash=80_000.0)
        assert g.delta == 100.0
        assert g.gamma == 0.0
        assert g.notional_exposure == 20_000.0
        assert g.cash_pct == 80.0

    def test_short_equity_contributes_negative_delta(self):
        positions = [FakePosition("AAPL", "equity", -100, 20_000.0)]
        g = aggregate_greeks(positions=positions, option_quotes={}, equity=100_000.0, cash=100_000.0)
        assert g.delta == -100.0

    def test_long_call_scaled_by_contract_multiplier(self):
        # 1 call contract with delta=0.5 contributes 0.5 * 100 = 50 delta
        positions = [FakePosition("AAPL250620C00200000", "option", 1, 250.0)]
        quotes = {"AAPL250620C00200000": FakeQuote(delta=0.5, gamma=0.01, vega=0.20, theta=-0.05)}
        g = aggregate_greeks(positions=positions, option_quotes=quotes, equity=100_000.0, cash=99_750.0)
        assert g.delta == pytest.approx(50.0)
        assert g.gamma == pytest.approx(1.0)
        assert g.vega == pytest.approx(20.0)
        assert g.theta == pytest.approx(-5.0)

    def test_short_call_flips_sign_through_negative_qty(self):
        # Short 1 call (qty=-1) with delta=0.5: contribution = -1 * 100 * 0.5 = -50
        positions = [FakePosition("AAPL250620C00200000", "option", -1, 250.0)]
        quotes = {"AAPL250620C00200000": FakeQuote(delta=0.5, theta=-0.05)}
        g = aggregate_greeks(positions=positions, option_quotes=quotes, equity=100_000.0, cash=99_750.0)
        assert g.delta == pytest.approx(-50.0)
        # Selling theta-decaying option = receiving theta. -1 * 100 * -0.05 = +5.
        assert g.theta == pytest.approx(5.0)

    def test_missing_quote_zero_greeks_but_notional_counted(self):
        positions = [FakePosition("UNKNOWN", "option", 5, 1_000.0)]
        g = aggregate_greeks(positions=positions, option_quotes={}, equity=100_000.0, cash=99_000.0)
        assert g.delta == 0
        assert g.gamma == 0
        assert g.notional_exposure == 1_000.0  # still counts toward notional

    def test_mixed_portfolio(self):
        positions = [
            FakePosition("AAPL", "equity", 100, 20_000.0),
            FakePosition("MSFT", "equity", -50, 15_000.0),
            FakePosition("AAPL250620C00200000", "option", 2, 500.0),  # long 2 calls
            FakePosition("MSFT250620P00350000", "option", -1, 300.0),  # short 1 put
        ]
        quotes = {
            "AAPL250620C00200000": FakeQuote(delta=0.6, vega=0.20, theta=-0.05),
            "MSFT250620P00350000": FakeQuote(delta=-0.30, vega=0.15, theta=-0.03),
        }
        g = aggregate_greeks(positions=positions, option_quotes=quotes, equity=100_000.0, cash=64_200.0)

        # Equity delta: 100 (long AAPL) + (-50) (short MSFT) = 50
        # Option delta: long 2 calls @ 0.6 = +120, short 1 put @ -0.30 = +30
        expected_delta = 50 + 120 + 30
        assert g.delta == pytest.approx(expected_delta)

    def test_cash_pct_zero_when_equity_nonpositive(self):
        # Degenerate but possible (margin call etc.)
        g = aggregate_greeks(positions=[], option_quotes={}, equity=0.0, cash=0.0)
        assert g.cash_pct == 0.0


# ----------------------------------------------------------------------
# check_greeks_limits
# ----------------------------------------------------------------------


def _greeks(delta=0.0, vega=0.0, theta=0.0, notional=0.0) -> PortfolioGreeks:
    return PortfolioGreeks(
        delta=delta, gamma=0.0, vega=vega, theta=theta, rho=0.0,
        notional_exposure=notional, cash_pct=50.0,
    )


def _limits(max_delta=200, max_vega=500, min_theta=-50, max_notional_pct=1.5) -> GreeksLimits:
    return GreeksLimits(
        max_delta=max_delta, max_vega=max_vega,
        min_theta=min_theta, max_notional_exposure_pct=max_notional_pct,
    )


class TestCheckGreeksLimits:
    def test_all_within_limits(self):
        d = check_greeks_limits(_greeks(delta=50, vega=100, theta=-10), _limits(), equity=100_000.0)
        assert d.approved is True
        assert d.breached_limit is None

    def test_delta_breach(self):
        d = check_greeks_limits(_greeks(delta=300), _limits(max_delta=200), equity=100_000.0)
        assert d.approved is False
        assert d.breached_limit == "MAX_DELTA"
        assert "300" in d.detail

    def test_short_delta_also_breaches(self):
        # The check is on absolute value
        d = check_greeks_limits(_greeks(delta=-300), _limits(max_delta=200), equity=100_000.0)
        assert d.approved is False
        assert d.breached_limit == "MAX_DELTA"

    def test_vega_breach(self):
        d = check_greeks_limits(_greeks(vega=600), _limits(max_vega=500), equity=100_000.0)
        assert d.approved is False
        assert d.breached_limit == "MAX_VEGA"

    def test_theta_floor_breach(self):
        # theta = -100/day breaches floor of -50/day
        d = check_greeks_limits(_greeks(theta=-100), _limits(min_theta=-50), equity=100_000.0)
        assert d.approved is False
        assert d.breached_limit == "MIN_THETA"

    def test_notional_breach(self):
        d = check_greeks_limits(
            _greeks(notional=200_000.0),
            _limits(max_notional_pct=1.5),
            equity=100_000.0,  # 1.5x = $150k cap
        )
        assert d.approved is False
        assert d.breached_limit == "MAX_NOTIONAL"

    def test_first_breach_wins(self):
        # Both delta and vega breach — delta is checked first, so MAX_DELTA reported
        d = check_greeks_limits(
            _greeks(delta=300, vega=600),
            _limits(max_delta=200, max_vega=500),
            equity=100_000.0,
        )
        assert d.breached_limit == "MAX_DELTA"

    def test_zero_equity_skips_notional_check(self):
        # When equity is 0 the % check is bypassed (defensive: avoid div-by-zero)
        d = check_greeks_limits(_greeks(notional=10_000.0), _limits(), equity=0.0)
        assert d.approved is True


def test_contract_multiplier_constant():
    # Asserts the constant — a regression suite catch if anyone changes it
    assert CONTRACT_MULTIPLIER == 100.0
