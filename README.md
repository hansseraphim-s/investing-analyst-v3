# investing-analyst-v3

A risk-disciplined, backtest-first trading agent with a real-time
colleague-shareable dashboard. Paper trading by default; live trading is
explicitly gated behind walk-forward validation and a documented checklist.

> **No performance is promised.** This system enforces discipline and lets
> you measure strategies on real options data before risking capital.
> Not financial advice.

## Architecture

```
agent/          Python trading agent (Alpaca = broker + market data + options
                chains; Finnhub = earnings + news + sentiment; Anthropic =
                off-path advisor commentary, NOT in decision path)
dashboard/      Next.js 16 dashboard on Vercel; Clerk-gated for colleagues
shared/         schema.sql (source of truth for Neon Postgres tables) +
                types.ts (TS bindings)
scripts/        deploy + DB migration helpers
docs/           design notes, runbooks
```

## Quick start

```bash
# Agent
cd agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
iav3 backtest --symbols AAPL,MSFT,NVDA,SPY --strategy vol_target_trend --start 2015-01-01
iav3 paper --strategy vol_target_trend

# Dashboard
cd dashboard
pnpm install
pnpm dev   # http://localhost:3000
```

See `.env.example` for required environment configuration.

## What's new vs v2 (AI-IB-Analyst)

- **Real options data via Alpaca** — wheel and call strategies use actual
  market IV, not `realized_vol × multiplier`. The MODEL caveat goes away
  for Alpaca-priced backtests.
- **Walk-forward + purged k-fold** for every strategy before promotion.
  Hand-picked parameters are not trusted.
- **Portfolio Greeks aggregator** + delta/vega/theta limits enforced in
  the risk layer (required for any options overlay).
- **CVaR-based daily loss breaker** replacing the absolute drawdown cap.
- **Risk-parity portfolio allocator** replacing equal-weight allocation.
- **Neon Postgres journal** instead of local SQLite, powering the
  colleague-facing Next.js dashboard.
- **Clerk auth** on the dashboard — invite by email, no public URL.
- **Anthropic advisor** writes per-trade rationale into the journal
  post-execution. Still NOT in the decision path.

## Strategies (rollout order, gated by validation)

1. `vol_target_trend` — ported from v2 (validated baseline)
2. `earnings_event_wheel` (phase 1) — cash-secured puts into pre-earnings
   IV on names you'd own. Real IV from Alpaca.
3. `iv_rank_long_call_overlay` (phase 2) — when trend filter is ON and
   IV rank < 30, replace ~25% of equity delta with long-dated long calls
   or defined-risk debit call spreads.
4. `diagonal_call_spreads` (phase 3) — capital-efficient covered-call
   replacement for high-priced names.

## Go-live checklist (paper -> live)

- [ ] Walk-forward Sharpe > 0.7 net of fees on out-of-sample windows
- [ ] Walk-forward max DD < 25% across all OOS windows
- [ ] >= 30 sessions paper-traded with behavior matching backtest within 15%
- [ ] Greeks limits demonstrably enforced in `risk/manager.py`
- [ ] Kill-switch tested (deliberately tripped, verified positions unwound)
- [ ] Manually placed and cancelled a bracket order on Alpaca paper UI
- [ ] `TRADING_MODE=LIVE` set consciously, with size you can afford to lose

## License

MIT.
