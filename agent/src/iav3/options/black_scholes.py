"""Black-Scholes-Merton European option pricing — dependency-free.

Uses math.erf for the normal CDF (no scipy). This is a *model*. Real options
are American, dividends matter, and — most importantly for this project —
the implied volatility you must feed it is NOT observable from free data.
Everything downstream that uses this is a model approximation, labeled as
such. See options/wheel.py for the volatility assumption and its caveat.
"""

from __future__ import annotations

import math

_SQRT2 = math.sqrt(2.0)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _intrinsic(option_type: str, s: float, k: float) -> float:
    return max(s - k, 0.0) if option_type == "call" else max(k - s, 0.0)


def bs_price(
    option_type: str,
    s: float,
    k: float,
    t_years: float,
    r: float,
    sigma: float,
) -> float:
    """European call/put price.

    Degenerate inputs collapse to discounted intrinsic value rather than
    raising, so a backtest loop never crashes on an expiry-day bar.
    """
    option_type = option_type.lower()
    if option_type not in {"call", "put"}:
        raise ValueError(f"option_type must be 'call' or 'put'; got {option_type!r}")
    if s <= 0.0 or k <= 0.0:
        return 0.0
    if t_years <= 0.0 or sigma <= 0.0:
        # No time or no vol -> value is just (discounted) intrinsic.
        disc_k = k * math.exp(-r * max(t_years, 0.0))
        if option_type == "call":
            return max(s - disc_k, 0.0)
        return max(disc_k - s, 0.0)

    vol_sqrt_t = sigma * math.sqrt(t_years)
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t_years) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    disc = math.exp(-r * t_years)
    if option_type == "call":
        return s * _norm_cdf(d1) - k * disc * _norm_cdf(d2)
    return k * disc * _norm_cdf(-d2) - s * _norm_cdf(-d1)


def bs_delta(
    option_type: str,
    s: float,
    k: float,
    t_years: float,
    r: float,
    sigma: float,
) -> float:
    """Option delta. Call in [0,1], put in [-1,0]."""
    option_type = option_type.lower()
    if s <= 0.0 or k <= 0.0 or t_years <= 0.0 or sigma <= 0.0:
        intrinsic_itm = _intrinsic(option_type, s, k) > 0.0
        if option_type == "call":
            return 1.0 if intrinsic_itm else 0.0
        return -1.0 if intrinsic_itm else 0.0
    vol_sqrt_t = sigma * math.sqrt(t_years)
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t_years) / vol_sqrt_t
    return _norm_cdf(d1) if option_type == "call" else _norm_cdf(d1) - 1.0
