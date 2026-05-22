"""Multi-factor composite scanner — picks the top-N from a universe.

Investment thesis the scan operationalizes:
  Stocks already moving up (short + medium momentum) and trading above
  their long-term trend, with elevated recent volume confirming the move,
  outperform passively-held alternatives.

The four factors, equal-weighted via z-score normalization:
  1. m20  — 20-day return                       (short-term momentum)
  2. m60  — 60-day return                       (medium-term momentum)
  3. trend — (close - 200d SMA) / 200d SMA      (regime alignment)
  4. vol_ratio — last 20d avg vol / prior 60d   (participation / confirmation)

Why z-score not raw values: factors have different units and natural
scales (returns are decimal, vol_ratio centers at 1.0). Z-scoring puts
them on a common scale before summing. Equal weighting is the
unbiased prior — any factor weighting beyond that is a separate
optimization decision that requires its own validation.

Batched data fetch via Alpaca's get_stock_bars (100 symbols/call),
so scanning 2,000 names is ~20 API calls = ~10-20 seconds total.
"""

from __future__ import annotations

import logging
import os
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanResult:
    symbol: str
    m20: float
    m60: float
    trend: float
    vol_ratio: float
    composite: float

    @property
    def factor_dict(self) -> dict[str, float]:
        return {
            "m20": self.m20,
            "m60": self.m60,
            "trend": self.trend,
            "vol_ratio": self.vol_ratio,
        }


def _safe_pct(later: float, earlier: float) -> float:
    if earlier <= 0:
        return 0.0
    return later / earlier - 1.0


def _zscore(values: list[float]) -> list[float]:
    """Z-score a list, returning zeros if stddev is too small to be meaningful."""
    if len(values) < 2:
        return [0.0] * len(values)
    mu = statistics.fmean(values)
    sd = statistics.pstdev(values)
    if sd < 1e-12:
        return [0.0] * len(values)
    return [(v - mu) / sd for v in values]


# Sanity filters applied per-symbol to reject corrupted or non-investable
# rows BEFORE z-scoring. Otherwise a single +26,000% row (reverse-split
# data corruption) dominates the entire distribution and pollutes the
# top-N pick.
MIN_PRICE = 5.0          # no penny stocks
MAX_ABS_M60 = 5.0        # ±500% over 60d is a split/data issue, not momentum
MAX_VOL_RATIO = 50.0     # vol ratios above ~50x are halts / reverse-split artifacts
MIN_AVG_VOL = 500_000.0  # avg daily volume floor — excludes thin / illiquid names


def _per_symbol_factors(closes: list[float], volumes: list[float]) -> dict[str, float] | None:
    """Compute m20 / m60 / trend / vol_ratio for one symbol.

    Returns None when:
      - history is too short for 200d SMA + 80d volume baseline
      - last price < $5 (penny stocks excluded for spread / data hygiene)
      - 60d return absolute value > 500% (data corruption / extreme split)
      - vol_ratio > 50x (halts or post-event spikes, not organic flow)
      - average daily volume < 500k (thin name, can't trade meaningfully)
    """
    if len(closes) < 200 or len(volumes) < 80:
        return None

    last = closes[-1]
    if last < MIN_PRICE:
        return None

    m20 = _safe_pct(last, closes[-21])
    m60 = _safe_pct(last, closes[-61])
    if abs(m60) > MAX_ABS_M60:
        return None

    sma200 = sum(closes[-200:]) / 200.0
    if sma200 <= 0:
        return None
    trend = (closes[-1] - sma200) / sma200

    v20 = sum(volumes[-20:]) / 20.0
    v60_pre = sum(volumes[-80:-20]) / 60.0
    if v20 < MIN_AVG_VOL:
        return None
    if v60_pre <= 0:
        vol_ratio = 1.0
    else:
        vol_ratio = v20 / v60_pre
    if vol_ratio > MAX_VOL_RATIO:
        return None

    return {"m20": m20, "m60": m60, "trend": trend, "vol_ratio": vol_ratio}


def scan_top_n(
    universe: list[str],
    *,
    top_n: int = 100,
    api_key: str | None = None,
    api_secret: str | None = None,
    days_back: int = 320,
    batch_size: int = 100,
) -> list[ScanResult]:
    """Fetch bars for the universe and return the top-N by composite z-score.

    Batched API calls limit network round-trips. Symbols with insufficient
    history are silently dropped (no Russell-1000 stock should have <200d
    history; small caps that JUST IPO'd will be filtered here).
    """
    api_key = api_key or os.environ.get("ALPACA_API_KEY")
    api_secret = api_secret or os.environ.get("ALPACA_API_SECRET") or os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not api_secret:
        raise RuntimeError("Alpaca credentials missing for scan")

    from alpaca.data.historical.stock import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(api_key, api_secret)
    start = datetime.now(timezone.utc) - timedelta(days=days_back)

    per_symbol: dict[str, dict[str, float]] = {}
    n_fetched = 0
    for i in range(0, len(universe), batch_size):
        batch = universe[i : i + batch_size]
        try:
            req = StockBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame.Day,
                start=start,
            )
            bars = client.get_stock_bars(req)
        except Exception as e:
            logger.warning("scan: batch %d-%d failed: %s", i, i + len(batch), e)
            continue

        data = getattr(bars, "data", {}) or {}
        for sym, sym_bars in data.items():
            closes = [float(b.close) for b in sym_bars]
            volumes = [float(b.volume) for b in sym_bars]
            n_fetched += 1
            factors = _per_symbol_factors(closes, volumes)
            if factors is not None:
                per_symbol[sym] = factors

    if not per_symbol:
        logger.warning("scan: no symbols with usable history (universe size %d)", len(universe))
        return []

    logger.info(
        "scan: %d symbols fetched, %d with full history (universe %d)",
        n_fetched, len(per_symbol), len(universe),
    )

    return _rank_top_n(per_symbol, top_n=top_n)


def _rank_top_n(
    per_symbol: dict[str, dict[str, float]],
    *,
    top_n: int,
) -> list[ScanResult]:
    """Z-score each factor across the universe, sum, sort, return top-N."""
    symbols = list(per_symbol.keys())
    factor_names = ("m20", "m60", "trend", "vol_ratio")
    factor_values = {f: [per_symbol[s][f] for s in symbols] for f in factor_names}
    factor_z = {f: _zscore(factor_values[f]) for f in factor_names}

    results: list[ScanResult] = []
    for i, sym in enumerate(symbols):
        composite = sum(factor_z[f][i] for f in factor_names)
        results.append(ScanResult(
            symbol=sym,
            m20=per_symbol[sym]["m20"],
            m60=per_symbol[sym]["m60"],
            trend=per_symbol[sym]["trend"],
            vol_ratio=per_symbol[sym]["vol_ratio"],
            composite=composite,
        ))

    results.sort(key=lambda r: r.composite, reverse=True)
    return results[:top_n]
