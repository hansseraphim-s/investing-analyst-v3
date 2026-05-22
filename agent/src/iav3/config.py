"""Typed, validated configuration.

All settings come from environment variables (optionally via a .env file).
Risk parameters live in a frozen dataclass so they cannot be mutated at
runtime by accident.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _split(csv: str) -> list[str]:
    return [s.strip().upper() for s in csv.split(",") if s.strip()]


@dataclass(frozen=True)
class RiskConfig:
    """Hard guardrails. Every order is checked against these (see risk.manager)."""

    max_position_pct: float = 0.08          # max fraction of equity in one symbol
    max_daily_loss_pct: float = 0.03        # halt new entries if day P&L <= -3%
    max_trades_per_day: int = 20            # runaway-loop guard
    max_order_value: float = 5_000.0        # hard $ cap per order
    min_cash_reserve_pct: float = 0.15      # always keep >=15% cash
    atr_stop_mult: float = 2.0              # stop = entry - 2*ATR (long)
    atr_target_mult: float = 4.0            # target = entry + 4*ATR (long) -> 2:1 R:R
    prohibited_symbols: tuple[str, ...] = (
        "UVXY", "SQQQ", "SPXS", "TVIX", "VIXY", "FAZ", "TQQQ", "SOXL", "SOXS",
    )
    # ---- Portfolio-level Greeks caps (consumed when options orders are involved) -------
    # max_portfolio_delta is in SHARE-EQUIVALENTS, NOT dollars (e.g. 1500 = up to 15
    # standard-lot equivalents of net long/short exposure across the book).
    max_portfolio_delta: float = 1500.0
    max_portfolio_vega: float = 500.0       # $/1% IV move (absolute value cap)
    min_portfolio_theta: float = -100.0     # most-negative theta/day allowed
    max_portfolio_notional_pct: float = 1.5 # 150% of equity (room for defined-risk leverage)

    def __post_init__(self) -> None:
        for name in ("max_position_pct", "max_daily_loss_pct", "min_cash_reserve_pct"):
            v = getattr(self, name)
            if not 0.0 < v < 1.0:
                raise ValueError(f"RiskConfig.{name} must be in (0, 1); got {v}")
        if self.atr_target_mult <= self.atr_stop_mult:
            raise ValueError("atr_target_mult must exceed atr_stop_mult (need R:R > 1)")

    def greeks_limits(self):
        """Lazy import to avoid a hard dependency in config.py."""
        from .greeks.aggregator import GreeksLimits
        return GreeksLimits(
            max_delta=self.max_portfolio_delta,
            max_vega=self.max_portfolio_vega,
            min_theta=self.min_portfolio_theta,
            max_notional_exposure_pct=self.max_portfolio_notional_pct,
        )


@dataclass(frozen=True)
class Settings:
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    anthropic_api_key: str = ""
    trading_mode: str = "PAPER"             # PAPER or LIVE
    watchlist: list[str] = field(default_factory=lambda: ["AAPL", "MSFT", "SPY"])
    strategy: str = "momentum"
    cycle_interval_minutes: int = 30
    enable_advisor: bool = False
    enable_options_overlay: bool = False     # consult LongCallOverlay per symbol when trend ON
    risk: RiskConfig = field(default_factory=RiskConfig)

    @property
    def is_paper(self) -> bool:
        return self.trading_mode.upper() != "LIVE"

    @property
    def alpaca_base_url(self) -> str:
        return (
            "https://paper-api.alpaca.markets"
            if self.is_paper
            else "https://api.alpaca.markets"
        )


def load_settings() -> Settings:
    mode = os.getenv("TRADING_MODE", "PAPER").upper()
    if mode not in {"PAPER", "LIVE"}:
        raise ValueError(f"TRADING_MODE must be PAPER or LIVE; got {mode!r}")
    # Accept both ALPACA_SECRET_KEY (v2 convention) and ALPACA_API_SECRET
    # (Alpaca's own docs use the latter). My .env.example uses _API_SECRET
    # but config used to only read _SECRET_KEY, so paper mode silently
    # routed to the in-process simulator instead of Alpaca paper.
    secret = os.getenv("ALPACA_SECRET_KEY") or os.getenv("ALPACA_API_SECRET", "")
    risk = RiskConfig(
        max_position_pct=float(os.getenv("RISK_MAX_POSITION_PCT", "0.08")),
        max_daily_loss_pct=float(os.getenv("RISK_MAX_DAILY_LOSS_PCT", "0.03")),
        max_trades_per_day=int(os.getenv("RISK_MAX_TRADES_PER_DAY", "20")),
        max_order_value=float(os.getenv("RISK_MAX_ORDER_VALUE", "5000")),
        min_cash_reserve_pct=float(os.getenv("RISK_MIN_CASH_RESERVE_PCT", "0.15")),
        max_portfolio_delta=float(os.getenv("RISK_MAX_PORTFOLIO_DELTA", "1500")),
        max_portfolio_vega=float(os.getenv("RISK_MAX_PORTFOLIO_VEGA", "500")),
        min_portfolio_theta=float(os.getenv("RISK_MIN_PORTFOLIO_THETA", "-100")),
        max_portfolio_notional_pct=float(os.getenv("RISK_MAX_PORTFOLIO_NOTIONAL_PCT", "1.5")),
    )
    return Settings(
        alpaca_api_key=os.getenv("ALPACA_API_KEY", ""),
        alpaca_secret_key=secret,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        trading_mode=mode,
        watchlist=_split(os.getenv("WATCHLIST", "AAPL,MSFT,SPY")),
        strategy=os.getenv("STRATEGY", "momentum").strip().lower(),
        cycle_interval_minutes=int(os.getenv("CYCLE_INTERVAL_MINUTES", "30")),
        enable_advisor=os.getenv("ENABLE_ADVISOR", "false").strip().lower() == "true",
        enable_options_overlay=os.getenv("ENABLE_OPTIONS_OVERLAY", "false").strip().lower() == "true",
        risk=risk,
    )
