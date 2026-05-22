"""Unit tests for the Alpaca options adapter.

These tests cover only the pure-logic surface (OCC symbol parsing,
helper math). The network-dependent paths (chain, latest_quote, etc.)
are covered by integration tests that require live Alpaca creds and
are not part of the default `make agent-test` run.
"""

from __future__ import annotations

from datetime import date

import pytest

from iav3.data.alpaca_options import (
    OptionContract,
    _mid,
    _safe_float,
    parse_occ,
)


class TestParseOcc:
    def test_call_basic(self):
        c = parse_occ("AAPL250620C00200000")
        assert c.underlying == "AAPL"
        assert c.option_type == "call"
        assert c.strike == 200.0
        assert c.expiry == date(2025, 6, 20)
        assert c.symbol == "AAPL250620C00200000"

    def test_put_basic(self):
        c = parse_occ("MSFT250117P00350000")
        assert c.underlying == "MSFT"
        assert c.option_type == "put"
        assert c.strike == 350.0
        assert c.expiry == date(2025, 1, 17)

    def test_fractional_strike(self):
        c = parse_occ("SPY250620C00450500")
        assert c.strike == 450.5

    def test_sub_dollar_strike(self):
        c = parse_occ("ABCD250620C00000500")
        assert c.strike == 0.5

    def test_multi_char_underlying(self):
        c = parse_occ("GOOGL250620C00150000")
        assert c.underlying == "GOOGL"
        assert c.strike == 150.0

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "AAPL",
            "AAPL250620X00200000",          # invalid option type
            "AAPL2506C00200000",             # malformed date
            "AAPL250620C0020000",            # 7-digit strike
            "aapl250620C00200000",           # lowercase underlying
            "AAPL250620C002000000",          # 9-digit strike
        ],
    )
    def test_rejects_malformed(self, bad):
        with pytest.raises(ValueError):
            parse_occ(bad)


class TestMidHelper:
    def test_both_sides_present(self):
        assert _mid(10.0, 12.0) == 11.0

    def test_zero_bid(self):
        # When bid is 0 we fall back to ask (a one-sided quote)
        assert _mid(0.0, 12.0) == 12.0

    def test_zero_ask(self):
        # And vice versa
        assert _mid(10.0, 0.0) == 10.0

    def test_both_zero(self):
        assert _mid(0.0, 0.0) == 0.0

    def test_none_inputs(self):
        # alpaca-py can return None for empty quotes
        assert _mid(None, None) == 0.0
        assert _mid(None, 5.0) == 5.0
        assert _mid(5.0, None) == 5.0


class TestSafeFloat:
    def test_valid_number(self):
        assert _safe_float(1.5) == 1.5
        assert _safe_float("2.5") == 2.5

    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_nan_returns_none(self):
        # alpaca-py occasionally returns NaN for missing Greeks
        assert _safe_float(float("nan")) is None

    def test_garbage_returns_none(self):
        assert _safe_float("not a number") is None
        assert _safe_float({}) is None


class TestOptionContract:
    def test_dataclass_immutable(self):
        c = OptionContract(
            symbol="AAPL250620C00200000",
            underlying="AAPL",
            option_type="call",
            strike=200.0,
            expiry=date(2025, 6, 20),
        )
        with pytest.raises(Exception):
            c.strike = 201.0  # type: ignore[misc]
