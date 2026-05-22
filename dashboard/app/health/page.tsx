import { sql } from "@/lib/db";

export const dynamic = "force-dynamic";
export const revalidate = 10;

async function getHealth() {
  try {
    const [latest] = await sql`
      SELECT started_at, ended_at, trading_mode, strategy
      FROM sessions ORDER BY started_at DESC LIMIT 1
    `;
    const [counts] = await sql`
      SELECT
        (SELECT COUNT(*) FROM orders) AS orders,
        (SELECT COUNT(*) FROM sessions) AS sessions,
        (SELECT COUNT(*) FROM signals) AS signals
    `;
    return { latest, counts, db_ok: true, error: null };
  } catch (e) {
    return {
      latest: null,
      counts: null,
      db_ok: false,
      error: e instanceof Error ? e.message : String(e),
    };
  }
}

export default async function HealthPage() {
  const h = await getHealth();
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-neutral-800 p-4">
        <div className="text-xs font-mono uppercase text-neutral-500 mb-2">DATABASE</div>
        <div className={`text-sm font-mono ${h.db_ok ? "text-emerald-400" : "text-red-400"}`}>
          {h.db_ok ? "● connected" : "● disconnected"}
        </div>
        {h.error && <pre className="text-xs text-red-400 mt-2">{h.error}</pre>}
      </div>
      {h.counts && (
        <div className="grid grid-cols-3 gap-4">
          <Tile label="Sessions" value={String(h.counts.sessions)} />
          <Tile label="Orders" value={String(h.counts.orders)} />
          <Tile label="Signals" value={String(h.counts.signals)} />
        </div>
      )}
      {h.latest && (
        <div className="rounded-lg border border-neutral-800 p-4 text-sm">
          <div className="text-xs font-mono uppercase text-neutral-500 mb-2">LATEST SESSION</div>
          <div className="font-mono text-neutral-300">
            {h.latest.strategy} · {h.latest.trading_mode} ·{" "}
            {new Date(h.latest.started_at).toLocaleString()}
          </div>
        </div>
      )}
    </div>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-4">
      <div className="text-xs uppercase text-neutral-500 font-mono">{label}</div>
      <div className="text-2xl font-mono mt-1">{value}</div>
    </div>
  );
}
