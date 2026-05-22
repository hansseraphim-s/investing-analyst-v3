"""Unit tests for the live wheel decision engine.

Network-dependent feeds (AlpacaOptionsFeed, FinnhubFeed) are mocked.
These tests assert decision semantics — what action the engine takes
and why — without hitting any API.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from iav3.data.alpaca_options import OptionContract, OptionQuote
from iav3.data.finnhub_feed import EarningsEvent
from iav3.options.wheel_live import (
    CONTRACT_LOT,
    WheelLive,
    WheelLiveConfig,
    WheelState,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

TODAY = date(2026, 6, 1)


def make_contract(strike: float, otype="put", expiry_days=30) -> OptionContract:
    expiry = TODAY + timedelta(days=expiry_days)
    return OptionContract(
        symbol=f"AAPL{expiry.strftime('%y%m%d')}{'P' if otype == 'put' else 'C'}{int(strike * 1000):08d}",
        underlying="AAPL",
        option_type=otype,
        strike=strike,
        expiry=expiry,
    )


def make_quote(
    strike: float,
    otype="put",
    *,
    bid: float = 2.40,
    ask: float = 2.60,
    iv: float = 0.30,
    expiry_days: int = 30,
) -> OptionQuote:
    contract = make_contract(strike, otype, expiry_days)
    mid = (bid + ask) / 2
    return OptionQuote(
        contract=contract,
        bid=bid,
        ask=ask,
        last=mid,
        mid=mid,
        iv=iv,
        delta=-0.30 if otype == "put" else 0.30,
        gamma=0.02,
        vega=0.15,
        theta=-0.05,
        rho=0.01,
        open_interest=1000,
        volume=50,
        quoted_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def mock_feed():
    feed = MagicMock()
    feed.nearest_expiry.return_value = TODAY + timedelta(days=30)
    feed.iv_rank.return_value = None  # default: IV rank unavailable
    return feed


@pytest.fixture
def mock_finnhub():
    return MagicMock()


@pytest.fixture
def standard_state():
    return WheelState(
        underlying="AAPL",
        spot=200.0,
        shares=0,
        open_option=None,
        available_cash=25_000.0,
    )


# ----------------------------------------------------------------------
# Hold path
# ----------------------------------------------------------------------


class TestHold:
    def test_holds_when_position_already_open(self, mock_feed, standard_state):
        state = WheelState(
            **{**standard_state.__dict__, "open_option": make_contract(190.0, "put")}
        )
        engine = WheelLive(mock_feed)
        d = engine.decide(state, as_of=TODAY)
        assert d.action == "hold"
        assert d.contract is not None
        assert "awaiting" in d.rationale.lower()
        # No chain lookup should have happened
        mock_feed.chain.assert_not_called()


# ----------------------------------------------------------------------
# CSP path
# ----------------------------------------------------------------------


class TestCashSecuredPut:
    def test_opens_csp_when_no_shares(self, mock_feed, standard_state):
        # Spot 200, 5% OTM target = 190
        mock_feed.chain.return_value = [
            make_quote(185.0, "put"),
            make_quote(190.0, "put"),  # the closest-to-target
            make_quote(195.0, "put"),
        ]
        engine = WheelLive(mock_feed)
        d = engine.decide(standard_state, as_of=TODAY)
        assert d.action == "open_csp"
        assert d.contract is not None
        assert d.contract.strike == 190.0
        # $25k / ($190 * 100) = 1 contract
        assert d.contracts == 1
        assert d.is_opening

    def test_blocks_when_chain_empty(self, mock_feed, standard_state):
        mock_feed.chain.return_value = []
        d = WheelLive(mock_feed).decide(standard_state, as_of=TODAY)
        assert d.action == "block_no_chain"

    def test_blocks_when_insufficient_cash(self, mock_feed):
        # $5k cash, $190 strike needs $19k for 1 CSP
        state = WheelState(
            underlying="AAPL", spot=200.0, shares=0,
            open_option=None, available_cash=5_000.0,
        )
        mock_feed.chain.return_value = [make_quote(190.0, "put")]
        d = WheelLive(mock_feed).decide(state, as_of=TODAY)
        assert d.action == "block_insufficient_cash"

    def test_blocks_on_wide_spread(self, mock_feed, standard_state):
        mock_feed.chain.return_value = [make_quote(190.0, "put", bid=1.0, ask=5.0)]
        d = WheelLive(mock_feed).decide(standard_state, as_of=TODAY)
        assert d.action == "block_no_liquidity"
        assert "spread" in d.rationale.lower()

    def test_blocks_on_zero_bid(self, mock_feed, standard_state):
        mock_feed.chain.return_value = [make_quote(190.0, "put", bid=0.0, ask=2.0)]
        d = WheelLive(mock_feed).decide(standard_state, as_of=TODAY)
        assert d.action == "block_no_liquidity"

    def test_blocks_on_low_premium(self, mock_feed, standard_state):
        # Tight spread (within liquidity gate) but mid below the min premium
        # floor — triggers the premium-floor branch specifically.
        mock_feed.chain.return_value = [make_quote(190.0, "put", bid=0.10, ask=0.11)]
        d = WheelLive(mock_feed).decide(standard_state, as_of=TODAY)
        assert d.action == "block_no_liquidity"
        assert "min premium" in d.rationale.lower()


# ----------------------------------------------------------------------
# Covered call path
# ----------------------------------------------------------------------


class TestCoveredCall:
    def test_opens_cc_when_holding_full_lot(self, mock_feed):
        state = WheelState(
            underlying="AAPL", spot=200.0,
            shares=100, open_option=None, available_cash=0.0,
        )
        mock_feed.chain.return_value = [make_quote(210.0, "call")]
        d = WheelLive(mock_feed).decide(state, as_of=TODAY)
        assert d.action == "open_cc"
        assert d.contracts == 1
        assert d.contract.option_type == "call"

    def test_opens_cc_uses_full_held_lot_count(self, mock_feed):
        state = WheelState(
            underlying="AAPL", spot=200.0,
            shares=350, open_option=None, available_cash=0.0,
        )
        mock_feed.chain.return_value = [make_quote(210.0, "call")]
        d = WheelLive(mock_feed).decide(state, as_of=TODAY)
        assert d.action == "open_cc"
        assert d.contracts == 3  # 350 // 100

    def test_falls_to_csp_when_below_full_lot(self, mock_feed):
        state = WheelState(
            underlying="AAPL", spot=200.0,
            shares=50, open_option=None, available_cash=25_000.0,
        )
        mock_feed.chain.return_value = [make_quote(190.0, "put")]
        d = WheelLive(mock_feed).decide(state, as_of=TODAY)
        assert d.action == "open_csp"


# ----------------------------------------------------------------------
# Earnings-event mode
# ----------------------------------------------------------------------


class TestEarningsMode:
    def test_blocks_when_no_event(self, mock_feed, mock_finnhub, standard_state):
        mock_finnhub.next_earnings.return_value = None
        d = WheelLive(mock_feed, finnhub=mock_finnhub).decide(
            standard_state, mode="earnings_event", as_of=TODAY,
        )
        assert d.action == "block_no_earnings_event"

    def test_blocks_when_event_too_far(self, mock_feed, mock_finnhub, standard_state):
        mock_finnhub.next_earnings.return_value = EarningsEvent(
            symbol="AAPL", date=TODAY + timedelta(days=45), hour="amc",
            eps_estimate=None, revenue_estimate=None,
        )
        d = WheelLive(mock_feed, finnhub=mock_finnhub).decide(
            standard_state, mode="earnings_event", as_of=TODAY,
        )
        assert d.action == "block_outside_earnings_window"

    def test_blocks_when_event_too_close(self, mock_feed, mock_finnhub, standard_state):
        mock_finnhub.next_earnings.return_value = EarningsEvent(
            symbol="AAPL", date=TODAY + timedelta(days=1), hour="amc",
            eps_estimate=None, revenue_estimate=None,
        )
        d = WheelLive(mock_feed, finnhub=mock_finnhub).decide(
            standard_state, mode="earnings_event", as_of=TODAY,
        )
        assert d.action == "block_outside_earnings_window"

    def test_opens_csp_within_earnings_window(self, mock_feed, mock_finnhub, standard_state):
        mock_finnhub.next_earnings.return_value = EarningsEvent(
            symbol="AAPL", date=TODAY + timedelta(days=7), hour="amc",
            eps_estimate=1.20, revenue_estimate=80e9,
        )
        mock_feed.chain.return_value = [make_quote(190.0, "put")]
        d = WheelLive(mock_feed, finnhub=mock_finnhub).decide(
            standard_state, mode="earnings_event", as_of=TODAY,
        )
        assert d.action == "open_csp"

    def test_blocks_without_finnhub_in_earnings_mode(self, mock_feed, standard_state):
        d = WheelLive(mock_feed).decide(
            standard_state, mode="earnings_event", as_of=TODAY,
        )
        assert d.action == "block_no_earnings_event"


# ----------------------------------------------------------------------
# IV rank gate
# ----------------------------------------------------------------------


class TestIvRankGate:
    def test_skips_gate_when_history_insufficient(self, mock_feed, standard_state):
        mock_feed.iv_rank.return_value = None  # bootstrap state
        mock_feed.chain.return_value = [make_quote(190.0, "put")]
        cfg = WheelLiveConfig(min_iv_rank=50.0)
        d = WheelLive(mock_feed, config=cfg).decide(standard_state, as_of=TODAY)
        assert d.action == "open_csp"

    def test_blocks_when_iv_rank_below_floor(self, mock_feed, standard_state):
        mock_feed.iv_rank.return_value = 25.0
        cfg = WheelLiveConfig(min_iv_rank=50.0)
        d = WheelLive(mock_feed, config=cfg).decide(standard_state, as_of=TODAY)
        assert d.action == "block_iv_too_low"

    def test_proceeds_when_iv_rank_above_floor(self, mock_feed, standard_state):
        mock_feed.iv_rank.return_value = 75.0
        mock_feed.chain.return_value = [make_quote(190.0, "put")]
        cfg = WheelLiveConfig(min_iv_rank=50.0)
        d = WheelLive(mock_feed, config=cfg).decide(standard_state, as_of=TODAY)
        assert d.action == "open_csp"


# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------


def test_contract_lot_is_100():
    # If this ever changes, every contract-counting assertion above breaks.
    assert CONTRACT_LOT == 100
