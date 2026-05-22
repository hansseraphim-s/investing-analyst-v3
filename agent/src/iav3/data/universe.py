"""US equity universe — the "1000-stock pre-market scan" input.

Fetched at cycle time from Alpaca's tradeable-assets endpoint rather
than hard-coded, so the universe stays current with corporate actions
(IPOs, delistings, mergers) automatically.

Filters (each one removes some bad names):
  - active + tradable (Alpaca can route orders)
  - marginable (rough liquidity proxy)
  - fractionable (Alpaca's "we'll let users buy 0.1 shares of this" gate —
    only enabled on large + liquid names; this is the cleanest size filter
    short of querying market cap individually)
  - on NASDAQ / NYSE / ARCA (excludes OTC, pink sheets, foreign primary)
  - no class-share dots (yfinance compatibility for fallback paths)
  - symbol length 1-5 (excludes obviously-corrupt rows)

Expected size: 1,500-2,500 names. The scanner downsamples to top-100
by composite score.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def fetch_us_equity_universe(
    api_key: str | None = None,
    api_secret: str | None = None,
    *,
    paper: bool = True,
) -> list[str]:
    """Return the current Alpaca-tradeable US equity universe (sorted).

    Network call — caller should cache for the duration of a cycle, not
    re-fetch per scan invocation. ~2,000-3,500 symbols typical.
    """
    api_key = api_key or os.environ.get("ALPACA_API_KEY")
    api_secret = api_secret or os.environ.get("ALPACA_API_SECRET") or os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not api_secret:
        raise RuntimeError(
            "Alpaca credentials missing for universe fetch. "
            "Set ALPACA_API_KEY + ALPACA_API_SECRET in .env."
        )

    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AssetClass, AssetStatus, AssetExchange
    from alpaca.trading.requests import GetAssetsRequest

    client = TradingClient(api_key, api_secret, paper=paper)
    req = GetAssetsRequest(
        status=AssetStatus.ACTIVE,
        asset_class=AssetClass.US_EQUITY,
    )
    assets = client.get_all_assets(req)

    allowed_exchanges = {
        AssetExchange.NASDAQ,
        AssetExchange.NYSE,
        AssetExchange.ARCA,
    }
    universe: set[str] = set()
    for a in assets:
        if not getattr(a, "tradable", False):
            continue
        if not getattr(a, "marginable", False):
            continue
        # `shortable` + `easy_to_borrow` are Alpaca's strict liquidity gates.
        # Penny stocks, recent IPOs, hard-to-borrow special situations all
        # fail this filter. Combined with `fractionable` we get the actual
        # "blue-chip + liquid mid-cap" subset (~1500-2500 names).
        if not getattr(a, "shortable", False):
            continue
        if not getattr(a, "easy_to_borrow", False):
            continue
        if not getattr(a, "fractionable", False):
            continue
        if getattr(a, "exchange", None) not in allowed_exchanges:
            continue
        sym = getattr(a, "symbol", "") or ""
        if not sym or "." in sym or len(sym) > 5:
            continue
        universe.add(sym)

    out = sorted(universe)
    logger.info("Universe fetched: %d symbols", len(out))
    return out
