import { BenchmarkChart } from "@/components/BenchmarkChart";
import { KpiTile } from "@/components/KpiTile";
import { sql } from "@/lib/db";
import {
  alignSeries,
  comparePerformance,
  fetchSpyHistory,
} from "@/lib/benchmark";
import { formatPct } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const revalidate = 300;

async function loadPerformance() {
  const equityCurve = (await sql<{ recorded_at: string; equity: number }>`
    SELECT recorded_at, equity
    FROM equity_curve
    ORDER BY recorded_at ASC
  `) as { recorded_at: string; equity: number }[];

  const [latestSession] = await sql<{ equity_start: number; started_at: string }>`
    SELECT equity_start, started_at
    FROM sessions
    ORDER BY started_at ASC
    LIMIT 1
  `;

  // Figure out how far back to fetch SPY based on the journal's earliest
  // date (with a sensible minimum + cap).
  const firstDate =
    equityCurve[0]?.recorded_at ?? latestSession?.started_at ?? new Date().toISOString();
  const daysBack = Math.max(
    30,
    Math.min(
      730,
      Math.ceil((Date.now() - new Date(firstDate).getTime()) / 86_400_000) + 14,
    ),
  );

  let spy: Awaited<ReturnType<typeof fetchSpyHistory>> = [];
  let benchmarkError: string | null = null;
  try {
    spy = await fetchSpyHistory(daysBack);
  } catch (e) {
    benchmarkError = e instanceof Error ? e.message : String(e);
  }

  const startEquity = Number(latestSession?.equity_start ?? 100_000);
  const aligned = spy.length
    ? alignSeries(
        equityCurve.map((r) => ({ recorded_at: r.recorded_at, equity: Number(r.equity) })),
        spy,
        startEquity,
      )
    : [];
  const metrics = comparePerformance(aligned);

  return { equityCurve, aligned, metrics, startEquity, benchmarkError };
}

export default async function PerformancePage() {
  let data: Awaited<ReturnType<typeof loadPerformance>> | null = null;
  let error: string | null = null;
  try {
    data = await loadPerformance();
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  if (error || !data) {
    return (
      <div className="rounded-xl border border-neutral-800 bg-neutral-900/40 p-8">
        <h2 className="text-lg font-semibold mb-2">Performance view unavailable</h2>
        {error && <p className="text-xs text-red-400 font-mono">{error}</p>}
      </div>
    );
  }

  const { aligned, metrics, benchmarkError } = data;
  const beating = metrics.alphaPct >= 0;

  return (
    <div className="space-y-6">
      {/* HERO: alpha summary */}
      <div className="rounded-xl border border-neutral-800 bg-gradient-to-b from-neutral-900/60 to-neutral-950/60 p-6">
        <div className="flex items-baseline justify-between flex-wrap gap-3 mb-1">
          <div>
            <div className="text-xs uppercase text-neutral-500 font-mono tracking-wider mb-1">
              Performance vs S&P 500
            </div>
            <div className="flex items-baseline gap-3 flex-wrap">
              <div
                className={`text-4xl md:text-5xl font-mono tabular-nums ${
                  beating ? "text-emerald-400" : "text-red-400"
                }`}
              >
                {formatPct(metrics.alphaPct)}
              </div>
              <div className="text-base text-neutral-400 font-mono">
                {beating ? "outperforming" : "underperforming"} SPY
              </div>
            </div>
            <div className="text-xs text-neutral-500 mt-2 font-mono">
              Portfolio {formatPct(metrics.portfolioTotalPct)} · SPY{" "}
              {formatPct(metrics.benchmarkTotalPct)} · {aligned.length} aligned data point
              {aligned.length === 1 ? "" : "s"}
            </div>
          </div>
        </div>

        <BenchmarkChart points={aligned} />

        {benchmarkError && (
          <div className="mt-3 text-xs text-amber-400 font-mono">
            SPY fetch failed: {benchmarkError}. Chart shows portfolio only.
          </div>
        )}
      </div>

      {/* KPI tiles: side-by-side comparison */}
      <div>
        <h2 className="text-sm font-mono text-neutral-400 uppercase tracking-wider mb-3">
          Head-to-head
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <KpiTile
            label="Total Return"
            value={formatPct(metrics.portfolioTotalPct)}
            sublabel="portfolio"
            tone={metrics.portfolioTotalPct >= 0 ? "positive" : "negative"}
          />
          <KpiTile
            label="Total Return"
            value={formatPct(metrics.benchmarkTotalPct)}
            sublabel="S&P 500 (SPY)"
            tone={metrics.benchmarkTotalPct >= 0 ? "positive" : "negative"}
          />
          <KpiTile
            label="Alpha"
            value={formatPct(metrics.alphaPct)}
            sublabel="portfolio − SPY"
            tone={beating ? "positive" : "negative"}
            large
          />
          <KpiTile
            label="Max Drawdown"
            value={formatPct(metrics.portfolioMaxDdPct)}
            sublabel="portfolio peak-to-trough"
            tone={metrics.portfolioMaxDdPct < -10 ? "negative" : "neutral"}
          />
          <KpiTile
            label="Max Drawdown"
            value={formatPct(metrics.benchmarkMaxDdPct)}
            sublabel="SPY peak-to-trough"
            tone={metrics.benchmarkMaxDdPct < -10 ? "negative" : "neutral"}
          />
          <KpiTile
            label="DD Differential"
            value={formatPct(metrics.portfolioMaxDdPct - metrics.benchmarkMaxDdPct)}
            sublabel="portfolio − SPY"
            tone={
              metrics.portfolioMaxDdPct - metrics.benchmarkMaxDdPct >= 0
                ? "positive"
                : "warning"
            }
          />
        </div>
      </div>

      {/* Honesty footer */}
      <div className="rounded-lg border border-neutral-800 p-4 text-xs text-neutral-500 leading-relaxed">
        <strong className="text-neutral-300">How to read this:</strong> SPY is normalized
        to start at the same equity as the portfolio on day one, so both curves move
        from a common baseline. Alpha is the simple arithmetic difference of cumulative
        returns — not risk-adjusted. With few data points the comparison is informative
        but not statistically meaningful. SPY data fetched from Yahoo Finance (cached 1h).
      </div>
    </div>
  );
}
