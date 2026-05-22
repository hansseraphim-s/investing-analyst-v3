"""IV-rank-gated long-call overlay (defined-risk).

Premise: when our trend filter is ON for a name AND IV rank is low (calls
are cheap relative to their own history), replace a small slice of
intended equity exposure with long calls. Defined risk: max loss is the
premium paid; no naked short legs.

Why IV rank gates this: buying ANY option carries the volatility risk
premium against you. Buying calls only when IV is in the bottom decile of
its history dampens that headwind. Conversely, buying high-IV calls is a
known way to donate premium — this gate is structural protection against
that pattern, not a forecast.

Sizing: capped at `overlay_pct_of_equity` of total equity per position
(default 5%). At 75 DTE ATM with typical IV, that converts to a delta
exposure roughly equivalent to a small underlying position; the rest of
the equity stays in cash or trend-following stock.

Not registered in the price-only Strategy registry — this is a LIVE
decision module that needs real Alpaca chain access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal

from ..data.alpaca_options import AlpacaOptionsFeed, OptionContract, OptionQuote

logger = logging.getLogger(__name__)

CONTRACT_LOT = 100

LongCallAction = Literal[
    "open_long_call",
    "hold",
    "block_trend_off",
    "block_iv_history_insufficient",
    "block_iv_too_high",
    "block_no_chain",
    "block_no_liquidity",
    "block_too_close_to_expiry",
    "block_insufficient_cash",
    "block_sizing_below_one_contract",
]


@dataclass(frozen=True)
class LongCallOverlayConfig:
    iv_rank_max: float = 30.0                  # only buy when iv_rank < this
    target_dte: int = 75                       # 60-90 DTE sweet spot
    target_delta: float = 0.50                 # ATM by default
    overlay_pct_of_equity: float = 0.05        # 5% of equity per overlay position
    min_premium_per_share: float = 1.00        # gate cheap-but-illiquid contracts
    max_spread_pct: float = 0.20               # bid/ask gate (tighter than wheel since we're paying)
    min_dte_to_open: int = 30                  # avoid theta-rich short-dated calls


@dataclass(frozen=True)
class LongCallDecision:
    action: LongCallAction
    contract: OptionContract | None
    quote: OptionQuote | None
    contracts: int
    debit_per_contract: float       # $/share * 100 (what we pay per contract)
    total_debit: float              # contracts * debit_per_contract
    rationale: str

    @property
    def is_opening(self) -> bool:
        return self.action == "open_long_call"

    @property
    def is_block(self) -> bool:
        return self.action.startswith("block_")


class LongCallOverlay:
    """Live decision engine for the IV-rank long-call overlay strategy.

    Called once per cycle, per underlying, from the engine. Inputs are
    state the engine already has (trend signal, spot price, cash, equity);
    Alpaca chain access is handled by the injected feed.
    """

    def __init__(
        self,
        feed: AlpacaOptionsFeed,
        *,
        config: LongCallOverlayConfig | None = None,
    ) -> None:
        self.feed = feed
        self.config = config or LongCallOverlayConfig()

    def decide(
        self,
        *,
        underlying: str,
        spot: float,
        trend_on: bool,
        available_cash: float,
        equity: float,
        as_of: date | None = None,
    ) -> LongCallDecision:
        cfg = self.config
        today = as_of or datetime.now(timezone.utc).date()

        # 1. Trend filter — overlay only fires when the underlying strategy
        # would also want to be long. We don't take directional bets the base
        # strategy isn't taking.
        if not trend_on:
            return _block(
                "block_trend_off",
                f"Trend filter is OFF for {underlying}; no overlay entry.",
            )

        # 2. IV rank gate. Must have enough history to compute rank, and rank
        # must be below the ceiling.
        iv_rank = self.feed.iv_rank(underlying)
        if iv_rank is None:
            return _block(
                "block_iv_history_insufficient",
                f"IV rank history for {underlying} has fewer than 60 obs; "
                f"overlay gate not active yet.",
            )
        if iv_rank > cfg.iv_rank_max:
            return _block(
                "block_iv_too_high",
                f"IV rank {iv_rank:.0f} > ceiling {cfg.iv_rank_max:.0f} "
                f"(calls are not historically cheap; pass).",
            )

        # 3. Find a chain matching target DTE.
        try:
            expiry = self.feed.nearest_expiry(underlying, cfg.target_dte, as_of=today)
        except LookupError as e:
            logger.warning("nearest_expiry(%s) failed: %s", underlying, e)
            return _block(
                "block_no_chain",
                f"No listed call expiries for {underlying}.",
            )

        dte = (expiry - today).days
        if dte < cfg.min_dte_to_open:
            return _block(
                "block_too_close_to_expiry",
                f"Picked expiry {expiry} is {dte}d out, below min_dte_to_open={cfg.min_dte_to_open}.",
            )

        # 4. Pick the contract closest to target delta. Use a band around
        # spot to keep the chain query tight.
        band = max(spot * 0.30, 50.0)
        chain = self.feed.chain(
            underlying,
            expiry=expiry,
            option_type="call",
            min_strike=spot - band,
            max_strike=spot + band,
        )
        if not chain:
            return _block(
                "block_no_chain",
                f"Empty call chain for {underlying} exp {expiry}.",
            )

        # Filter to chain entries that report Greeks — we cannot pick by
        # delta if delta is None.
        with_delta = [q for q in chain if q.delta is not None]
        if not with_delta:
            return _block(
                "block_no_chain",
                f"Chain returned no quotes with Greeks for {underlying} exp {expiry}.",
            )
        pick = min(with_delta, key=lambda q: abs((q.delta or 0.0) - cfg.target_delta))

        # 5. Liquidity gate.
        liq_block = _check_liquidity(pick, cfg)
        if liq_block is not None:
            return liq_block

        # 6. Sizing — overlay_pct_of_equity, converted to contract count.
        debit_per_contract = pick.mid * CONTRACT_LOT
        budget = equity * cfg.overlay_pct_of_equity
        contracts = int(budget // debit_per_contract)
        if contracts < 1:
            return _block(
                "block_sizing_below_one_contract",
                f"Budget ${budget:,.0f} < 1 contract @ ${debit_per_contract:,.0f}. "
                f"Lower overlay_pct_of_equity or pick a cheaper strike.",
            )
        total_debit = contracts * debit_per_contract
        if total_debit > available_cash:
            return _block(
                "block_insufficient_cash",
                f"Total debit ${total_debit:,.0f} > available cash ${available_cash:,.0f}.",
            )

        rationale = (
            f"Buy {contracts}x {underlying} {expiry} call @ ${pick.contract.strike:.2f} "
            f"(spot ${spot:.2f}, Δ {pick.delta:.2f}, DTE {dte}, IV rank {iv_rank:.0f}, "
            f"mid ${pick.mid:.2f}, total debit ${total_debit:,.0f}). "
            f"Max loss = debit paid."
        )
        return LongCallDecision(
            action="open_long_call",
            contract=pick.contract,
            quote=pick,
            contracts=contracts,
            debit_per_contract=debit_per_contract,
            total_debit=total_debit,
            rationale=rationale,
        )


def _block(action: LongCallAction, rationale: str) -> LongCallDecision:
    return LongCallDecision(
        action=action,
        contract=None,
        quote=None,
        contracts=0,
        debit_per_contract=0.0,
        total_debit=0.0,
        rationale=rationale,
    )


def _check_liquidity(q: OptionQuote, cfg: LongCallOverlayConfig) -> LongCallDecision | None:
    if q.bid <= 0 or q.mid <= 0:
        return _block(
            "block_no_liquidity",
            f"Bid ${q.bid:.2f} / mid ${q.mid:.2f} — no two-sided market.",
        )
    if q.spread_pct > cfg.max_spread_pct:
        return _block(
            "block_no_liquidity",
            f"Spread {q.spread_pct:.0%} > max {cfg.max_spread_pct:.0%}.",
        )
    if q.mid < cfg.min_premium_per_share:
        return _block(
            "block_no_liquidity",
            f"Mid ${q.mid:.2f} < min premium ${cfg.min_premium_per_share:.2f}/share.",
        )
    return None
