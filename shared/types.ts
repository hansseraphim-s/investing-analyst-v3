// TypeScript bindings for the Neon Postgres tables defined in schema.sql.
// Source of truth lives in shared/schema.sql; keep these in sync.

export type TradingMode = "PAPER" | "LIVE";
export type AssetClass = "equity" | "option";
export type OptionType = "call" | "put";
export type OrderSide = "BUY" | "SELL" | "SELL_TO_OPEN" | "BUY_TO_CLOSE";
export type OrderStatus =
  | "filled"
  | "blocked"
  | "submitted"
  | "cancelled"
  | "rejected";

export interface Session {
  id: number;
  started_at: string;
  ended_at: string | null;
  trading_mode: TradingMode;
  strategy: string;
  equity_start: string;
  equity_end: string | null;
  cash_end: string | null;
  day_pnl_pct: string | null;
  summary: string | null;
  advisor_review: string | null;
  agent_version: string;
  git_sha: string | null;
}

export interface Order {
  id: number;
  session_id: number | null;
  submitted_at: string;
  symbol: string;
  asset_class: AssetClass;
  option_type: OptionType | null;
  strike: string | null;
  expiry: string | null;
  side: OrderSide;
  qty: number;
  price: string;
  stop_price: string | null;
  take_profit: string | null;
  status: OrderStatus;
  reason: string;
  broker_order_id: string | null;
  advisor_rationale: string | null;
}

export interface Position {
  id: number;
  session_id: number | null;
  snapshot_at: string;
  symbol: string;
  asset_class: AssetClass;
  qty: string;
  avg_entry: string | null;
  market_value: string;
  unrealized_pl: string | null;
  option_type: OptionType | null;
  strike: string | null;
  expiry: string | null;
}

export interface EquityPoint {
  id: number;
  recorded_at: string;
  session_id: number | null;
  equity: string;
  cash: string;
  benchmark_value: string | null;
  drawdown_pct: string | null;
}

export interface Signal {
  id: number;
  session_id: number | null;
  generated_at: string;
  symbol: string;
  strategy: string;
  target: 0 | 1;
  target_weight: string | null;
  price: string;
  atr: string | null;
  realized_vol: string | null;
  iv_rank: string | null;
  extra_features: Record<string, unknown> | null;
}

export interface GreeksSnapshot {
  id: number;
  snapshot_at: string;
  session_id: number | null;
  portfolio_delta: string;
  portfolio_gamma: string;
  portfolio_vega: string;
  portfolio_theta: string;
  portfolio_rho: string | null;
  notional_exposure: string;
  cash_pct: string;
}

export interface WalkForwardRun {
  id: number;
  run_id: string;
  completed_at: string;
  strategy: string;
  symbol: string;
  is_start: string;
  is_end: string;
  oos_start: string;
  oos_end: string;
  params: Record<string, unknown>;
  is_sharpe: string | null;
  oos_sharpe: string | null;
  oos_return_pct: string | null;
  oos_max_dd_pct: string | null;
  promoted: boolean;
}

export interface KillSwitchEvent {
  id: number;
  triggered_at: string;
  session_id: number | null;
  trigger: string;
  detail: string;
  positions_at_trigger: Record<string, unknown> | null;
  resolved_at: string | null;
  resolved_by: string | null;
}
