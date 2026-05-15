"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import type { AnalyticsData } from "@/lib/types";
import Badge from "@/components/Badge";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
  Cell,
} from "recharts";

function fmt$(n: number) {
  return "$" + n.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function fmtDate(s: string) {
  try {
    const d = new Date(s + "T12:00:00");
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  } catch {
    return s;
  }
}

const CHART_COLOR = "#8b4513";
const GRID_COLOR = "#e8dcc8";

export default function AnalyticsPage() {
  const { data, error } = useSWR<AnalyticsData>("/api/data/analytics", fetcher, {
    refreshInterval: 30_000,
  });

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-red-700">
        Failed to load analytics: {error.message}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-10 w-64 rounded-xl" />
        <div className="skeleton h-64 w-full rounded-xl" />
        <div className="skeleton h-64 w-full rounded-xl" />
      </div>
    );
  }

  const { top_performers, learning, revenue_trend } = data;
  const rprEntries = Object.entries(learning.rpr_by_audience ?? {})
    .sort(([, a], [, b]) => b - a)
    .slice(0, 12);

  return (
    <div className="space-y-5">
      <h1
        className="text-2xl font-bold text-[#8b4513]"
        style={{ fontFamily: "var(--font-dm-serif)" }}
      >
        Analytics
      </h1>

      {/* 30-day revenue sparkline */}
      <div className="bg-white rounded-xl border border-[#e8dcc8] shadow-sm p-5">
        <div className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] mb-4">
          30-Day Revenue Trend
        </div>
        {revenue_trend.length === 0 ? (
          <div className="flex items-center justify-center h-48 text-gray-400 italic text-sm">
            No trend data yet — ingestion populates this.
          </div>
        ) : (
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart
                data={revenue_trend}
                margin={{ top: 4, right: 16, bottom: 0, left: 16 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
                <XAxis
                  dataKey="date"
                  tickFormatter={fmtDate}
                  tick={{ fontSize: 10, fill: "#8b7355" }}
                  axisLine={{ stroke: GRID_COLOR }}
                  tickLine={false}
                  interval="preserveStartEnd"
                />
                <YAxis
                  tickFormatter={(v) => `$${(v / 1000).toFixed(0)}K`}
                  tick={{ fontSize: 10, fill: "#8b7355" }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  formatter={(value: number) => [fmt$(value), "Revenue"]}
                  labelFormatter={(label) => fmtDate(label as string)}
                  contentStyle={{
                    background: "#fff",
                    border: "1px solid #e8dcc8",
                    borderRadius: "8px",
                    fontSize: "12px",
                  }}
                />
                <Line
                  type="monotone"
                  dataKey="revenue"
                  stroke={CHART_COLOR}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, fill: CHART_COLOR }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* Top performers */}
      <div className="bg-white rounded-xl border border-[#e8dcc8] shadow-sm overflow-hidden">
        <div className="px-5 py-4 border-b border-[#e8dcc8]">
          <h2 className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355]">
            Top 10 Performers
          </h2>
        </div>
        {top_performers.length === 0 ? (
          <div className="p-8 text-center text-gray-400 italic text-sm">
            No finalized campaign data yet. Revenue backfill runs daily at 9am.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b-2 border-[#e8dcc8]">
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-4 py-3">
                    #
                  </th>
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                    Audience
                  </th>
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                    Type
                  </th>
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                    Revenue
                  </th>
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                    RPR
                  </th>
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                    Date
                  </th>
                </tr>
              </thead>
              <tbody>
                {top_performers.map((row, i) => (
                  <tr
                    key={i}
                    className="border-b border-[#f0ece4] last:border-0 hover:bg-[#fdf8f2] transition-colors"
                  >
                    <td className="px-4 py-2.5 text-[#8b7355] font-bold text-xs">
                      #{i + 1}
                    </td>
                    <td className="px-3 py-2.5 font-medium text-sm">{row.a}</td>
                    <td className="px-3 py-2.5">
                      <Badge contentType={row.t} />
                    </td>
                    <td className="px-3 py-2.5 font-semibold text-sm">
                      {fmt$(row.rv)}
                    </td>
                    <td className="px-3 py-2.5 text-sm">${row.rpr.toFixed(3)}</td>
                    <td className="px-3 py-2.5 text-sm text-gray-500">{row.d}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* RPR by audience bar chart */}
      {rprEntries.length > 0 && (
        <div className="bg-white rounded-xl border border-[#e8dcc8] shadow-sm p-5">
          <div className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] mb-4">
            RPR by Audience (Learning Loop)
          </div>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={rprEntries.map(([aud, rpr]) => ({ audience: aud, rpr }))}
                layout="vertical"
                margin={{ top: 0, right: 24, bottom: 0, left: 24 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} horizontal={false} />
                <XAxis
                  type="number"
                  tickFormatter={(v) => `$${v.toFixed(2)}`}
                  tick={{ fontSize: 10, fill: "#8b7355" }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="audience"
                  tick={{ fontSize: 11, fill: "#2c2417" }}
                  axisLine={false}
                  tickLine={false}
                  width={110}
                />
                <Tooltip
                  formatter={(v: number) => [`$${v.toFixed(4)}`, "RPR"]}
                  contentStyle={{
                    background: "#fff",
                    border: "1px solid #e8dcc8",
                    borderRadius: "8px",
                    fontSize: "12px",
                  }}
                />
                <Bar dataKey="rpr" radius={[0, 4, 4, 0]}>
                  {rprEntries.map((_, index) => (
                    <Cell
                      key={`cell-${index}`}
                      fill={index === 0 ? "#d4a847" : CHART_COLOR}
                      opacity={1 - index * 0.06}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Learning loop entries */}
      {learning.entries.length > 0 && (
        <div className="bg-white rounded-xl border border-[#e8dcc8] shadow-sm p-5">
          <div className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] mb-4">
            Learning Loop Activity
          </div>
          <div className="space-y-3">
            {learning.entries.map((entry, i) => (
              <div
                key={i}
                className="flex gap-3 text-sm border-b border-[#f0ece4] last:border-0 pb-3 last:pb-0"
              >
                <span className="text-xs text-gray-400 flex-shrink-0 mt-0.5 w-32">
                  {entry.at}
                </span>
                <span className="text-[#2c2417] text-xs leading-relaxed">{entry.summary}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
