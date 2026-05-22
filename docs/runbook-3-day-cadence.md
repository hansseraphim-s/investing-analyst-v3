# 3-day weekly cadence runbook — AGGRESSIVE STACK (target 20%+ annualized)

Operational guide for running investing-analyst-v3 paper trading
Tuesday / Wednesday / Friday near market close, with the aggressive
configuration that targets 20%+ annualized at the cost of accepting
30%+ drawdowns.

> **Honest expectation:** this configuration may hit 20-40% in a
> bull/momentum regime (NVDA-style 2023-2024). In a regime-change
> year (2022-style rotation, 2018-style vol spike) it may produce
> -25% to -35% drawdowns. The walk-forward harness is set up so
> the strategy must re-validate before any consideration of live.

## TL;DR — what to type Tue/Wed/Fri at ~3:50 PM ET

```bash
cd /Users/hansmseraphim/Desktop/investing-analyst-v3
source agent/.venv/bin/activate
iav3 paper
open https://dashboard.fairwinds.live
```

The agent reads everything from `.env`. No flags needed.

## Locked configuration

| Setting | Value | Rationale |
|---|---|---|
| Strategy | `vol_target_trend_aggressive` | fast=10, slow=50, target_vol=0.20 — faster trend response, higher sizing per realized-vol unit. NOT walk-forward-validated on these params; paper-trade is the validation |
| Watchlist | AAPL, NVDA, GOOGL, MSFT, QQQ | 5-name tech-bias for max concentration in highest-momentum names. Cuts diversification to amplify favorable regimes |
| Max position % | 20% | Up from 8% — allows 1/5 of equity in a single name, the maximum still compatible with portfolio-style risk |
| Max daily loss | 6% | Up from 3-4% — accepts ~2σ vol days without halting trading |
| Min cash reserve | 5% | Down from 15% — keeps capital deployed; less dry powder for opportunistic adds |
| Options overlay | ENABLED | Bootstraps IV history from day 1 |
| Overlay IV-rank ceiling | 50 | Up from 30 — fires more often, at less ideal IV levels |
| Overlay sizing | 12% of equity | Up from 5% — each overlay call is a ~$12k debit on a $103k account |
| Overlay target DTE | 60 | Down from 75 — more theta acceleration, more responsive to underlying moves |
| Overlay target delta | 0.55 | Up from 0.50 — slightly ITM calls; more delta exposure per contract |

All settings live in `/Users/hansmseraphim/Desktop/investing-analyst-v3/.env`.

## Schedule

| Day | Time (ET) | Action |
|---|---|---|
| Tuesday | 3:50 PM | `iav3 paper` |
| Wednesday | 3:50 PM | `iav3 paper` |
| Friday | 3:50 PM | `iav3 paper` |

Monday / Thursday / weekend: nothing. Alpaca bracket orders run
autonomously on the stops/targets between your cycles.

## What this stack does that the disciplined plan doesn't

1. **Concentrates** into 5 highest-momentum names — bigger gains in
   trending regimes; bigger losses when leaders rotate
2. **Buys faster** — fast EMA 10 vs 20 catches momentum changes ~1
   week sooner but also chops more in sideways markets
3. **Sizes bigger** — 20% per position (4× the conservative cap)
   means a 50% loss on one name = 10% portfolio loss
4. **Bleeds less cash** — 5% reserve vs 15% means more capital
   working; also less ability to add on dips
5. **Stretches the overlay** — IV rank up to 50 means calls aren't
   always genuinely cheap; sizing at 12% means each is meaningful

## What I'd be watching for after week 4

After ~12 sessions (4 weeks × 3 days), pull these numbers from the
dashboard `/health` and `/journal`:

| Metric | Within range | Concerning |
|---|---|---|
| Paper P&L | Within ±25% of what the strategy could plausibly produce given the regime | Persistently negative across all 5 symbols even in trending market |
| Trade count | 5-20 entries over 12 sessions | 0 (broken signal pipeline) or > 50 (overtrading) |
| Bracket hit rate | 20-40% of entries stopped, 30-50% targeted, rest signal-exited | All stops, no targets (vol-target may be poorly calibrated to regime) |
| Overlay decisions | All "block_iv_history_insufficient" until ~session 60 per symbol | Overlay opens early due to wrong gate config |
| Max paper drawdown | < 20% peak-to-trough | > 30% — pause and reassess |

## Bail conditions

Concrete numbers that mean STOP and reassess:

| Trigger | Action |
|---|---|
| Cumulative paper return < -25% by week 8 | Stop. Either the strategy doesn't suit current regime OR there's a bug. Run walk-forward on real paper data, not historical |
| 3+ consecutive overlay positions lose 80%+ of premium | Disable overlay (`ENABLE_OPTIONS_OVERLAY=false`). IV-rank gate is letting through bad timing |
| Single-day equity drop > 15% intraday | Run `iav3 paper` immediately. Manually verify positions on Alpaca dashboard. Investigate before next cycle |
| Aggregate Sharpe over the live paper period < 0 by week 12 | Stop. Strategy doesn't work in this regime. Revert to conservative settings or sit on cash |

## Promotion to live (not before)

The aggressive walk-forward gate is loosened:
- `oos_sharpe > 0.5` (was 0.7)
- `max_oos_dd < 35%` (was 25%)
- `worst_window_sharpe > -1.0` (was -0.5)

To promote: pass these gates on a walk-forward run that includes the
paper-trade period AND historical data. Plus ≥ 30 paper sessions
where realized P&L tracks walk-forward expectation within 20%.

When BOTH conditions hold:
1. Set `TRADING_MODE=LIVE` in `.env`
2. Restart agent
3. Start with a fraction of intended capital (e.g. $10k of a $100k
   account) — observe one week before scaling

## Falling back to the disciplined plan

If after 4-8 weeks the aggressive stack is not behaving, revert by
editing `.env`:

```
STRATEGY=vol_target_trend
WATCHLIST=AAPL,MSFT,NVDA,GOOGL,META,AMZN,SPY,QQQ
RISK_MAX_POSITION_PCT=0.08
RISK_MAX_DAILY_LOSS_PCT=0.04
RISK_MIN_CASH_RESERVE_PCT=0.15
OVERLAY_IV_RANK_MAX=30
OVERLAY_PCT_OF_EQUITY=0.05
OVERLAY_TARGET_DTE=75
OVERLAY_TARGET_DELTA=0.50
```

These are the disciplined defaults — expected 5-12% annualized at
sub-15% DD. Safer but lower ceiling.

## Honest framing

The expected return distribution for this aggressive stack is wide.
The 20% target is the upper-middle of what's plausibly achievable on
a 5-name mega-cap-tech portfolio with options overlay in a trending
market. Hitting 20% means everything goes right: trending regime,
overlay arms in time for cheap-IV windows, no major drawdown event.

The downside is symmetric. A regime change (rate shock, recession,
sector rotation) easily produces -25% to -35% on this configuration.
There is no "20% annualized with controlled risk" for retail; that's
top-decile hedge fund performance and they have edges you don't.

What this paper experiment is genuinely measuring: how big the
drawdown gets, how fast it recovers, and whether the strategy's
behavior matches expectation closely enough to consider scaling.
Treat the paper period as the most important data you'll generate.
