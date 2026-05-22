import { sql } from "@/lib/db";
import { formatCurrency } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const revalidate = 30;

async function getOrders() {
  try {
    return await sql`
      SELECT submitted_at, symbol, asset_class, side, qty, price, status, reason, advisor_rationale
      FROM orders
      ORDER BY submitted_at DESC
      LIMIT 200
    `;
  } catch {
    return [];
  }
}

export default async function JournalPage() {
  const orders = await getOrders();

  if (orders.length === 0) {
    return (
      <div className="rounded-lg border border-neutral-800 p-6">
        <h2 className="text-lg font-semibold mb-2">Trade journal</h2>
        <p className="text-sm text-neutral-400">No orders yet. Run a paper cycle to populate.</p>
      </div>
    );
  }

  return (
    <div>
      <h2 className="text-sm font-mono text-neutral-400 mb-3">TRADE JOURNAL (last 200)</h2>
      <div className="rounded-lg border border-neutral-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-neutral-900 text-xs uppercase text-neutral-500">
            <tr>
              <th className="text-left px-4 py-2">Time</th>
              <th className="text-left px-4 py-2">Symbol</th>
              <th className="text-left px-4 py-2">Side</th>
              <th className="text-right px-4 py-2">Qty</th>
              <th className="text-right px-4 py-2">Price</th>
              <th className="text-left px-4 py-2">Status</th>
              <th className="text-left px-4 py-2">Reason</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((o, i) => {
              const statusColor =
                o.status === "filled"
                  ? "text-emerald-400"
                  : o.status === "blocked" || o.status === "rejected"
                    ? "text-red-400"
                    : "text-neutral-400";
              return (
                <tr key={i} className="border-t border-neutral-800">
                  <td className="px-4 py-2 font-mono text-xs text-neutral-500">
                    {new Date(o.submitted_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-2 font-mono">{o.symbol}</td>
                  <td className="px-4 py-2 text-neutral-400">{o.side}</td>
                  <td className="px-4 py-2 text-right font-mono">{o.qty}</td>
                  <td className="px-4 py-2 text-right font-mono">{formatCurrency(o.price)}</td>
                  <td className={`px-4 py-2 font-mono text-xs ${statusColor}`}>{o.status}</td>
                  <td className="px-4 py-2 text-neutral-400 text-xs">{o.reason}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
