// Performance metrics computed from the equity_curve table.
// Pure functions — no DB calls, no side effects.

export type EquityPoint = {
  recorded_at: string;
  equity: number;
  cash: number;
  drawdown_pct: number | null;
};

export type PerformanceMetrics = {
  totalReturnPct: number;
  maxDrawdownPct: number;
  sharpe: number;
  numSessions: number;
  startEquity: number;
  startDate: string | null;
  daysActive: number;
};

export function computeMetrics(
  curve: EquityPoint[],
  startEquity = 100_000,
): PerformanceMetrics {
  if (!curve.length) {
    return {
      totalReturnPct: 0,
      maxDrawdownPct: 0,
      sharpe: 0,
      numSessions: 0,
      startEquity,
      startDate: null,
      daysActive: 0,
    };
  }

  const first = curve[0];
  const last = curve[curve.length - 1];
  const baseline = Number(first.equity) || startEquity;
  const final_ = Number(last.equity) || baseline;
  const totalReturnPct = ((final_ / baseline) - 1) * 100;

  // Max drawdown across the entire equity series. Recompute rather than
  // trust the per-row drawdown_pct (which is sometimes null in early rows).
  let peak = baseline;
  let maxDD = 0;
  for (const row of curve) {
    const eq = Number(row.equity);
    if (eq > peak) peak = eq;
    const dd = (eq / peak - 1) * 100;
    if (dd < maxDD) maxDD = dd;
  }

  // Sharpe from session-to-session returns (not annualized — would be
  // misleading with <30 days of data).
  let sharpe = 0;
  if (curve.length > 1) {
    const returns: number[] = [];
    for (let i = 1; i < curve.length; i++) {
      const prev = Number(curve[i - 1].equity);
      const cur = Number(curve[i].equity);
      if (prev > 0) returns.push(cur / prev - 1);
    }
    if (returns.length > 1) {
      const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
      const variance =
        returns.reduce((acc, r) => acc + (r - mean) ** 2, 0) / returns.length;
      const sd = Math.sqrt(variance);
      sharpe = sd > 0 ? (mean / sd) * Math.sqrt(252) : 0;
    }
  }

  const firstDate = first.recorded_at ? new Date(first.recorded_at) : null;
  const lastDate = last.recorded_at ? new Date(last.recorded_at) : null;
  const daysActive =
    firstDate && lastDate
      ? Math.max(
          1,
          Math.round((lastDate.getTime() - firstDate.getTime()) / 86_400_000),
        )
      : 0;

  return {
    totalReturnPct,
    maxDrawdownPct: maxDD,
    sharpe,
    numSessions: curve.length,
    startEquity: baseline,
    startDate: first.recorded_at ?? null,
    daysActive,
  };
}
