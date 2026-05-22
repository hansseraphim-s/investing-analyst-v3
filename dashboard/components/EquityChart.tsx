"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

type Point = {
  recorded_at: string;
  equity: number;
};

export function EquityChart({
  points,
  startEquity,
}: {
  points: Point[];
  startEquity: number;
}) {
  if (!points.length) {
    return (
      <div className="flex items-center justify-center h-72 text-sm text-neutral-500">
        No equity history yet — chart fills in as cycles run.
      </div>
    );
  }

  // Normalize: equity values in dollars; x-axis as session index (most
  // recent on the right). Use timestamps as tooltip but compact session
  // labels on the axis.
  const data = points.map((p, i) => ({
    idx: i,
    date: new Date(p.recorded_at).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    }),
    equity: Number(p.equity),
  }));

  const current = data[data.length - 1].equity;
  const isPositive = current >= startEquity;
  const colorGradient = isPositive ? "rgb(16, 185, 129)" : "rgb(239, 68, 68)";
  const colorLine = isPositive ? "#10b981" : "#ef4444";

  // Tighten the y-axis a bit so small moves aren't visually dominated
  // by the absolute scale near the origin.
  const equities = data.map((d) => d.equity);
  const yMin = Math.min(...equities, startEquity) * 0.995;
  const yMax = Math.max(...equities, startEquity) * 1.005;

  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 12, bottom: 4, left: 12 }}>
          <defs>
            <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={colorGradient} stopOpacity={0.4} />
              <stop offset="95%" stopColor={colorGradient} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#262626" vertical={false} />
          <XAxis
            dataKey="date"
            stroke="#525252"
            fontSize={11}
            tick={{ fill: "#737373" }}
            tickLine={{ stroke: "#404040" }}
            axisLine={{ stroke: "#404040" }}
            minTickGap={32}
          />
          <YAxis
            domain={[yMin, yMax]}
            stroke="#525252"
            fontSize={11}
            tick={{ fill: "#737373" }}
            tickLine={{ stroke: "#404040" }}
            axisLine={{ stroke: "#404040" }}
            tickFormatter={(v) =>
              new Intl.NumberFormat("en-US", {
                style: "currency",
                currency: "USD",
                maximumFractionDigits: 0,
              }).format(v)
            }
            width={75}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "#0a0a0a",
              border: "1px solid #404040",
              borderRadius: 6,
              fontSize: 12,
            }}
            labelStyle={{ color: "#a3a3a3" }}
            itemStyle={{ color: colorLine }}
            formatter={(v: number) =>
              new Intl.NumberFormat("en-US", {
                style: "currency",
                currency: "USD",
                maximumFractionDigits: 2,
              }).format(v)
            }
          />
          <Area
            type="monotone"
            dataKey="equity"
            stroke={colorLine}
            strokeWidth={2}
            fill="url(#equityFill)"
            dot={data.length <= 30 ? { r: 3, fill: colorLine } : false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
