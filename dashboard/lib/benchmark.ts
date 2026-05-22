// Benchmark data fetch + alignment helpers.
//
// We fetch SPY (S&P 500 ETF, used as the index proxy) historical daily
// closes from Yahoo's public chart endpoint. No API key required.
// Cached at the Next.js level via the page's `revalidate` directive.

export type SpyPoint = {
  date: string;     // YYYY-MM-DD
  close: number;
};

const YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/SPY";

export async function fetchSpyHistory(
  daysBack = 365,
): Promise<SpyPoint[]> {
  const range =
    daysBack <= 30 ? "1mo" :
    daysBack <= 90 ? "3mo" :
    daysBack <= 180 ? "6mo" :
    daysBack <= 365 ? "1y" :
    daysBack <= 730 ? "2y" :
    "5y";

  const url = `${YAHOO_CHART}?range=${range}&interval=1d`;
  const res = await fetch(url, {
    // Yahoo blocks requests without a UA that looks browser-ish.
    headers: { "User-Agent": "Mozilla/5.0 (compatible; iav3-dashboard/1.0)" },
    // Cache for an hour at the Vercel CDN level — daily-bar data, doesn't
    // change minute-to-minute and we don't want to hammer Yahoo on every
    // page view.
    next: { revalidate: 3600 },
  });
  if (!res.ok) {
    throw new Error(`Yahoo SPY fetch failed: HTTP ${res.status}`);
  }
  const json = (await res.json()) as {
    chart?: {
      result?: Array<{
        timestamp: number[];
        indicators: { quote: Array<{ close: (number | null)[] }> };
      }>;
      error?: { description?: string };
    };
  };

  if (json.chart?.error?.description) {
    throw new Error(`Yahoo SPY error: ${json.chart.error.description}`);
  }
  const result = json.chart?.result?.[0];
  if (!result) {
    throw new Error("Yahoo SPY: no result rows");
  }

  const closes = result.indicators.quote[0].close;
  const points: SpyPoint[] = [];
  for (let i = 0; i < result.timestamp.length; i++) {
    const close = closes[i];
    if (close == null) continue;
    const date = new Date(result.timestamp[i] * 1000).toISOString().slice(0, 10);
    points.push({ date, close });
  }
  return points;
}


// Align an SPY series to a portfolio equity curve. For each portfolio
// timestamp we find the SPY close on the same trading day; SPY value
// gets normalized so it starts at the portfolio's starting equity.
//
// Returns an aligned list of {date, equity, benchmark, equityNorm, benchmarkNorm}
// suitable for a dual-line chart.
export type AlignedPoint = {
  date: string;
  equity: number;
  benchmark: number;
};

export function alignSeries(
  portfolio: Array<{ recorded_at: string; equity: number }>,
  spy: SpyPoint[],
  startEquity: number,
): AlignedPoint[] {
  if (!portfolio.length || !spy.length) return [];

  // Index SPY by date for O(1) lookup
  const spyByDate = new Map<string, number>();
  for (const p of spy) spyByDate.set(p.date, p.close);

  // Find SPY's value on the first portfolio date (or the most recent prior trading day)
  const first = portfolio[0];
  const firstDate = first.recorded_at.slice(0, 10);
  // Walk forward through SPY data to find the first date >= portfolio start
  const sortedSpy = [...spy].sort((a, b) => a.date.localeCompare(b.date));
  const spyBaselineEntry = sortedSpy.find((p) => p.date >= firstDate);
  if (!spyBaselineEntry) return [];
  const spyBaseline = spyBaselineEntry.close;

  const out: AlignedPoint[] = [];
  for (const p of portfolio) {
    const date = p.recorded_at.slice(0, 10);
    // Find closest SPY date <= portfolio date
    let spyClose: number | undefined = spyByDate.get(date);
    if (spyClose == null) {
      // Walk backwards to find prior trading day
      for (let i = sortedSpy.length - 1; i >= 0; i--) {
        if (sortedSpy[i].date <= date) {
          spyClose = sortedSpy[i].close;
          break;
        }
      }
    }
    if (spyClose == null) continue;
    const benchmarkValue = (spyClose / spyBaseline) * startEquity;
    out.push({
      date,
      equity: Number(p.equity),
      benchmark: benchmarkValue,
    });
  }
  return out;
}


export type PerformanceMetrics = {
  portfolioTotalPct: number;
  benchmarkTotalPct: number;
  alphaPct: number;             // portfolio - benchmark, in absolute % points
  portfolioMaxDdPct: number;
  benchmarkMaxDdPct: number;
};


export function comparePerformance(
  aligned: AlignedPoint[],
): PerformanceMetrics {
  if (aligned.length < 2) {
    return {
      portfolioTotalPct: 0,
      benchmarkTotalPct: 0,
      alphaPct: 0,
      portfolioMaxDdPct: 0,
      benchmarkMaxDdPct: 0,
    };
  }
  const first = aligned[0];
  const last = aligned[aligned.length - 1];
  const portfolioTotal = (last.equity / first.equity - 1) * 100;
  const benchmarkTotal = (last.benchmark / first.benchmark - 1) * 100;

  const maxDd = (series: number[]): number => {
    let peak = series[0];
    let worst = 0;
    for (const v of series) {
      if (v > peak) peak = v;
      const dd = (v / peak - 1) * 100;
      if (dd < worst) worst = dd;
    }
    return worst;
  };

  return {
    portfolioTotalPct: portfolioTotal,
    benchmarkTotalPct: benchmarkTotal,
    alphaPct: portfolioTotal - benchmarkTotal,
    portfolioMaxDdPct: maxDd(aligned.map((a) => a.equity)),
    benchmarkMaxDdPct: maxDd(aligned.map((a) => a.benchmark)),
  };
}
