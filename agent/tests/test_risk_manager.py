import pytest

from iav3.config import RiskConfig
from iav3.risk import PortfolioView, Position, pre_trade_check

RISK = RiskConfig()


def pv(**kw):
    base = dict(equity=100_000.0, cash=100_000.0, day_pnl_pct=0.0,
               positions=(), trades_today=0)
    base.update(kw)
    return PortfolioView(**base)


def test_clean_buy_passes():
    d = pre_trade_check("BUY", "AAPL", 10, 100.0, pv(), RISK)
    assert d.approved and d.check == "ALL_PASSED"


def test_invalid_qty_and_price():
    assert not pre_trade_check("BUY", "AAPL", 0, 100.0, pv(), RISK).approved
    assert not pre_trade_check("BUY", "AAPL", 10, 0.0, pv(), RISK).approved


def test_zero_equity_blocked():
    d = pre_trade_check("BUY", "AAPL", 1, 10.0, pv(equity=0.0), RISK)
    assert not d.approved and d.check == "NO_EQUITY"


def test_daily_loss_breaker_blocks_buys_not_sells():
    p = pv(day_pnl_pct=-3.5,
           positions=(Position("AAPL", 10, 1000.0),))
    buy = pre_trade_check("BUY", "AAPL", 1, 100.0, p, RISK)
    assert not buy.approved and buy.check == "DAILY_LOSS_BREAKER"
    # De-risking sells must still be allowed when the breaker is tripped.
    sell = pre_trade_check("SELL", "AAPL", 5, 100.0, p, RISK)
    assert sell.approved


def test_prohibited_symbol():
    d = pre_trade_check("BUY", "UVXY", 1, 10.0, pv(), RISK)
    assert not d.approved and d.check == "PROHIBITED_SYMBOL"


def test_order_too_large():
    d = pre_trade_check("BUY", "AAPL", 100, 100.0, pv(), RISK)  # $10k > $5k cap
    assert not d.approved and d.check == "ORDER_TOO_LARGE"


def test_concentration_limit():
    # 8% of 100k = 8k; existing 5k + new 4k = 9k -> 9% > 8%.
    p = pv(positions=(Position("AAPL", 50, 5_000.0),))
    d = pre_trade_check("BUY", "AAPL", 40, 100.0, p, RISK)
    assert not d.approved and d.check == "CONCENTRATION_LIMIT"


def test_cash_reserve_enforced():
    # Spend almost all cash -> would drop below 15% reserve.
    p = pv(cash=100_000.0, equity=100_000.0)
    d = pre_trade_check("BUY", "AAPL", 49, 100.0, p, RISK)  # $4900 ok size-wise
    assert d.approved  # 4900 leaves 95.1% cash
    big = pre_trade_check("BUY", "AAPL", 48, 100.0,
                          pv(cash=4_000.0, equity=100_000.0), RISK)
    assert not big.approved and big.check in {"CASH_RESERVE", "INSUFFICIENT_CASH"}


def test_insufficient_cash():
    d = pre_trade_check("BUY", "AAPL", 30, 100.0,
                        pv(cash=1_000.0, equity=100_000.0), RISK)
    assert not d.approved and d.check == "INSUFFICIENT_CASH"


def test_sell_requires_position_and_size():
    no_pos = pre_trade_check("SELL", "AAPL", 1, 100.0, pv(), RISK)
    assert not no_pos.approved and no_pos.check == "NO_POSITION"
    oversell = pre_trade_check(
        "SELL", "AAPL", 99, 100.0,
        pv(positions=(Position("AAPL", 10, 1000.0),)), RISK,
    )
    assert not oversell.approved and oversell.check == "OVERSELL"


def test_frequency_limit():
    d = pre_trade_check("BUY", "AAPL", 1, 100.0,
                        pv(trades_today=RISK.max_trades_per_day), RISK)
    assert not d.approved and d.check == "FREQUENCY_LIMIT"


def test_riskconfig_validation():
    with pytest.raises(ValueError):
        RiskConfig(max_position_pct=1.5)
    with pytest.raises(ValueError):
        RiskConfig(atr_stop_mult=5.0, atr_target_mult=2.0)
