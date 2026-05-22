from .base import Account, Broker, BrokerPosition, OrderResult
from .paper import PaperBroker

__all__ = ["Account", "Broker", "BrokerPosition", "OrderResult", "PaperBroker"]


def get_broker(settings) -> Broker:
    """Construct the right broker from settings.

    PAPER + no Alpaca keys -> in-process PaperBroker (zero setup).
    PAPER/LIVE + Alpaca keys -> AlpacaBroker against the matching endpoint.
    """
    has_keys = bool(settings.alpaca_api_key and settings.alpaca_secret_key)
    if not has_keys:
        if not settings.is_paper:
            raise ValueError("LIVE mode requires Alpaca API keys.")
        return PaperBroker()
    from .alpaca import AlpacaBroker

    return AlpacaBroker(
        settings.alpaca_api_key,
        settings.alpaca_secret_key,
        paper=settings.is_paper,
    )
