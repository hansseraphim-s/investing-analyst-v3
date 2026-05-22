import { EquityChart } from "@/components/EquityChart";
import { KpiTile } from "@/components/KpiTile";
import { LiveIndicator } from "@/components/LiveIndicator";
import { RecentActivity } from "@/components/RecentActivity";
import { sql } from "@/lib/db";
import { computeMetrics, type EquityPoint } from "@/lib/metrics";
import { formatCurrency, formatPct } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const revalidate = 30;

async function getLiveSnapshot() {
  const [latestSession] = await sql`
    SELECT id, trading_mode, strategy, equity_start, equity_end, cash_end,
           day_pnl_pct, started_at, ended_at
    FROM sessions
    ORDER BY started_at DESC
    LIMIT 1
  `;

  // Pull equity curve asc so the chart can use it directly.
  const equityDesc = await sql`
    SELECT recorded_at, equity, cash, drawdown_pct
    FROM equity_curve
    ORDER BY recorded_at DESC
    LIMIT 500
  `;
  const equityCurve = (equityDesc as EquityPoint[]).slice().reverse();

  const positions = await sql`
    SELECT symbol, asset_class, qty, avg_entry, market_value, unrealized_pl,
           option_type, strike, expiry
    FROM positions
    WHERE snapshot_at > now() - interval '1 day'
    ORDER BY market_value DESC
  `;

  const recentOrders = await sql`
    SELECT submitted_at, symbol, side, qty, price, status, reason
    FROM orders
    ORDER BY submitted_at DESC
    LIMIT 8
  `;

  return { latestSession, equityCurve, positions, recentOrders };
}

export default async function LivePage() {
  let data: Awaited<ReturnType<typeof getLiveSnapshot>> | null = null;
  let error: string | null = null;
  try {
    data = await getLiveSnapshot();
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  if (error || !data || !data.latestSession) {
    return (
      <div className="rounded-xl border border-neutral-800 bg-neutral-900/40 p-8">
        <h2 className="text-lg font-semibold mb-2">No journal data yet</h2>
        <p className="text-sm text-neutral-400 mb-4">
          The dashboard is connected, but the agent hasn&apos;t written a session
          yet. Run a cycle:
        </p>
        <pre className="bg-neutral-950 rounded-lg p-4 text-xs font-mono overflow-x-auto border border-neutral-800">
          {"cd ~/iav3 && source agent/.venv/bin/activate && iav3 paper"}
        </pre>
        {error && (
          <p className="text-xs text-red-400 mt-3 font-mono">DB: {error}</p>
        )}
      </div>
    );
  }

  const { latestSession, equityCurve, positions, recentOrders } = data;
  const equity = Number(latestSession.equity_end ?? 0);
  const cash = Number(latestSession.cash_end ?? 0);
  const pnl = Number(latestSession.day_pnl_pct ?? 0);
  const startEquity = Number(latestSession.equity_start ?? 100_000);
  const invested = equity - cash;
  const investedPct = equity > 0 ? (invested / equity) * 100 : 0;

  const metrics = computeMetrics(equityCurve, startEquity);

  return (
    <div className="space-y-6">
      {/* HERO: equity + chart */}
      <div className="rounded-xl border border-neutral-800 bg-gradient-to-b from-neutral-900/60 to-neutral-950/60 p-6">
        <div className="flex items-start justify-between mb-4 flex-wrap gap-3">
          <div>
            <div className="text-xs uppercase text-neutral-500 font-mono tracking-wider mb-1">
              Portfolio Equity
            </div>
            <div className="flex items-baseline gap-3 flex-wrap">
              <div className="text-4xl md:text-5xl font-mono tabular-nums">
                {formatCurrency(equity)}
              </div>
              <div
                className={`text-lg font-mono ${
                  pnl >= 0 ? "text-emerald-400" : "text-red-400"
                }`}
              >
                {formatPct(pnl)} today
              </div>
            </div>
            <div className="text-xs text-neutral-500 mt-2 font-mono">
              Started at {formatCurrency(startEquity)}
              {metrics.startDate &&
                ` · ${new Date(metrics.startDate).toLocaleDateString("en-US", {
                  month: "short",
                  day: "numeric",
                  year: "numeric",
                })}`}{" "}
              · {metrics.numSessions} session
              {metrics.numSessions === 1 ? "" : "s"}
            </div>
          </div>
          <LiveIndicator lastUpdate={latestSession.started_at ?? null} />
        </div>

        <EquityChart points={equityCurve} startEquity={startEquity} />
      </div>

      {/* KPI TILES */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
        <KpiTile
          label="Total Return"
          value={formatPct(metrics.totalReturnPct)}
          sublabel={`${metrics.daysActive}d active`}
          tone={metrics.totalReturnPct >= 0 ? "positive" : "negative"}
        />
        <KpiTile
          label="Cash"
          value={formatCurrency(cash)}
          sublabel={`${Math.round(100 - investedPct)}% of book`}
        />
        <KpiTile
          label="Invested"
          value={formatCurrency(invested)}
          sublabel={`${Math.round(investedPct)}% of book`}
        />
        <KpiTile
          label="Max Drawdown"
          value={formatPct(metrics.maxDrawdownPct)}
          sublabel={`peak-to-trough`}
          tone={metrics.maxDrawdownPct < -10 ? "negative" : metrics.maxDrawdownPct < -5 ? "warning" : "neutral"}
        />
        <KpiTile
          label="Sharpe (annualized)"
          value={metrics.sharpe.toFixed(2)}
          sublabel={metrics.numSessions < 30 ? "needs 30+ days" : "stable"}
          tone={metrics.sharpe > 1 ? "positive" : metrics.sharpe < 0 ? "negative" : "neutral"}
        />
      </div>

      {/* OPEN POSITIONS */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-mono text-neutral-400 uppercase tracking-wider">
            Open Positions
          </h2>
          <span className="text-xs text-neutral-600 font-mono">
            {positions.length} {positions.length === 1 ? "position" : "positions"}
          </span>
        </div>
        {positions.length === 0 ? (
          <div className="rounded-lg border border-neutral-800 p-6 text-center">
            <div className="text-sm text-neutral-500">
              No open positions. Entries appear here as the strategy fills them.
            </div>
          </div>
        ) : (
          <div className="rounded-lg border border-neutral-800 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-neutral-900/60 text-xs uppercase text-neutral-500">
                <tr>
                  <th className="text-left px-4 py-3 font-mono">Symbol</th>
                  <th className="text-left px-4 py-3 font-mono">Type</th>
                  <th className="text-right px-4 py-3 font-mono">Qty</th>
                  <th className="text-right px-4 py-3 font-mono">Entry</th>
                  <th className="text-right px-4 py-3 font-mono">Market Value</th>
                  <th className="text-right px-4 py-3 font-mono">Unrealized</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => {
                  const upl = Number(p.unrealized_pl ?? 0);
                  const uplPct =
                    Number(p.avg_entry) > 0 && Number(p.qty) !== 0
                      ? (upl / (Number(p.avg_entry) * Number(p.qty))) * 100
                      : 0;
                  return (
                    <tr
                      key={i}
                      className="border-t border-neutral-800 hover:bg-neutral-900/30 transition-colors"
                    >
                      <td className="px-4 py-3 font-mono font-medium">{p.symbol}</td>
                      <td className="px-4 py-3 text-neutral-400 text-xs">
                        {p.asset_class === "option"
                          ? `${p.option_type?.toUpperCase()} ${p.strike} ${p.expiry}`
                          : "stock"}
                      </td>
                      <td className="px-4 py-3 text-right font-mono tabular-nums">
                        {p.qty}
                      </td>
                      <td className="px-4 py-3 text-right font-mono tabular-nums">
                        {formatCurrency(p.avg_entry)}
                      </td>
                      <td className="px-4 py-3 text-right font-mono tabular-nums">
                        {formatCurrency(p.market_value)}
                      </td>
                      <td
                        className={`px-4 py-3 text-right font-mono tabular-nums ${
                          upl >= 0 ? "text-emerald-400" : "text-red-400"
                        }`}
                      >
                        {formatCurrency(upl)}
                        <span className="text-xs ml-1 opacity-70">
                          ({formatPct(uplPct, 1)})
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* RECENT ACTIVITY */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-mono text-neutral-400 uppercase tracking-wider">
            Recent Activity
          </h2>
          <span className="text-xs text-neutral-600 font-mono">
            last {recentOrders.length} {recentOrders.length === 1 ? "order" : "orders"}
          </span>
        </div>
        <RecentActivity rows={recentOrders} />
      </section>
    </div>
  );
}
