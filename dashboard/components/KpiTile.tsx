import { cn } from "@/lib/utils";

type Tone = "neutral" | "positive" | "negative" | "warning";

export function KpiTile({
  label,
  value,
  sublabel,
  tone = "neutral",
  large = false,
}: {
  label: string;
  value: string;
  sublabel?: string;
  tone?: Tone;
  large?: boolean;
}) {
  const toneClass: Record<Tone, string> = {
    neutral: "text-neutral-100",
    positive: "text-emerald-400",
    negative: "text-red-400",
    warning: "text-amber-400",
  };

  return (
    <div
      className={cn(
        "group rounded-lg border border-neutral-800 bg-neutral-900/40 p-4",
        "transition-all hover:border-neutral-700 hover:bg-neutral-900/60",
      )}
    >
      <div className="text-xs uppercase text-neutral-500 font-mono tracking-wider">
        {label}
      </div>
      <div
        className={cn(
          "font-mono mt-1 tabular-nums",
          large ? "text-3xl md:text-4xl" : "text-2xl",
          toneClass[tone],
        )}
      >
        {value}
      </div>
      {sublabel && (
        <div className="text-xs text-neutral-500 mt-1.5 font-mono">
          {sublabel}
        </div>
      )}
    </div>
  );
}
