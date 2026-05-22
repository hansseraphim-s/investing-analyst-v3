import { sql } from "@/lib/db";
import { formatCurrency, formatPct } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const revalidate = 30;

async function getLiveSnapshot() {
  const [latestSession] = await sql`
    SELECT id, trading_mode, strategy, equity_end, cash_end, day_pnl_pct,
           started_at, ended_at
    FROM sessions
    ORDER BY started_at DESC
    LIMIT 1
  `;

  const equity = await sql`
    SELECT recorded_at, equity, cash, drawdown_pct
    FROM equity_curve
    ORDER BY recorded_at DESC
    LIMIT 500
  `;

  const positions = await sql`
    SELECT symbol, asset_class, qty, avg_entry, market_value, unrealized_pl,
           option_type, strike, expiry
    FROM positions
    WHERE snapshot_at > now() - interval '1 day'
    ORDER BY market_value DESC
  `;

  return { latestSession, equity: equity.reverse(), positions };
}

export default async function LivePage() {
  let data: Awaited<ReturnType<typeof getLiveSnapshot>> | null = null;
  let error: string | null = null;
  try {
    data = await getLiveSnapshot();
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  if (error || !data) {
    return (
      <div className="rounded-lg border border-neutral-800 p-6">
        <h2 className="text-lg font-semibold mb-2">No journal data yet</h2>
        <p className="text-sm text-neutral-400 mb-4">
          The dashboard is connected, but the agent hasn&apos;t written a session yet. Run a cycle:
        </p>
        <pre className="bg-neutral-900 rounded p-3 text-xs font-mono overflow-x-auto">
          cd agent &amp;&amp; iav3 paper --strategy vol_target_trend
        </pre>
        {error && (
          <p className="text-xs text-red-400 mt-3 font-mono">DB: {error}</p>
        )}
      </div>
    );
  }

  const { latestSession, positions } = data;
  const equity = Number(latestSession?.equity_end ?? 0);
  const cash = Number(latestSession?.cash_end ?? 0);
  const pnl = Number(latestSession?.day_pnl_pct ?? 0);

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <KpiTile label="Equity" value={formatCurrency(equity)} />
        <KpiTile label="Cash" value={formatCurrency(cash)} />
        <KpiTile
          label="Day P&L"
          value={formatPct(pnl)}
          tone={pnl >= 0 ? "positive" : "negative"}
        />
        <KpiTile label="Strategy" value={latestSession?.strategy ?? "—"} />
      </div>

      <section>
        <h2 className="text-sm font-mono text-neutral-400 mb-3">OPEN POSITIONS</h2>
        {positions.length === 0 ? (
          <p className="text-sm text-neutral-500">No open positions.</p>
        ) : (
          <div className="rounded-lg border border-neutral-800 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-neutral-900 text-xs uppercase text-neutral-500">
                <tr>
                  <th className="text-left px-4 py-2">Symbol</th>
                  <th className="text-left px-4 py-2">Type</th>
                  <th className="text-right px-4 py-2">Qty</th>
                  <th className="text-right px-4 py-2">Entry</th>
                  <th className="text-right px-4 py-2">Market Value</th>
                  <th className="text-right px-4 py-2">Unrealized P/L</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => (
                  <tr key={i} className="border-t border-neutral-800">
                    <td className="px-4 py-2 font-mono">{p.symbol}</td>
                    <td className="px-4 py-2 text-neutral-400">
                      {p.asset_class === "option"
                        ? `${p.option_type?.toUpperCase()} ${p.strike} ${p.expiry}`
                        : "stock"}
                    </td>
                    <td className="px-4 py-2 text-right font-mono">{p.qty}</td>
                    <td className="px-4 py-2 text-right font-mono">{formatCurrency(p.avg_entry)}</td>
                    <td className="px-4 py-2 text-right font-mono">{formatCurrency(p.market_value)}</td>
                    <td
                      className={`px-4 py-2 text-right font-mono ${
                        Number(p.unrealized_pl ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"
                      }`}
                    >
                      {formatCurrency(p.unrealized_pl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function KpiTile({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "positive" | "negative";
}) {
  const toneClass =
    tone === "positive"
      ? "text-emerald-400"
      : tone === "negative"
        ? "text-red-400"
        : "text-neutral-100";
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-4">
      <div className="text-xs uppercase text-neutral-500 font-mono">{label}</div>
      <div className={`text-2xl font-mono mt-1 ${toneClass}`}>{value}</div>
    </div>
  );
}
