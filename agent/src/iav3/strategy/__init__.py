from .base import Strategy
from .mean_reversion import MeanReversionStrategy
from .momentum import MomentumStrategy
from .vol_target_trend import VolTargetTrendStrategy

_REGISTRY: dict[str, type[Strategy]] = {
    MomentumStrategy.name: MomentumStrategy,
    MeanReversionStrategy.name: MeanReversionStrategy,
    VolTargetTrendStrategy.name: VolTargetTrendStrategy,
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
    "get_strategy",
    "available_strategies",
]
