"""Unit tests for the IV-rank long-call overlay."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from iav3.data.alpaca_options import OptionContract, OptionQuote
from iav3.options.long_call_overlay import (
    LongCallOverlay,
    LongCallOverlayConfig,
)


TODAY = date(2026, 6, 1)


def _contract(strike: float, dte: int = 75) -> OptionContract:
    expiry = TODAY + timedelta(days=dte)
    return OptionContract(
        symbol=f"AAPL{expiry.strftime('%y%m%d')}C{int(strike * 1000):08d}",
        underlying="AAPL",
        option_type="call",
        strike=strike,
        expiry=expiry,
    )


def _quote(
    strike: float,
    *,
    delta: float = 0.50,
    iv: float = 0.25,
    bid: float = 7.40,
    ask: float = 7.60,
    dte: int = 75,
) -> OptionQuote:
    contract = _contract(strike, dte)
    mid = (bid + ask) / 2
    return OptionQuote(
        contract=contract, bid=bid, ask=ask, last=mid, mid=mid,
        iv=iv, delta=delta, gamma=0.02, vega=0.30, theta=-0.04, rho=0.05,
        open_interest=1000, volume=50,
        quoted_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def mock_feed():
    feed = MagicMock()
    feed.nearest_expiry.return_value = TODAY + timedelta(days=75)
    feed.iv_rank.return_value = 20.0  # default: low IV, gate passes
    feed.chain.return_value = [
        _quote(195.0, delta=0.55),
        _quote(200.0, delta=0.50),  # closest to target_delta=0.50
        _quote(205.0, delta=0.45),
    ]
    return feed


@pytest.fixture
def base_args():
    return dict(
        underlying="AAPL",
        spot=200.0,
        trend_on=True,
        available_cash=100_000.0,
        equity=100_000.0,
        as_of=TODAY,
    )


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------


class TestHappyPath:
    def test_opens_long_call_at_target_delta(self, mock_feed, base_args):
        d = LongCallOverlay(mock_feed).decide(**base_args)
        assert d.action == "open_long_call"
        assert d.contract is not None
        assert d.contract.strike == 200.0   # ATM, closest to target_delta=0.50
        assert d.contracts >= 1
        # overlay_pct_of_equity = 0.05, equity = 100k → budget = 5k
        # debit_per_contract = 7.50 * 100 = 750, so contracts = 5000 // 750 = 6
        assert d.contracts == 6
        assert d.total_debit == pytest.approx(d.contracts * d.debit_per_contract)
        assert d.is_opening


# ----------------------------------------------------------------------
# Trend gate
# ----------------------------------------------------------------------


class TestTrendGate:
    def test_blocks_when_trend_off(self, mock_feed, base_args):
        d = LongCallOverlay(mock_feed).decide(**{**base_args, "trend_on": False})
        assert d.action == "block_trend_off"
        mock_feed.iv_rank.assert_not_called()  # short-circuited before IV lookup


# ----------------------------------------------------------------------
# IV rank gate
# ----------------------------------------------------------------------


class TestIvRankGate:
    def test_blocks_when_iv_history_insufficient(self, mock_feed, base_args):
        mock_feed.iv_rank.return_value = None
        d = LongCallOverlay(mock_feed).decide(**base_args)
        assert d.action == "block_iv_history_insufficient"

    def test_blocks_when_iv_rank_above_ceiling(self, mock_feed, base_args):
        mock_feed.iv_rank.return_value = 75.0  # IV rich, do not buy
        d = LongCallOverlay(mock_feed).decide(**base_args)
        assert d.action == "block_iv_too_high"
        assert "75" in d.rationale

    def test_opens_when_iv_rank_low(self, mock_feed, base_args):
        mock_feed.iv_rank.return_value = 10.0  # IV very cheap
        d = LongCallOverlay(mock_feed).decide(**base_args)
        assert d.action == "open_long_call"

    def test_custom_ceiling(self, mock_feed, base_args):
        mock_feed.iv_rank.return_value = 40.0
        # default ceiling 30 → blocked
        d = LongCallOverlay(mock_feed).decide(**base_args)
        assert d.action == "block_iv_too_high"
        # custom ceiling 50 → opens
        cfg = LongCallOverlayConfig(iv_rank_max=50.0)
        d = LongCallOverlay(mock_feed, config=cfg).decide(**base_args)
        assert d.action == "open_long_call"


# ----------------------------------------------------------------------
# Chain / expiry gates
# ----------------------------------------------------------------------


class TestChainGates:
    def test_blocks_when_no_expiries(self, mock_feed, base_args):
        mock_feed.nearest_expiry.side_effect = LookupError("no expiries")
        d = LongCallOverlay(mock_feed).decide(**base_args)
        assert d.action == "block_no_chain"

    def test_blocks_when_chain_empty(self, mock_feed, base_args):
        mock_feed.chain.return_value = []
        d = LongCallOverlay(mock_feed).decide(**base_args)
        assert d.action == "block_no_chain"

    def test_blocks_when_no_quotes_have_delta(self, mock_feed, base_args):
        # Chain has entries but Greeks are missing — can't pick by delta
        no_greeks = [_quote(200.0, delta=None)]  # type: ignore[arg-type]
        mock_feed.chain.return_value = no_greeks
        d = LongCallOverlay(mock_feed).decide(**base_args)
        assert d.action == "block_no_chain"

    def test_blocks_when_expiry_too_close(self, mock_feed, base_args):
        mock_feed.nearest_expiry.return_value = TODAY + timedelta(days=10)
        # Default min_dte_to_open=30; 10d expiry blocks
        d = LongCallOverlay(mock_feed).decide(**base_args)
        assert d.action == "block_too_close_to_expiry"


# ----------------------------------------------------------------------
# Liquidity
# ----------------------------------------------------------------------


class TestLiquidity:
    def test_blocks_on_wide_spread(self, mock_feed, base_args):
        mock_feed.chain.return_value = [_quote(200.0, bid=5.0, ask=10.0)]  # 67% spread
        d = LongCallOverlay(mock_feed).decide(**base_args)
        assert d.action == "block_no_liquidity"
        assert "spread" in d.rationale.lower()

    def test_blocks_on_zero_bid(self, mock_feed, base_args):
        mock_feed.chain.return_value = [_quote(200.0, bid=0.0, ask=1.0)]
        d = LongCallOverlay(mock_feed).decide(**base_args)
        assert d.action == "block_no_liquidity"

    def test_blocks_on_low_mid(self, mock_feed, base_args):
        mock_feed.chain.return_value = [_quote(200.0, bid=0.40, ask=0.45)]  # mid below 1.00
        d = LongCallOverlay(mock_feed).decide(**base_args)
        assert d.action == "block_no_liquidity"
        assert "min premium" in d.rationale.lower()


# ----------------------------------------------------------------------
# Sizing
# ----------------------------------------------------------------------


class TestSizing:
    def test_blocks_when_sizing_below_one_contract(self, mock_feed, base_args):
        # Tiny equity → budget 5% * 1000 = $50 < $750 per contract
        d = LongCallOverlay(mock_feed).decide(**{**base_args, "equity": 1_000.0, "available_cash": 1_000.0})
        assert d.action == "block_sizing_below_one_contract"

    def test_blocks_when_insufficient_cash(self, mock_feed, base_args):
        # Equity 100k → budget 5k → 6 contracts × $750 = $4500
        # Available cash 1k blocks insufficient_cash
        d = LongCallOverlay(mock_feed).decide(**{**base_args, "available_cash": 1_000.0})
        assert d.action == "block_insufficient_cash"

    def test_custom_overlay_pct(self, mock_feed, base_args):
        cfg = LongCallOverlayConfig(overlay_pct_of_equity=0.10)  # 10% instead of 5%
        d = LongCallOverlay(mock_feed, config=cfg).decide(**base_args)
        # 10k budget / $750 = 13 contracts
        assert d.action == "open_long_call"
        assert d.contracts == 13
