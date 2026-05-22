"""Unit tests for the multi-factor scanner.

The network paths (Alpaca asset list + bars fetch) are not exercised
here — they require live credentials and live data, which is the
integration-test surface. These tests cover the pure-logic core:
per-symbol factor math, z-scoring, ranking, and degenerate edges.
"""

from __future__ import annotations

import pytest

from iav3.data.scanner import (
    ScanResult,
    _per_symbol_factors,
    _rank_top_n,
    _safe_pct,
    _zscore,
)


# ----------------------------------------------------------------------
# _safe_pct
# ----------------------------------------------------------------------


class TestSafePct:
    def test_basic(self):
        assert _safe_pct(110.0, 100.0) == pytest.approx(0.10)

    def test_negative_return(self):
        assert _safe_pct(90.0, 100.0) == pytest.approx(-0.10)

    def test_zero_denominator_returns_zero(self):
        assert _safe_pct(100.0, 0.0) == 0.0

    def test_negative_denominator_returns_zero(self):
        assert _safe_pct(100.0, -1.0) == 0.0


# ----------------------------------------------------------------------
# _zscore
# ----------------------------------------------------------------------


class TestZScore:
    def test_returns_zero_for_short_input(self):
        assert _zscore([]) == []
        assert _zscore([1.0]) == [0.0]

    def test_returns_zero_for_zero_stddev(self):
        # All identical values → stddev=0 → return all zeros (avoid div by zero)
        assert _zscore([5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]

    def test_symmetric_values_have_zero_mean(self):
        z = _zscore([-1.0, 0.0, 1.0])
        # mean is 0, stddev is ~0.816 → z = [-1.22, 0, 1.22]
        assert abs(z[0] + z[2]) < 1e-9  # symmetric
        assert abs(z[1]) < 1e-9          # middle is 0

    def test_sign_preserved(self):
        z = _zscore([1.0, 2.0, 3.0, 4.0, 5.0])
        # Below-mean values are negative; above-mean are positive
        assert z[0] < 0
        assert z[-1] > 0


# ----------------------------------------------------------------------
# _per_symbol_factors
# ----------------------------------------------------------------------


def _flat_history(price: float = 100.0, n: int = 250) -> tuple[list[float], list[float]]:
    """A flat-price, flat-volume history of length n."""
    return [price] * n, [1_000_000.0] * n


def _trending_history(start: float = 50.0, end: float = 150.0, n: int = 250):
    """A linearly-trending price history."""
    step = (end - start) / (n - 1)
    closes = [start + i * step for i in range(n)]
    vols = [1_000_000.0] * n
    return closes, vols


class TestPerSymbolFactors:
    def test_returns_none_on_short_history(self):
        # Strategy needs 200d of close + 80d of volume; <200 closes → None
        assert _per_symbol_factors([100.0] * 50, [1e6] * 50) is None

    def test_flat_history_yields_zero_momentum_and_trend(self):
        closes, vols = _flat_history(price=100.0)
        f = _per_symbol_factors(closes, vols)
        assert f is not None
        assert f["m20"] == 0.0
        assert f["m60"] == 0.0
        assert f["trend"] == 0.0
        assert f["vol_ratio"] == pytest.approx(1.0)  # constant volume

    def test_uptrend_yields_positive_momentum(self):
        closes, vols = _trending_history(start=50.0, end=150.0, n=250)
        f = _per_symbol_factors(closes, vols)
        assert f is not None
        assert f["m20"] > 0
        assert f["m60"] > 0
        assert f["trend"] > 0

    def test_recent_volume_spike_yields_vol_ratio_above_one(self):
        n = 250
        closes = [100.0] * n
        # First (n-20) days at 1M, last 20 days at 5M
        vols = [1_000_000.0] * (n - 20) + [5_000_000.0] * 20
        f = _per_symbol_factors(closes, vols)
        assert f is not None
        assert f["vol_ratio"] == pytest.approx(5.0, rel=0.1)


# ----------------------------------------------------------------------
# _rank_top_n
# ----------------------------------------------------------------------


class TestRankTopN:
    def test_returns_at_most_top_n(self):
        per_symbol = {
            f"SYM{i:03d}": {"m20": 0.1 * i, "m60": 0.1 * i, "trend": 0.1 * i, "vol_ratio": 1.0}
            for i in range(50)
        }
        results = _rank_top_n(per_symbol, top_n=10)
        assert len(results) == 10

    def test_ranks_by_composite_descending(self):
        per_symbol = {
            "LOSER":   {"m20": -0.5, "m60": -0.5, "trend": -0.5, "vol_ratio": 0.5},
            "MIDDLE":  {"m20":  0.0, "m60":  0.0, "trend":  0.0, "vol_ratio": 1.0},
            "WINNER":  {"m20":  0.5, "m60":  0.5, "trend":  0.5, "vol_ratio": 2.0},
        }
        results = _rank_top_n(per_symbol, top_n=10)
        assert [r.symbol for r in results] == ["WINNER", "MIDDLE", "LOSER"]

    def test_returns_empty_for_empty_input(self):
        assert _rank_top_n({}, top_n=10) == []

    def test_results_carry_raw_factor_values(self):
        per_symbol = {
            "AAPL": {"m20": 0.12, "m60": 0.30, "trend": 0.18, "vol_ratio": 1.5},
            "MSFT": {"m20": 0.08, "m60": 0.22, "trend": 0.12, "vol_ratio": 1.2},
        }
        results = _rank_top_n(per_symbol, top_n=5)
        aapl = next(r for r in results if r.symbol == "AAPL")
        assert aapl.m20 == 0.12
        assert aapl.m60 == 0.30
        assert aapl.trend == 0.18
        assert aapl.vol_ratio == 1.5

    def test_composite_is_z_score_sum(self):
        # Two symbols, factors [-1, +1] each. Z-scores are [-1, +1] for each factor.
        # Composite for each is sum of its z's = -4 and +4.
        per_symbol = {
            "LOW":  {"m20": -1.0, "m60": -1.0, "trend": -1.0, "vol_ratio": -1.0},
            "HIGH": {"m20":  1.0, "m60":  1.0, "trend":  1.0, "vol_ratio":  1.0},
        }
        results = _rank_top_n(per_symbol, top_n=2)
        winner = results[0]
        loser = results[1]
        assert winner.symbol == "HIGH"
        assert loser.symbol == "LOW"
        # 4 z-scores each at +1 → composite = +4 (or -4 for the loser)
        assert winner.composite == pytest.approx(4.0)
        assert loser.composite == pytest.approx(-4.0)
