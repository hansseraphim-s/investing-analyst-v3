from .base import Strategy
from .mean_reversion import MeanReversionStrategy
from .momentum import MomentumStrategy
from .vol_target_trend import VolTargetTrendStrategy
from .vol_target_trend_aggressive import VolTargetTrendAggressiveStrategy

_REGISTRY: dict[str, type[Strategy]] = {
    MomentumStrategy.name: MomentumStrategy,
    MeanReversionStrategy.name: MeanReversionStrategy,
    VolTargetTrendStrategy.name: VolTargetTrendStrategy,
    VolTargetTrendAggressiveStrategy.name: VolTargetTrendAggressiveStrategy,
}


def get_strategy(name: str) -> Strategy:
    key = name.strip().lower()
    if key not in _REGISTRY:
        raise ValueError(
            f"Unknown strategy {name!r}. Available: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[key]()


def available_strategies() -> list[str]:
    return sorted(_REGISTRY)


__all__ = [
    "Strategy",
    "MomentumStrategy",
    "MeanReversionStrategy",
    "VolTargetTrendStrategy",
    "VolTargetTrendAggressiveStrategy",
    "get_strategy",
    "available_strategies",
]
