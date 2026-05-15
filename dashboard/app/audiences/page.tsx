"use client";

import useSWR from "swr";
import { fetcher, apiPost } from "@/lib/api";
import type { AudiencesData, AudienceHealth } from "@/lib/types";
import { useState } from "react";

const HEALTH_CONFIG = {
  FRESH:  { label: "FRESH",    bg: "#1e7e34", text: "No recent send" },
  WARM:   { label: "WARM",     bg: "#e07b00", text: "Sent 7–14d ago" },
  RECENT: { label: "COOLDOWN", bg: "#c0392b", text: "Sent < 7d ago" },
};

function RPRTrend({ rpr30, rpr90 }: { rpr30: number; rpr90: number }) {
  if (!rpr30 || !rpr90) return null;
  if (rpr30 > rpr90 * 1.05) return <span className="text-[#1e7e34] font-bold">↑</span>;
  if (rpr30 < rpr90 * 0.9) return <span className="text-[#c0392b] font-bold">↓</span>;
  return <span className="text-gray-400">→</span>;
}

function HealthRow({ row, onRefresh }: { row: AudienceHealth; onRefresh: () => void }) {
  const h = HEALTH_CONFIG[row.health] ?? { label: row.health, bg: "#888", text: "" };
  return (
    <tr className="border-b border-[#f0ece4] last:border-0 hover:bg-[#fdf8f2] transition-colors">
      <td className="px-4 py-3 font-semibold text-sm">{row.audience}</td>
      <td className="px-3 py-3 text-sm text-gray-600">{row.last_send}</td>
      <td className="px-3 py-3 text-sm text-gray-500">{row.days_since}d ago</td>
      <td className="px-3 py-3 text-sm">
        <span className="font-semibold">${row.rpr_90d.toFixed(3)}</span>{" "}
        <RPRTrend rpr30={row.rpr_30d} rpr90={row.rpr_90d} />
      </td>
      <td className="px-3 py-3 text-sm text-center">{row.sends_90d}</td>
      <td className="px-3 py-3">
        <span
          className="inline-block px-2 py-0.5 rounded-full text-white text-[11px] font-semibold"
          style={{ background: h.bg }}
          title={h.text}
        >
          {h.label}
        </span>
      </td>
    </tr>
  );
}

export default function AudiencesPage() {
  const { data, error, mutate } = useSWR<AudiencesData>("/api/data/audiences", fetcher, {
    refreshInterval: 30_000,
  });
  const [burnInput, setBurnInput] = useState("");
  const [burnMsg, setBurnMsg] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-red-700">
        Failed to load audience data: {error.message}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-10 w-64 rounded-xl" />
        <div className="skeleton h-[400px] w-full rounded-xl" />
      </div>
    );
  }

  const { health, burn_list } = data;

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await apiPost("/api/refresh-audience-health");
      await mutate();
    } finally {
      setRefreshing(false);
    }
  };

  const handleBurn = async () => {
    const aud = burnInput.trim();
    if (!aud) return;
    try {
      await apiPost(`/api/burn-audience?audience=${encodeURIComponent(aud)}`);
      setBurnMsg(`Burned: ${aud}`);
      setBurnInput("");
      mutate();
    } catch (e: unknown) {
      setBurnMsg(e instanceof Error ? e.message : "Error");
    }
    setTimeout(() => setBurnMsg(null), 4000);
  };

  const handleUnburn = async (aud: string) => {
    await apiPost(`/api/unburn-audience?audience=${encodeURIComponent(aud)}`);
    mutate();
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1
          className="text-2xl font-bold text-[#8b4513]"
          style={{ fontFamily: "var(--font-dm-serif)" }}
        >
          Audiences
        </h1>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="px-4 py-2 bg-white border border-[#e8dcc8] text-[#2c2417] rounded-lg text-sm font-medium hover:border-[#8b4513] transition-colors disabled:opacity-60"
        >
          {refreshing ? "Pulling from Klaviyo…" : "Refresh from Klaviyo"}
        </button>
      </div>

      {/* Audience health */}
      <div className="bg-white rounded-xl border border-[#e8dcc8] shadow-sm overflow-hidden">
        <div className="px-5 py-4 border-b border-[#e8dcc8]">
          <h2 className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355]">
            Audience Health
          </h2>
        </div>

        {health.length === 0 ? (
          <div className="p-8 text-center">
            <p className="text-gray-400 italic text-sm mb-4">
              No audience data cached yet.
            </p>
            <p className="text-xs text-gray-400 mb-4">
              Loads from Klaviyo history — 90-day RPR per audience.
            </p>
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="px-5 py-2.5 bg-[#8b4513] text-white rounded-lg text-sm font-semibold hover:bg-[#6d3410] transition-colors disabled:opacity-60"
            >
              {refreshing ? "Loading…" : "Load Audience History from Klaviyo"}
            </button>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b-2 border-[#e8dcc8]">
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-4 py-3">
                    Audience
                  </th>
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3 whitespace-nowrap">
                    Last Send
                  </th>
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                    Since
                  </th>
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3 whitespace-nowrap">
                    90d RPR
                  </th>
                  <th className="text-center text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3 whitespace-nowrap">
                    Sends (90d)
                  </th>
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                    Status
                  </th>
                </tr>
              </thead>
              <tbody>
                {health.map((row, i) => (
                  <HealthRow key={i} row={row} onRefresh={() => mutate()} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Burn list */}
      <div className="bg-white rounded-xl border border-[#e8dcc8] shadow-sm p-5">
        <div className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] mb-4">
          Burn List
        </div>

        {burn_list.length === 0 ? (
          <p className="text-sm text-gray-400 italic mb-4">No burned audiences.</p>
        ) : (
          <div className="flex flex-wrap gap-2 mb-4">
            {burn_list.map((aud) => (
              <div
                key={aud}
                className="flex items-center gap-2 bg-red-50 border border-red-200 rounded-lg px-3 py-1.5"
              >
                <span className="text-sm font-medium text-[#c0392b]">{aud}</span>
                <button
                  onClick={() => handleUnburn(aud)}
                  className="text-xs text-gray-400 hover:text-[#c0392b] font-semibold transition-colors"
                  title="Unburn"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Add to burn list */}
        <div className="flex gap-2 items-center">
          <input
            type="text"
            value={burnInput}
            onChange={(e) => setBurnInput(e.target.value)}
            placeholder="audience_name (e.g. lapsed_30d)"
            onKeyDown={(e) => e.key === "Enter" && handleBurn()}
            className="flex-1 border border-[#e8dcc8] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[#8b4513] bg-white"
          />
          <button
            onClick={handleBurn}
            disabled={!burnInput.trim()}
            className="px-4 py-2 bg-[#c0392b] text-white rounded-lg text-sm font-semibold hover:bg-[#a93226] transition-colors disabled:opacity-40"
          >
            Burn
          </button>
        </div>
        {burnMsg && (
          <p className="text-xs text-[#8b7355] mt-2">{burnMsg}</p>
        )}
      </div>
    </div>
  );
}
