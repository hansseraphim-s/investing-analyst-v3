"""Unit tests for broker option-order submission.

AlpacaBroker.submit_option_order is exercised with a mocked TradingClient
so the test doesn't hit the live API. PaperBroker.submit_option_order is
expected to raise NotImplementedError — the in-process simulator doesn't
model an options chain.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iav3.broker.alpaca import AlpacaBroker
from iav3.broker.paper import PaperBroker


# ----------------------------------------------------------------------
# PaperBroker
# ----------------------------------------------------------------------


class TestPaperBrokerOptionOrder:
    def test_raises_not_implemented(self, tmp_path):
        broker = PaperBroker(starting_cash=100_000.0, db_path=str(tmp_path / "p.db"))
        with pytest.raises(NotImplementedError, match="does not support option orders"):
            broker.submit_option_order("AAPL250620C00200000", 1, "BUY", 7.50)


# ----------------------------------------------------------------------
# AlpacaBroker (TradingClient mocked)
# ----------------------------------------------------------------------


@pytest.fixture
def alpaca_broker():
    with patch("alpaca.trading.client.TradingClient") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        broker = AlpacaBroker("test_key", "test_secret", paper=True)
        # Stash the mocked client on the broker so tests can configure it
        broker._mock_client = mock_client  # type: ignore[attr-defined]
        yield broker


class TestAlpacaBrokerOptionOrder:
    def test_submit_buy_at_limit(self, alpaca_broker):
        mock_order = MagicMock(id="o-1", status="accepted")
        alpaca_broker._mock_client.submit_order.return_value = mock_order
        result = alpaca_broker.submit_option_order(
            symbol="AAPL250620C00200000",
            qty=2,
            side="BUY",
            limit_price=7.55,
        )
        assert result.order_id == "o-1"
        assert result.symbol == "AAPL250620C00200000"
        assert result.qty == 2
        assert result.side == "BUY"

    def test_passes_correct_args_to_alpaca_py(self, alpaca_broker):
        mock_order = MagicMock(id="o-2", status="accepted")
        alpaca_broker._mock_client.submit_order.return_value = mock_order
        alpaca_broker.submit_option_order(
            "AAPL250620C00200000", qty=1, side="BUY", limit_price=7.50,
        )
        # Verify the LimitOrderRequest was built correctly
        args, _ = alpaca_broker._mock_client.submit_order.call_args
        req = args[0]
        assert req.symbol == "AAPL250620C00200000"
        assert req.qty == 1
        # alpaca-py uses enums for side / TIF — checking the values' string forms
        assert str(req.side).endswith("BUY") or str(req.side) == "OrderSide.BUY"
        assert str(req.time_in_force).endswith("DAY") or str(req.time_in_force) == "TimeInForce.DAY"
        assert req.limit_price == 7.50

    def test_rounds_limit_price_to_two_decimals(self, alpaca_broker):
        mock_order = MagicMock(id="o-3", status="accepted")
        alpaca_broker._mock_client.submit_order.return_value = mock_order
        alpaca_broker.submit_option_order(
            "AAPL250620C00200000", qty=1, side="BUY", limit_price=7.5567,
        )
        args, _ = alpaca_broker._mock_client.submit_order.call_args
        assert args[0].limit_price == 7.56

    def test_rejects_invalid_side(self, alpaca_broker):
        with pytest.raises(ValueError, match="BUY or SELL"):
            alpaca_broker.submit_option_order(
                "AAPL250620C00200000", qty=1, side="HOLD", limit_price=7.50,
            )

    def test_rejects_zero_or_negative_limit(self, alpaca_broker):
        with pytest.raises(ValueError, match="limit_price"):
            alpaca_broker.submit_option_order(
                "AAPL250620C00200000", qty=1, side="BUY", limit_price=0.0,
            )
        with pytest.raises(ValueError, match="limit_price"):
            alpaca_broker.submit_option_order(
                "AAPL250620C00200000", qty=1, side="BUY", limit_price=-1.0,
            )

    def test_rejects_zero_or_negative_qty(self, alpaca_broker):
        with pytest.raises(ValueError, match="qty"):
            alpaca_broker.submit_option_order(
                "AAPL250620C00200000", qty=0, side="BUY", limit_price=7.50,
            )

    def test_sell_side_also_works(self, alpaca_broker):
        mock_order = MagicMock(id="o-4", status="accepted")
        alpaca_broker._mock_client.submit_order.return_value = mock_order
        result = alpaca_broker.submit_option_order(
            "AAPL250620C00200000", qty=1, side="SELL", limit_price=7.55,
        )
        assert result.side == "SELL"
