"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { moneyK, fmtDate } from "@/lib/format";

const ACCENT = "#4f46e5";
const GOOD = "#059669";
const FAINT = "#94a3b8";

function TipBox({
  active,
  payload,
  label,
  fmt,
}: {
  active?: boolean;
  payload?: { name: string; value: number; color: string }[];
  label?: string;
  fmt: (n: number) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-line bg-white px-3 py-2 shadow-pop text-xs">
      <div className="font-semibold text-ink mb-1">{fmtDate(label)}</div>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center gap-2 text-ink-soft">
          <span
            className="w-2 h-2 rounded-full"
            style={{ background: p.color }}
          />
          <span className="capitalize">{p.name}</span>
          <span className="ml-auto font-semibold text-ink tabular-nums">
            {fmt(p.value)}
          </span>
        </div>
      ))}
    </div>
  );
}

export function Spark({
  data,
  dataKey = "revenue",
  color = ACCENT,
  height = 40,
}: {
  data: Record<string, unknown>[];
  dataKey?: string;
  color?: string;
  height?: number;
}) {
  if (!data?.length)
    return <div style={{ height }} className="rounded bg-line2" />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 2, bottom: 2, left: 0, right: 0 }}>
        <defs>
          <linearGradient id={`sp-${color}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.3} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area
          type="monotone"
          dataKey={dataKey}
          stroke={color}
          strokeWidth={2}
          fill={`url(#sp-${color})`}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function RevenueArea({
  data,
  height = 260,
}: {
  data: { date: string; revenue: number }[];
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
        <defs>
          <linearGradient id="rev-g" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={ACCENT} stopOpacity={0.22} />
            <stop offset="100%" stopColor={ACCENT} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="#f1f5f9" vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={fmtDate}
          tick={{ fontSize: 11, fill: FAINT }}
          axisLine={false}
          tickLine={false}
          minTickGap={28}
        />
        <YAxis
          tickFormatter={(v) => moneyK(v)}
          tick={{ fontSize: 11, fill: FAINT }}
          axisLine={false}
          tickLine={false}
          width={48}
        />
        <Tooltip content={<TipBox fmt={(n) => `$${n.toLocaleString()}`} />} />
        <Area
          type="monotone"
          dataKey="revenue"
          stroke={ACCENT}
          strokeWidth={2.5}
          fill="url(#rev-g)"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function PacingLines({
  data,
  height = 260,
}: {
  data: { date: string; actual: number; target: number }[];
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
        <CartesianGrid stroke="#f1f5f9" vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={fmtDate}
          tick={{ fontSize: 11, fill: FAINT }}
          axisLine={false}
          tickLine={false}
          minTickGap={28}
        />
        <YAxis
          tickFormatter={(v) => moneyK(v)}
          tick={{ fontSize: 11, fill: FAINT }}
          axisLine={false}
          tickLine={false}
          width={48}
        />
        <Tooltip content={<TipBox fmt={(n) => `$${n.toLocaleString()}`} />} />
        <Line
          type="monotone"
          dataKey="target"
          stroke={FAINT}
          strokeWidth={2}
          strokeDasharray="4 4"
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="actual"
          stroke={GOOD}
          strokeWidth={2.5}
          dot={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function HBars({
  data,
  height = 280,
  color = ACCENT,
  fmt = (n: number) => n.toFixed(3),
}: {
  data: { name: string; value: number }[];
  height?: number;
  color?: string;
  fmt?: (n: number) => string;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 0, right: 16, bottom: 0, left: 8 }}
      >
        <CartesianGrid stroke="#f1f5f9" horizontal={false} />
        <XAxis
          type="number"
          tickFormatter={fmt}
          tick={{ fontSize: 11, fill: FAINT }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          type="category"
          dataKey="name"
          tick={{ fontSize: 11, fill: "#334155" }}
          axisLine={false}
          tickLine={false}
          width={120}
        />
        <Tooltip
          cursor={{ fill: "#f8fafc" }}
          content={<TipBox fmt={fmt} />}
        />
        <Bar dataKey="value" fill={color} radius={[0, 4, 4, 0]} barSize={14} />
      </BarChart>
    </ResponsiveContainer>
  );
}
