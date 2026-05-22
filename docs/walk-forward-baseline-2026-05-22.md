# Walk-forward baseline — vol_target_trend (2026-05-22)

First documented walk-forward run after Phase 2 lands. This becomes the
baseline future strategies (or future parameter tunings of this one) must
beat to be considered for live promotion.

## Command

```
iav3 walk-forward --symbols AAPL,MSFT,NVDA,SPY \
  --strategy vol_target_trend \
  --start 2015-01-01
```

## Setup

- IS window: 36 months, OOS window: 6 months, step: 6 months
- Purge: 0 days (vol_target_trend uses only price-derived features; no
  cross-bar label leakage)
- Param grid: empty (defaults from `vol_target_trend.py`:
  fast=20, slow=100, regime=200, vol_window=20, target_vol=0.15)
- Promotion gate: `oos_sharpe > 0.7`, `max_oos_dd < 25%`,
  `worst_window_sharpe > -0.5` (all three must pass)
- Backtester: event-driven, conservative fills, no leverage
- Data: yfinance daily bars (survivorship-biased, documented in README)

## Result

**PROMOTION BLOCKED** — fails on 2 of 3 gates.

| Metric | Value | Gate | Status |
|---|---|---|---|
| Aggregate OOS Sharpe | 0.54 | > 0.70 | FAIL |
| Max OOS drawdown | 24.4% | < 25% | PASS (marginal) |
| Worst window OOS Sharpe | -3.87 | > -0.5 | FAIL |

## Per-window highlights

Best OOS windows (Sharpe > 2.0):
- NVDA 2020-01→2023-01: Sharpe +3.12, return +33.7%, max DD -2.6%
- NVDA 2021-01→2024-01: Sharpe +4.53, return +40.9%, max DD -4.2%
- MSFT 2018-01→2021-01: Sharpe +2.21, return +17.0%, max DD -5.6%
- MSFT 2020-01→2023-01: Sharpe +2.21, return +14.7%, max DD -4.4%
- SPY 2018-01→2021-01: Sharpe +2.21, return +14.2%, max DD -4.7%

Worst OOS windows (Sharpe < -1.0):
- MSFT 2021-07→2024-07: Sharpe -3.87, return -22.6%, max DD -24.4%
- NVDA 2019-01→2022-01: Sharpe -3.30, return -8.2%, max DD -8.2%
- SPY 2019-01→2022-01: Sharpe -3.29, return -10.0%, max DD -10.0%
- MSFT 2019-01→2022-01: Sharpe -1.97, return -4.6%, max DD -4.6%

The 2019-01 IS / 2022-01 OOS windows all cluster as failures because
COVID + 2022 rate-hike regime broke the trend-following pattern that
was learned 2016-2018. This is the kind of fragility walk-forward is
designed to surface.

## What this means

`vol_target_trend` is NOT promotion-eligible at defaults. Options to
improve future runs:

1. Add a regime filter that detects the structural break and reduces
   exposure (e.g. VIX-rank gate, or 200-SMA crossover state machine)
2. Sweep parameters via `--param-grid` to find a window-robust
   configuration (risk: overfitting; mitigate with longer step_months)
3. Add a strict per-window stop (no trade in a window with VIX > X
   without confirmation from a second signal)
4. Combine with the IV-rank long-call overlay during regime change
   periods (the overlay only fires when IV is cheap, naturally
   reducing exposure during fearful regimes)

The walk-forward harness is now part of every promotion decision.
A strategy doesn't go from paper to live without passing this gate.

## Caveats

- yfinance survivorship bias: delisted names excluded from results
- Single-symbol backtests (no portfolio cash competition); aggregate is
  cross-symbol average, not a true portfolio run
- Slippage modeled as flat 5 bps; real fills will be worse
- No transaction costs beyond commission ($1/trade, conservative)
- Strategy uses daily bars; intraday fills assumed at next-day open
  (no look-ahead but also no information-rich intra-bar logic)
