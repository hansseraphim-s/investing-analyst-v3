-- ============================================================================
-- investing-analyst-v3 Neon Postgres schema
-- Source of truth for the journal store that powers the dashboard.
-- Agent writes; dashboard reads. Apply with: make db-migrate
-- ============================================================================

-- Sessions: one row per agent cycle.
CREATE TABLE IF NOT EXISTS sessions (
    id              BIGSERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ,
    trading_mode    TEXT NOT NULL CHECK (trading_mode IN ('PAPER','LIVE')),
    strategy        TEXT NOT NULL,
    equity_start    NUMERIC(18, 4) NOT NULL,
    equity_end      NUMERIC(18, 4),
    cash_end        NUMERIC(18, 4),
    day_pnl_pct     NUMERIC(10, 4),
    summary         TEXT,
    advisor_review  TEXT,
    agent_version   TEXT NOT NULL,
    git_sha         TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions (started_at DESC);

-- Orders: every order the agent submits or blocks.
CREATE TABLE IF NOT EXISTS orders (
    id              BIGSERIAL PRIMARY KEY,
    session_id      BIGINT REFERENCES sessions(id) ON DELETE CASCADE,
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol          TEXT NOT NULL,
    asset_class     TEXT NOT NULL CHECK (asset_class IN ('equity','option')),
    option_type     TEXT CHECK (option_type IN ('call','put')),
    strike          NUMERIC(12, 4),
    expiry          DATE,
    side            TEXT NOT NULL CHECK (side IN ('BUY','SELL','SELL_TO_OPEN','BUY_TO_CLOSE')),
    qty             INTEGER NOT NULL CHECK (qty > 0),
    price           NUMERIC(12, 4) NOT NULL,
    stop_price      NUMERIC(12, 4),
    take_profit     NUMERIC(12, 4),
    status          TEXT NOT NULL CHECK (status IN ('filled','blocked','submitted','cancelled','rejected')),
    reason          TEXT NOT NULL,
    broker_order_id TEXT,
    advisor_rationale TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_submitted_at ON orders (submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_symbol       ON orders (symbol);
CREATE INDEX IF NOT EXISTS idx_orders_session_id   ON orders (session_id);

-- Positions: snapshot per session.
CREATE TABLE IF NOT EXISTS positions (
    id              BIGSERIAL PRIMARY KEY,
    session_id      BIGINT REFERENCES sessions(id) ON DELETE CASCADE,
    snapshot_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol          TEXT NOT NULL,
    asset_class     TEXT NOT NULL CHECK (asset_class IN ('equity','option')),
    qty             NUMERIC(12, 4) NOT NULL,
    avg_entry       NUMERIC(12, 4),
    market_value    NUMERIC(18, 4) NOT NULL,
    unrealized_pl   NUMERIC(18, 4),
    option_type     TEXT,
    strike          NUMERIC(12, 4),
    expiry          DATE
);
CREATE INDEX IF NOT EXISTS idx_positions_snapshot_at ON positions (snapshot_at DESC);

-- Equity curve: one point per cycle.
CREATE TABLE IF NOT EXISTS equity_curve (
    id              BIGSERIAL PRIMARY KEY,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id      BIGINT REFERENCES sessions(id) ON DELETE CASCADE,
    equity          NUMERIC(18, 4) NOT NULL,
    cash            NUMERIC(18, 4) NOT NULL,
    benchmark_value NUMERIC(18, 4),
    drawdown_pct    NUMERIC(10, 4)
);
CREATE INDEX IF NOT EXISTS idx_equity_curve_recorded_at ON equity_curve (recorded_at DESC);

-- Signals: per-symbol decisions per cycle.
CREATE TABLE IF NOT EXISTS signals (
    id              BIGSERIAL PRIMARY KEY,
    session_id      BIGINT REFERENCES sessions(id) ON DELETE CASCADE,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol          TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    target          SMALLINT NOT NULL CHECK (target IN (0, 1)),
    target_weight   NUMERIC(6, 4),
    price           NUMERIC(12, 4) NOT NULL,
    atr             NUMERIC(12, 6),
    realized_vol    NUMERIC(8, 6),
    iv_rank         NUMERIC(5, 2),
    extra_features  JSONB
);
CREATE INDEX IF NOT EXISTS idx_signals_generated_at ON signals (generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_symbol       ON signals (symbol);

-- Portfolio Greeks snapshot.
CREATE TABLE IF NOT EXISTS greeks_snapshot (
    id              BIGSERIAL PRIMARY KEY,
    snapshot_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id      BIGINT REFERENCES sessions(id) ON DELETE CASCADE,
    portfolio_delta NUMERIC(12, 4) NOT NULL,
    portfolio_gamma NUMERIC(12, 6) NOT NULL,
    portfolio_vega  NUMERIC(12, 4) NOT NULL,
    portfolio_theta NUMERIC(12, 4) NOT NULL,
    portfolio_rho   NUMERIC(12, 6),
    notional_exposure NUMERIC(18, 4) NOT NULL,
    cash_pct        NUMERIC(5, 2) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_greeks_snapshot_at ON greeks_snapshot (snapshot_at DESC);

-- Walk-forward results: one row per OOS window per strategy run.
CREATE TABLE IF NOT EXISTS walk_forward_runs (
    id              BIGSERIAL PRIMARY KEY,
    run_id          UUID NOT NULL,
    completed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    strategy        TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    is_start        DATE NOT NULL,
    is_end          DATE NOT NULL,
    oos_start       DATE NOT NULL,
    oos_end         DATE NOT NULL,
    params          JSONB NOT NULL,
    is_sharpe       NUMERIC(6, 3),
    oos_sharpe      NUMERIC(6, 3),
    oos_return_pct  NUMERIC(8, 3),
    oos_max_dd_pct  NUMERIC(8, 3),
    promoted        BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_walk_forward_run_id ON walk_forward_runs (run_id);

-- Kill-switch events.
CREATE TABLE IF NOT EXISTS kill_switch_events (
    id              BIGSERIAL PRIMARY KEY,
    triggered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id      BIGINT REFERENCES sessions(id) ON DELETE SET NULL,
    trigger         TEXT NOT NULL,
    detail          TEXT NOT NULL,
    positions_at_trigger JSONB,
    resolved_at     TIMESTAMPTZ,
    resolved_by     TEXT
);
