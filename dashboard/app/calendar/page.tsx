"use client";

import useSWR from "swr";
import { fetcher, apiPost } from "@/lib/api";
import type { CalendarData, CalendarSlot, ContentType } from "@/lib/types";
import Badge from "@/components/Badge";
import { useState } from "react";

function fmt$(n: number) {
  if (!n) return "—";
  return "$" + n.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function fmt12h(tm: string) {
  if (!tm) return "—";
  const [hh, mm] = tm.split(":");
  const h = parseInt(hh, 10);
  const suffix = h >= 12 ? "pm" : "am";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${mm} ${suffix}`;
}

const CT_BORDER: Record<string, string> = {
  klaviyo_campaign: "#1a73e8",
  sniper_followup:  "#1558b0",
  hive_mind:        "#7b2d8b",
  seo_blog:         "#1e7e34",
  sleep_audio:      "#0e7c7b",
  sms_campaign:     "#e07b00",
  flow_experiment:  "#888",
  not_sent:         "#ddd",
};

const FILTERS: Array<{ label: string; types: ContentType[] | null }> = [
  { label: "All", types: null },
  { label: "Email", types: ["klaviyo_campaign", "sniper_followup"] },
  { label: "Hive Mind", types: ["hive_mind"] },
  { label: "SMS", types: ["sms_campaign"] },
  { label: "SEO", types: ["seo_blog"] },
  { label: "Sleep Audio", types: ["sleep_audio"] },
];

export default function CalendarPage() {
  const { data, error, mutate } = useSWR<CalendarData>("/api/data/calendar", fetcher, {
    refreshInterval: 30_000,
  });
  const [filter, setFilter] = useState<string>("All");

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-red-700">
        Failed to load calendar: {error.message}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-10 w-64 rounded-xl" />
        <div className="skeleton h-[600px] w-full rounded-xl" />
      </div>
    );
  }

  const today = new Date().toISOString().slice(0, 10);
  const { slots, approval: apv } = data;

  const activeFilter = FILTERS.find((f) => f.label === filter)!;
  const filtered = activeFilter.types
    ? slots.filter((s) => (activeFilter.types as string[]).includes(s.t))
    : slots;

  const totalEst = slots
    .filter((s) => s.t !== "seo_blog" && s.t !== "flow_experiment")
    .reduce((sum, s) => sum + (s.rv || 0), 0);
  const totalActual = slots.reduce((sum, s) => sum + (s.actual_rev || 0), 0);

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1
          className="text-2xl font-bold text-[#8b4513]"
          style={{ fontFamily: "var(--font-dm-serif)" }}
        >
          Calendar
        </h1>
        <div className="flex items-center gap-3 text-sm text-gray-500">
          <span>Est: {fmt$(totalEst)}</span>
          {totalActual > 0 && <span>Actual: {fmt$(totalActual)}</span>}
        </div>
      </div>

      {/* Approval banner */}
      {!apv.week_approved && (
        <div className="flex items-center gap-4 bg-yellow-50 border border-yellow-200 rounded-xl px-5 py-3">
          <span className="text-yellow-700 font-semibold text-sm">
            ⚠ This week not yet approved
          </span>
          <button
            onClick={() => apiPost("/api/approve-week").then(() => mutate())}
            className="px-4 py-1.5 bg-[#8b4513] text-white rounded-lg text-sm font-semibold hover:bg-[#6d3410] transition-colors"
          >
            Approve Week
          </button>
        </div>
      )}

      {/* Filter pills */}
      <div className="flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.label}
            onClick={() => setFilter(f.label)}
            className={`px-4 py-1.5 rounded-full text-sm font-medium transition-colors ${
              filter === f.label
                ? "bg-[#8b4513] text-white"
                : "bg-white border border-[#e8dcc8] text-[#2c2417] hover:border-[#8b4513]"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Calendar table */}
      <div className="bg-white rounded-xl border border-[#e8dcc8] shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b-2 border-[#e8dcc8]">
                <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-4 py-3 whitespace-nowrap">
                  Date
                </th>
                <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                  Type
                </th>
                <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                  Audience
                </th>
                <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                  Topic
                </th>
                <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3 whitespace-nowrap">
                  Time
                </th>
                <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3 whitespace-nowrap">
                  Est. Rev
                </th>
                <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3 whitespace-nowrap">
                  Actual
                </th>
                <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                  Status
                </th>
                <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-4 py-8 text-center text-gray-400 italic">
                    No slots found.
                  </td>
                </tr>
              ) : (
                (() => {
                  let lastDate = "";
                  return filtered.map((slot, i) => {
                    const isToday = slot.date === today;
                    const isPast = slot.date < today;
                    const showDate = slot.date !== lastDate;
                    if (showDate) lastDate = slot.date;

                    const borderColor =
                      slot.status === "not_sent"
                        ? "#ddd"
                        : CT_BORDER[slot.t] || "#555";

                    let dateLabel = "";
                    if (showDate) {
                      try {
                        const d = new Date(slot.date + "T12:00:00");
                        dateLabel = d.toLocaleDateString("en-US", {
                          weekday: "short",
                          month: "short",
                          day: "numeric",
                        });
                      } catch {
                        dateLabel = slot.date;
                      }
                    }

                    // Actual revenue
                    let actualNode: React.ReactNode = "—";
                    if (slot.actual_rev && slot.actual_rev > 0) {
                      const diff = slot.rv > 0
                        ? ((slot.actual_rev - slot.rv) / slot.rv) * 100
                        : 0;
                      const diffColor = slot.actual_rev >= slot.rv ? "#1e7e34" : "#c0392b";
                      actualNode = (
                        <span>
                          {fmt$(slot.actual_rev)}{" "}
                          {slot.rv > 0 && (
                            <span className="text-[11px]" style={{ color: diffColor }}>
                              ({diff > 0 ? "+" : ""}
                              {diff.toFixed(0)}%)
                            </span>
                          )}
                        </span>
                      );
                    } else if (
                      (slot.status === "dispatched" || slot.status === "completed") &&
                      slot.date <= today
                    ) {
                      actualNode = (
                        <span className="text-[11px] text-gray-400">pending backfill</span>
                      );
                    }

                    return (
                      <tr
                        key={i}
                        style={{ borderLeft: `3px solid ${borderColor}` }}
                        className={`border-b border-[#f0ece4] last:border-0 transition-colors hover:bg-[#fdf8f2] ${
                          isToday ? "bg-[#fffef8]" : isPast ? "opacity-70" : ""
                        }`}
                      >
                        <td className="px-4 py-2.5 whitespace-nowrap align-middle">
                          {showDate ? (
                            <div className="flex items-center gap-1.5">
                              <strong className="text-sm">{dateLabel}</strong>
                              {isToday && (
                                <span className="px-1.5 py-0.5 bg-[#d4a847] text-white text-[10px] font-bold rounded">
                                  TODAY
                                </span>
                              )}
                            </div>
                          ) : null}
                        </td>
                        <td className="px-3 py-2.5 align-middle">
                          <Badge contentType={slot.t} />
                        </td>
                        <td className="px-3 py-2.5 align-middle text-sm font-medium">
                          {slot.a}
                        </td>
                        <td className="px-3 py-2.5 align-middle max-w-[200px]">
                          <span className="block truncate text-xs text-gray-600">
                            {slot.tp}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 align-middle text-sm whitespace-nowrap">
                          {fmt12h(slot.tm)} ET
                        </td>
                        <td className="px-3 py-2.5 align-middle text-sm">
                          {fmt$(slot.rv)}
                        </td>
                        <td className="px-3 py-2.5 align-middle text-sm">
                          {actualNode}
                        </td>
                        <td className="px-3 py-2.5 align-middle">
                          <Badge status={slot.status} />
                        </td>
                        <td className="px-3 py-2.5 align-middle">
                          <div className="flex items-center gap-1.5">
                            {(slot.status === "failed" || slot.status === "blocked") &&
                              slot.exec_id && (
                                <button
                                  onClick={() =>
                                    apiPost(`/api/retry-slot?id=${slot.exec_id}`).then(
                                      () => mutate()
                                    )
                                  }
                                  className="text-[11px] border border-[#c0392b] text-[#c0392b] px-1.5 py-0.5 rounded hover:bg-[#c0392b] hover:text-white transition-colors"
                                >
                                  ↺ Retry
                                </button>
                              )}
                            {slot.kid && (
                              <a
                                href={`https://www.klaviyo.com/campaign/${slot.kid}/edit`}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-[11px] text-gray-400 hover:text-[#8b4513]"
                              >
                                ↗
                              </a>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  });
                })()
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
