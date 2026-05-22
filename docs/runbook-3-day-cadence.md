# Operations runbook — AUTOMATED 5-day cadence, aggressive stack

The agent now runs **automatically Mon-Fri at 12:50 PM PT (3:50 PM ET)**
via macOS launchd. You don't need to remember to run anything. Logs
land in `~/Library/Logs/iav3/`, dashboard updates in real time.

> **Honest expectation:** the aggressive stack targets 20%+ annualized.
> Best-case outcomes (bull/momentum regime) plausibly 25-40%. Regime-
> change years (rotation, vol spikes) plausibly -25% to -35%. The
> walk-forward harness must re-validate before any consideration of
> live trading; current config is paper-only.

## Project location

```
~/iav3
```

The project was moved from `~/Desktop/investing-analyst-v3` to `~/iav3`
because macOS Sequoia/Sonoma sandboxes `~/Desktop` against launchd
background jobs. The new path is sandbox-free.

## TL;DR — what you do day-to-day

**Nothing.** The launchd job fires automatically Mon-Fri. You just:

- Glance at `https://dashboard.fairwinds.live` whenever you want
- (Optional) `tail ~/Library/Logs/iav3/paper-$(date +%Y-%m-%d).log`
  to see what the most recent cycle did

## If you want to trigger a cycle manually (or run from terminal)

```bash
cd ~/iav3 && source agent/.venv/bin/activate && iav3 paper
```

The launchd job runs the same wrapper, so manual cycles produce
identical behavior — just bypass the schedule.

## Locked configuration

| Setting | Value | Rationale |
|---|---|---|
| Strategy | `vol_target_trend_aggressive` | fast=10, slow=50, target_vol=0.20 |
| Watchlist | AAPL, NVDA, GOOGL, MSFT, QQQ | 5-name tech-bias for concentration |
| Max position % | 20% | 1/5 of equity per name |
| Max daily loss | 6% | Tolerates ~2σ days |
| Min cash reserve | 5% | Most capital deployed |
| Options overlay | ENABLED | Bootstraps IV history from day 1 |
| Overlay IV-rank ceiling | 50 | Fires at less ideal IV |
| Overlay sizing | 12% of equity | ~$12k debit per overlay |
| Overlay target DTE | 60 | More theta acceleration |
| Overlay target delta | 0.55 | Slightly ITM calls |

All settings live in `~/iav3/.env`. Edit and the next cycle picks
up the changes — no restart required.

## Schedule

| Day | Time (PT) | Time (ET) | What happens |
|---|---|---|---|
| Monday | 12:50 PM | 3:50 PM | Automated cycle |
| Tuesday | 12:50 PM | 3:50 PM | Automated cycle |
| Wednesday | 12:50 PM | 3:50 PM | Automated cycle |
| Thursday | 12:50 PM | 3:50 PM | Automated cycle |
| Friday | 12:50 PM | 3:50 PM | Automated cycle |

Weekends + holidays: no cycle (Alpaca's `is_market_open()` returns
False; the agent logs and exits without acting). Bracket orders
already on the broker fill autonomously if their levels are hit
during market hours.

## Logs

```bash
# Today's cycle log (full agent stdout/stderr)
tail -f ~/Library/Logs/iav3/paper-$(date +%Y-%m-%d).log

# launchd's own logs (only useful for diagnosing scheduler issues)
tail ~/Library/Logs/iav3/launchd.out.log
tail ~/Library/Logs/iav3/launchd.err.log

# All historic cycle logs
ls -lh ~/Library/Logs/iav3/paper-*.log
```

## Managing the schedule

```bash
# Status — is the job loaded?
launchctl list | grep com.fairwinds.iav3-paper

# Trigger one cycle right now (useful for testing)
launchctl start com.fairwinds.iav3-paper

# Stop the automated schedule (manual-only mode)
bash ~/iav3/scripts/uninstall-launchd.sh

# Re-install / refresh after editing the plist
bash ~/iav3/scripts/install-launchd.sh
```

## Bail conditions — when to pause

Concrete numbers that should make you stop the automation and investigate:

| Trigger | Action |
|---|---|
| Cumulative paper return < -25% by week 8 | `uninstall-launchd.sh`; review per-trade behavior |
| 3+ consecutive overlay positions lose 80%+ of premium | Set `ENABLE_OPTIONS_OVERLAY=false` in `.env` |
| Single-day equity drop > 15% intraday | `launchctl start com.fairwinds.iav3-paper` to force a cycle; manually inspect open positions |
| Aggregate paper Sharpe over 12 weeks < 0 | Stop, revert to conservative config (instructions below) |

## Reverting to disciplined defaults

If after 4-8 weeks the aggressive stack isn't behaving, edit `~/iav3/.env`:

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

Next scheduled cycle picks up the new config. Expected return drops
to 5-12% annualized but max DD also drops to sub-15%.

## Promotion to live (not before)

Aggressive walk-forward gate (loosened from defaults):
- `oos_sharpe > 0.5`
- `max_oos_dd < 35%`
- `worst_window_sharpe > -1.0`

To promote: pass these gates on a walk-forward run that includes the
paper-trade period AND historical data, plus ≥ 30 paper sessions
where realized P&L tracks walk-forward expectation within 20%.

When BOTH conditions hold:
1. Set `TRADING_MODE=LIVE` in `~/iav3/.env`
2. Restart by triggering one cycle: `launchctl start com.fairwinds.iav3-paper`
3. Start with a fraction of intended capital — observe one week before scaling
