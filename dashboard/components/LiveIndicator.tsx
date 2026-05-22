// Animated "● LIVE" pill — pulsing green dot when the dashboard has any
// session data, gray when stale (>24h since last cycle).

export function LiveIndicator({ lastUpdate }: { lastUpdate: string | null }) {
  const ageMs = lastUpdate ? Date.now() - new Date(lastUpdate).getTime() : Infinity;
  const isFresh = ageMs < 24 * 60 * 60 * 1000;

  return (
    <span
      className={`inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-mono border ${
        isFresh
          ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/30"
          : "bg-neutral-800 text-neutral-500 border-neutral-700"
      }`}
    >
      <span className="relative inline-flex h-2 w-2">
        {isFresh && (
          <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75 animate-ping" />
        )}
        <span
          className={`relative inline-flex rounded-full h-2 w-2 ${
            isFresh ? "bg-emerald-400" : "bg-neutral-500"
          }`}
        />
      </span>
      {isFresh ? "LIVE" : "STALE"}
    </span>
  );
}
