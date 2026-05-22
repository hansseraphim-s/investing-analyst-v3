# dashboard

Next.js 16 + Clerk + Neon. Reads the journal tables written by the
Python agent in `../agent/`.

## Setup

```bash
pnpm install
cp .env.example .env.local
# fill in NEON_DATABASE_URL + Clerk keys
pnpm dev   # http://localhost:3000
```

## Pages

- `/live` — equity, cash, day P&L, open positions
- `/strategies` — per-strategy attribution + walk-forward OOS (phase 1)
- `/risk` — portfolio Greeks, VaR/CVaR, concentration (phase 2)
- `/journal` — every order, with status + reason + advisor rationale
- `/backtest` — interactive backtester (phase 1)
- `/health` — DB connectivity, table counts, latest session

## Deploy

Push to `main`, connect repo on Vercel, set project root to `dashboard/`,
add env vars in the Vercel project settings, point your custom domain at
the deployment via CNAME.
