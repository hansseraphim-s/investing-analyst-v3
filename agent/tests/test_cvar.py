"""Unit tests for the CVaR daily-loss breaker."""

from __future__ import annotations

import pytest

from iav3.risk.cvar import (
    MIN_HISTORY,
    check_cvar_breaker,
    cvar_threshold,
)


class TestCvarThreshold:
    def test_insufficient_history_returns_zero(self):
        assert cvar_threshold([], alpha=0.05) == 0.0
        assert cvar_threshold([-0.01] * (MIN_HISTORY - 1), alpha=0.05) == 0.0

    def test_threshold_is_mean_of_worst_alpha(self):
        # 100 days of returns, evenly spaced from -0.05 to +0.05
        history = [-0.05 + i * 0.001 for i in range(100)]
        # Worst 5% = 5 obs: [-0.05, -0.049, -0.048, -0.047, -0.046]
        # Mean = -0.048
        t = cvar_threshold(history, alpha=0.05)
        assert t == pytest.approx(-0.048, abs=1e-4)

    def test_threshold_is_negative_with_realistic_history(self):
        # Realistic-ish daily returns
        history = [0.001, -0.005, 0.012, -0.020, 0.008, -0.015, 0.003, -0.025] * 10
        t = cvar_threshold(history, alpha=0.05)
        assert t < 0  # CVaR of any historical sample with losses should be negative

    def test_alpha_must_be_in_open_unit_interval(self):
        history = [0.0] * 50
        with pytest.raises(ValueError):
            cvar_threshold(history, alpha=0.0)
        with pytest.raises(ValueError):
            cvar_threshold(history, alpha=1.0)
        with pytest.raises(ValueError):
            cvar_threshold(history, alpha=-0.1)

    def test_smaller_alpha_yields_more_extreme_threshold(self):
        history = [-0.05 + i * 0.001 for i in range(100)]
        t_5 = cvar_threshold(history, alpha=0.05)   # worst 5%
        t_10 = cvar_threshold(history, alpha=0.10)  # worst 10%
        # Smaller alpha = deeper tail = more negative threshold
        assert t_5 < t_10

    def test_at_least_one_observation_in_bucket_even_at_tiny_alpha(self):
        history = [-0.10, -0.05, -0.01, 0.0, 0.01, 0.05] * 10  # 60 obs
        t = cvar_threshold(history, alpha=0.001)  # absurdly small alpha
        # cutoff_idx clamped to 1 → threshold = worst single return = -0.10
        assert t == pytest.approx(-0.10, abs=1e-6)


class TestCheckCvarBreaker:
    def test_inactive_with_short_history(self):
        d = check_cvar_breaker(-5.0, [], alpha=0.05)
        assert d.breached is False
        assert d.cvar_threshold_pct == 0.0
        assert "inactive" in d.detail.lower()
        assert d.history_size == 0

    def test_breach_when_day_pnl_below_threshold(self):
        # Threshold from this history = -4.8% (per the test above scaled to %)
        history = [-0.05 + i * 0.001 for i in range(100)]
        # Today's loss is -5%, which is worse than threshold -4.8%
        d = check_cvar_breaker(-5.0, history, alpha=0.05)
        assert d.breached is True
        assert d.cvar_threshold_pct == pytest.approx(-4.8, abs=0.01)
        assert "breached" in d.detail.lower()

    def test_no_breach_when_day_pnl_above_threshold(self):
        history = [-0.05 + i * 0.001 for i in range(100)]
        # Today's loss is only -1%, well above the -4.8% threshold
        d = check_cvar_breaker(-1.0, history, alpha=0.05)
        assert d.breached is False
        assert "above" in d.detail.lower()

    def test_no_breach_on_positive_day_pnl(self):
        history = [-0.05 + i * 0.001 for i in range(100)]
        d = check_cvar_breaker(+2.5, history, alpha=0.05)
        assert d.breached is False

    def test_history_size_reported_correctly(self):
        history = [-0.01] * 50 + [0.01] * 50
        d = check_cvar_breaker(-2.0, history, alpha=0.05)
        assert d.history_size == 100

    def test_decision_carries_alpha_in_detail(self):
        # Just confirms the alpha value is surfaced in the rationale
        history = [-0.05 + i * 0.001 for i in range(100)]
        d = check_cvar_breaker(-2.0, history, alpha=0.10)
        assert "10%" in d.detail
