// Compact feed of recent orders — entry/exit/block actions sorted newest first.

type ActivityRow = {
  submitted_at: string;
  symbol: string;
  side: string;
  qty: number;
  price: string | number;
  status: string;
  reason: string;
};

function formatRelative(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60_000);
  const h = Math.floor(m / 60);
  const d = Math.floor(h / 24);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  if (h < 24) return `${h}h ago`;
  return `${d}d ago`;
}

export function RecentActivity({ rows }: { rows: ActivityRow[] }) {
  if (!rows.length) {
    return (
      <div className="rounded-lg border border-neutral-800 p-6 text-center">
        <div className="text-sm text-neutral-500">
          No recent activity. Orders will appear here after the agent acts.
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-neutral-800 divide-y divide-neutral-800">
      {rows.map((r, i) => {
        const isEntry = r.status === "filled" && r.side === "BUY";
        const isExit = r.status === "filled" && r.side === "SELL";
        const isBlocked = r.status === "blocked";
        const dotColor = isEntry
          ? "bg-emerald-400"
          : isExit
            ? "bg-blue-400"
            : isBlocked
              ? "bg-amber-400"
              : "bg-neutral-500";
        const label = isEntry
          ? "ENTRY"
          : isExit
            ? "EXIT"
            : isBlocked
              ? "BLOCKED"
              : r.status.toUpperCase();
        const labelClass = isEntry
          ? "text-emerald-400"
          : isExit
            ? "text-blue-400"
            : isBlocked
              ? "text-amber-400"
              : "text-neutral-500";
        return (
          <div
            key={i}
            className="flex items-center gap-4 px-4 py-3 hover:bg-neutral-900/40 transition-colors"
          >
            <div className={`h-2 w-2 rounded-full ${dotColor} shrink-0`} />
            <div className={`text-xs font-mono w-16 ${labelClass}`}>{label}</div>
            <div className="font-mono text-sm w-20">{r.symbol}</div>
            <div className="text-sm text-neutral-400 flex-1 truncate">
              {r.qty} @ ${Number(r.price).toFixed(2)}
              <span className="text-neutral-600 ml-2 text-xs">{r.reason}</span>
            </div>
            <div className="text-xs text-neutral-500 font-mono shrink-0">
              {formatRelative(r.submitted_at)}
            </div>
          </div>
        );
      })}
    </div>
  );
}
