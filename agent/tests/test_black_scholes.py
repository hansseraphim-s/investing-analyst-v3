import math

import pytest

from iav3.options import bs_delta, bs_price


def test_put_call_parity():
    s, k, t, r, sig = 100.0, 95.0, 0.5, 0.04, 0.25
    c = bs_price("call", s, k, t, r, sig)
    p = bs_price("put", s, k, t, r, sig)
    # C - P == S - K*e^{-rT}
    assert math.isclose(c - p, s - k * math.exp(-r * t), rel_tol=1e-9, abs_tol=1e-7)


def test_prices_non_negative_and_monotonic_in_vol():
    base = bs_price("call", 100, 100, 0.5, 0.04, 0.10)
    more = bs_price("call", 100, 100, 0.5, 0.04, 0.40)
    assert base >= 0 and more >= 0
    assert more > base  # higher vol -> more expensive option


def test_call_monotonic_in_spot():
    lo = bs_price("call", 90, 100, 0.5, 0.04, 0.25)
    hi = bs_price("call", 110, 100, 0.5, 0.04, 0.25)
    assert hi > lo


def test_expiry_collapses_to_intrinsic():
    assert bs_price("call", 120, 100, 0.0, 0.04, 0.25) == pytest.approx(20.0)
    assert bs_price("put", 80, 100, 0.0, 0.04, 0.25) == pytest.approx(20.0)
    assert bs_price("call", 90, 100, 0.0, 0.04, 0.25) == 0.0


def test_zero_vol_is_discounted_intrinsic():
    s, k, t, r = 100.0, 100.0, 1.0, 0.05
    # With no vol an ATM call is worth max(S - K e^{-rT}, 0).
    expected = max(s - k * math.exp(-r * t), 0.0)
    assert bs_price("call", s, k, t, r, 0.0) == pytest.approx(expected)


def test_delta_bounds():
    cd = bs_delta("call", 100, 100, 0.5, 0.04, 0.25)
    pd_ = bs_delta("put", 100, 100, 0.5, 0.04, 0.25)
    assert 0.0 <= cd <= 1.0
    assert -1.0 <= pd_ <= 0.0
    # Deep ITM call delta -> ~1; deep OTM put delta -> ~0.
    assert bs_delta("call", 1000, 100, 0.5, 0.04, 0.25) == pytest.approx(1.0, abs=1e-6)


def test_bad_option_type_raises():
    with pytest.raises(ValueError):
        bs_price("straddle", 100, 100, 0.5, 0.04, 0.25)
