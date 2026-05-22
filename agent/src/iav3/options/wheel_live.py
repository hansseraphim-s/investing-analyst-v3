"""Live wheel decision engine.

Decides what to do RIGHT NOW for a single underlying that's part of the
wheel: open a cash-secured put, open a covered call against held shares,
hold an existing position to expiry, or block (when liquidity / IV gates
fail).

Distinct from options/wheel.py — that one is a MODELED historical
backtest (Black-Scholes with assumed IV multiplier). This one uses real
Alpaca chain quotes and is what the live engine calls each cycle.

Bounded-risk invariants enforced here:
- Never sell naked. CSPs are cash-secured, CCs are share-covered.
- Never sell more contracts than the cash/share reserve covers.
- Never open inside the last 48h before expiry (gamma risk spike).
- Liquidity gate: bid > 0 AND (ask - bid) / mid <= max_spread_pct.
- Premium floor: min premium per contract in dollars per share.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal

from ..data.alpaca_options import AlpacaOptionsFeed, OptionContract, OptionQuote
from ..data.finnhub_feed import EarningsEvent, FinnhubFeed

logger = logging.getLogger(__name__)

WheelMode = Literal["standard", "earnings_event"]

Action = Literal[
    "open_csp",
    "open_cc",
    "hold",
    "block_no_liquidity",
    "block_too_close_to_expiry",
    "block_iv_too_low",
    "block_insufficient_cash",
    "block_insufficient_shares",
    "block_no_chain",
    "block_outside_earnings_window",
    "block_no_earnings_event",
]

CONTRACT_LOT = 100  # one option contract = 100 underlying shares


@dataclass(frozen=True)
class WheelLiveConfig:
    target_dte: int = 30
    put_otm_pct: float = 0.05
    call_otm_pct: float = 0.05
    min_premium_per_share: float = 0.50
    max_spread_pct: float = 0.25
    min_iv_rank: float | None = None        # None disables the gate
    min_dte_to_open: int = 2
    pre_earnings_days_min: int = 3          # earnings mode: open at least N days before
    pre_earnings_days_max: int = 14         # earnings mode: open at most N days before


@dataclass(frozen=True)
class WheelState:
    underlying: str
    spot: float
    shares: int
    open_option: OptionContract | None
    available_cash: float


@dataclass(frozen=True)
class WheelDecision:
    action: Action
    contract: OptionContract | None
    quote: OptionQuote | None
    premium_per_contract: float       # $/share * 100 (i.e. dollars credited per contract sold)
    contracts: int
    rationale: str

    @property
    def is_opening(self) -> bool:
        return self.action in ("open_csp", "open_cc")

    @property
    def is_block(self) -> bool:
        return self.action.startswith("block_")


class WheelLive:
    def __init__(
        self,
        feed: AlpacaOptionsFeed,
        *,
        config: WheelLiveConfig | None = None,
        finnhub: FinnhubFeed | None = None,
    ) -> None:
        self.feed = feed
        self.config = config or WheelLiveConfig()
        self.finnhub = finnhub

    def decide(
        self,
        state: WheelState,
        *,
        mode: WheelMode = "standard",
        as_of: date | None = None,
    ) -> WheelDecision:
        cfg = self.config
        today = as_of or datetime.now(timezone.utc).date()

        # 1. If we already have an open option, just hold.
        if state.open_option is not None:
            return _hold(
                state.open_option,
                "Position already open; awaiting expiry/assignment.",
            )

        # 2. Earnings-mode gate.
        if mode == "earnings_event":
            event = self._next_earnings(state.underlying, today)
            if event is None:
                return _block(
                    "block_no_earnings_event",
                    f"No upcoming earnings in lookback for {state.underlying}",
                )
            days_to = (event.date - today).days
            if days_to < cfg.pre_earnings_days_min or days_to > cfg.pre_earnings_days_max:
                return _block(
                    "block_outside_earnings_window",
                    f"Earnings in {days_to}d, outside open window "
                    f"[{cfg.pre_earnings_days_min}, {cfg.pre_earnings_days_max}].",
                )

        # 3. IV rank gate (optional).
        if cfg.min_iv_rank is not None:
            ivr = self.feed.iv_rank(state.underlying)
            if ivr is None:
                logger.info(
                    "iv_rank(%s) unavailable yet — IV-rank gate skipped (history bootstrap).",
                    state.underlying,
                )
            elif ivr < cfg.min_iv_rank:
                return _block(
                    "block_iv_too_low",
                    f"IV rank {ivr:.0f} < floor {cfg.min_iv_rank:.0f}",
                )

        # 4. Pick a leg: CC if we already hold a lot, otherwise CSP.
        if state.shares >= CONTRACT_LOT:
            return self._open_covered_call(state, today)
        return self._open_cash_secured_put(state, today)

    # ------------------------------------------------------------------

    def _open_cash_secured_put(self, state: WheelState, today: date) -> WheelDecision:
        cfg = self.config
        target_strike = state.spot * (1.0 - cfg.put_otm_pct)
        chain_q = self._pick_strike(state.underlying, state.spot, target_strike, "put", today)
        if chain_q is None or chain_q.contract is None:
            return _block(
                "block_no_chain",
                f"No put chain available for {state.underlying} near ${target_strike:.2f}.",
            )

        liquidity = _check_liquidity(chain_q, cfg)
        if liquidity is not None:
            return liquidity

        dte = (chain_q.contract.expiry - today).days
        if dte < cfg.min_dte_to_open:
            return _block(
                "block_too_close_to_expiry",
                f"DTE {dte} < min_dte_to_open {cfg.min_dte_to_open}.",
            )

        # Cash requirement per contract = strike * 100 (covers full assignment)
        cash_per_contract = chain_q.contract.strike * CONTRACT_LOT
        contracts = int(state.available_cash // cash_per_contract)
        if contracts < 1:
            return _block(
                "block_insufficient_cash",
                f"Cash ${state.available_cash:,.0f} < ${cash_per_contract:,.0f} "
                f"required for 1 cash-secured contract at strike ${chain_q.contract.strike:.2f}.",
            )

        premium_per_contract = chain_q.mid * CONTRACT_LOT
        return WheelDecision(
            action="open_csp",
            contract=chain_q.contract,
            quote=chain_q,
            premium_per_contract=premium_per_contract,
            contracts=contracts,
            rationale=(
                f"Sell-to-open {contracts}x {chain_q.contract.underlying} "
                f"{chain_q.contract.expiry} put @ strike ${chain_q.contract.strike:.2f} "
                f"(spot ${state.spot:.2f}, {chain_q.contract.strike / state.spot - 1.0:+.1%} OTM, "
                f"DTE {dte}, mid ${chain_q.mid:.2f}, IV {(chain_q.iv or 0)*100:.0f}%)"
            ),
        )

    def _open_covered_call(self, state: WheelState, today: date) -> WheelDecision:
        cfg = self.config
        target_strike = state.spot * (1.0 + cfg.call_otm_pct)
        chain_q = self._pick_strike(state.underlying, state.spot, target_strike, "call", today)
        if chain_q is None or chain_q.contract is None:
            return _block(
                "block_no_chain",
                f"No call chain available for {state.underlying} near ${target_strike:.2f}.",
            )

        liquidity = _check_liquidity(chain_q, cfg)
        if liquidity is not None:
            return liquidity

        dte = (chain_q.contract.expiry - today).days
        if dte < cfg.min_dte_to_open:
            return _block(
                "block_too_close_to_expiry",
                f"DTE {dte} < min_dte_to_open {cfg.min_dte_to_open}.",
            )

        # Share-coverage requirement: 1 contract covers 100 shares.
        contracts = state.shares // CONTRACT_LOT
        if contracts < 1:
            return _block(
                "block_insufficient_shares",
                f"Held {state.shares} shares < {CONTRACT_LOT} required per contract.",
            )

        premium_per_contract = chain_q.mid * CONTRACT_LOT
        return WheelDecision(
            action="open_cc",
            contract=chain_q.contract,
            quote=chain_q,
            premium_per_contract=premium_per_contract,
            contracts=contracts,
            rationale=(
                f"Sell-to-open {contracts}x {chain_q.contract.underlying} "
                f"{chain_q.contract.expiry} call @ strike ${chain_q.contract.strike:.2f} "
                f"(spot ${state.spot:.2f}, {chain_q.contract.strike / state.spot - 1.0:+.1%} OTM, "
                f"DTE {dte}, mid ${chain_q.mid:.2f}, IV {(chain_q.iv or 0)*100:.0f}%)"
            ),
        )

    # ------------------------------------------------------------------

    def _pick_strike(
        self,
        underlying: str,
        spot: float,
        target_strike: float,
        option_type: Literal["call", "put"],
        today: date,
    ) -> OptionQuote | None:
        try:
            expiry = self.feed.nearest_expiry(underlying, self.config.target_dte, as_of=today)
        except LookupError as e:
            logger.warning("nearest_expiry(%s) failed: %s", underlying, e)
            return None
        # Fetch a band of strikes around spot to keep the response small.
        band = max(spot * 0.20, 25.0)
        chain = self.feed.chain(
            underlying,
            expiry=expiry,
            option_type=option_type,
            min_strike=spot - band,
            max_strike=spot + band,
        )
        if not chain:
            return None
        return min(chain, key=lambda q: abs(q.contract.strike - target_strike))

    def _next_earnings(self, underlying: str, today: date) -> EarningsEvent | None:
        if self.finnhub is None:
            logger.warning(
                "earnings_event mode requested but no FinnhubFeed provided; treating as no event."
            )
            return None
        return self.finnhub.next_earnings(underlying, as_of=today)


# ----------------------------------------------------------------------
# Helpers (module-private)
# ----------------------------------------------------------------------


def _hold(contract: OptionContract, rationale: str) -> WheelDecision:
    return WheelDecision(
        action="hold",
        contract=contract,
        quote=None,
        premium_per_contract=0.0,
        contracts=0,
        rationale=rationale,
    )


def _block(action: Action, rationale: str) -> WheelDecision:
    return WheelDecision(
        action=action,
        contract=None,
        quote=None,
        premium_per_contract=0.0,
        contracts=0,
        rationale=rationale,
    )


def _check_liquidity(q: OptionQuote, cfg: WheelLiveConfig) -> WheelDecision | None:
    """Return a block decision when liquidity / premium gates fail, else None."""
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
