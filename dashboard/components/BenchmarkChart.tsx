"use client";

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { AlignedPoint } from "@/lib/benchmark";

export function BenchmarkChart({ points }: { points: AlignedPoint[] }) {
  if (!points.length) {
    return (
      <div className="flex items-center justify-center h-80 text-sm text-neutral-500">
        Not enough data yet to chart vs SPY. Comes alive after a few sessions.
      </div>
    );
  }

  const data = points.map((p) => ({
    date: new Date(p.date).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    }),
    Portfolio: Number(p.equity),
    "S&P 500 (SPY)": Number(p.benchmark),
  }));

  const allValues = data.flatMap((d) => [d.Portfolio, d["S&P 500 (SPY)"]]);
  const yMin = Math.min(...allValues) * 0.99;
  const yMax = Math.max(...allValues) * 1.01;

  return (
    <div className="h-80 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 4, right: 12, bottom: 4, left: 12 }}>
          <defs>
            <linearGradient id="portfolioGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#10b981" stopOpacity={0.25} />
              <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
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
            formatter={(v: number) =>
              new Intl.NumberFormat("en-US", {
                style: "currency",
                currency: "USD",
                maximumFractionDigits: 2,
              }).format(v)
            }
          />
          <Legend
            wrapperStyle={{ fontSize: 12, color: "#a3a3a3", paddingTop: 8 }}
            iconType="line"
          />
          <Area
            type="monotone"
            dataKey="Portfolio"
            stroke="#10b981"
            strokeWidth={2}
            fill="url(#portfolioGradient)"
            dot={data.length <= 30 ? { r: 3, fill: "#10b981" } : false}
          />
          <Line
            type="monotone"
            dataKey="S&P 500 (SPY)"
            stroke="#a3a3a3"
            strokeWidth={2}
            strokeDasharray="4 4"
            dot={data.length <= 30 ? { r: 3, fill: "#a3a3a3" } : false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
