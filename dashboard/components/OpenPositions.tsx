import { formatCurrency, formatPct } from "@/lib/utils";

export type Position = {
  symbol: string;
  asset_class: string;
  qty: number | string;
  avg_entry: number | string | null;
  market_value: number | string;
  unrealized_pl: number | string | null;
  option_type: string | null;
  strike: number | string | null;
  expiry: string | null;
};

export function OpenPositions({
  positions,
  emptyHint = "No open positions yet — entries appear here as the strategy fills them.",
}: {
  positions: Position[];
  emptyHint?: string;
}) {
  if (positions.length === 0) {
    return (
      <div className="rounded-lg border border-neutral-800 p-6 text-center">
        <div className="text-sm text-neutral-500">{emptyHint}</div>
      </div>
    );
  }

  return (
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
            const entryValue = Number(p.avg_entry ?? 0) * Number(p.qty ?? 0);
            const uplPct = entryValue !== 0 ? (upl / Math.abs(entryValue)) * 100 : 0;
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
                <td className="px-4 py-3 text-right font-mono tabular-nums">{p.qty}</td>
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
                  <span className="text-xs ml-1 opacity-70">({formatPct(uplPct, 1)})</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
