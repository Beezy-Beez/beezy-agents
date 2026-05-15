"use client";

import useSWR from "swr";
import { fetcher, apiPost } from "@/lib/api";
import type { FlowsData } from "@/lib/types";
import { useState } from "react";

const SEV_CONFIG = {
  ok:       { label: "HEALTHY",  bg: "#1e7e34" },
  warn:     { label: "WARNING",  bg: "#e07b00" },
  critical: { label: "CRITICAL", bg: "#c0392b" },
};

export default function FlowsPage() {
  const { data, error, mutate } = useSWR<FlowsData>("/api/data/flows", fetcher, {
    refreshInterval: 30_000,
  });
  const [running, setRunning] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const handleRun = async () => {
    setRunning(true);
    setMsg(null);
    try {
      await apiPost("/api/run-flow-check");
      setMsg("Flow check started. This takes ~30 seconds. Refresh in a moment.");
      setTimeout(() => mutate(), 35_000);
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : "Error");
    } finally {
      setRunning(false);
    }
  };

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-red-700">
        Failed to load flow data: {error.message}
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

  const analyses = data.analyses ?? [];
  const checkedAt = data._checked_at ?? "";

  const healthCount = analyses.filter((a) => a.severity === "ok").length;
  const warnCount = analyses.filter((a) => a.severity === "warn").length;
  const critCount = analyses.filter((a) => a.severity === "critical").length;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1
          className="text-2xl font-bold text-[#8b4513]"
          style={{ fontFamily: "var(--font-dm-serif)" }}
        >
          Flows
        </h1>
        <div className="flex items-center gap-3">
          {checkedAt && (
            <span className="text-xs text-gray-400">Last checked: {checkedAt}</span>
          )}
          <button
            onClick={handleRun}
            disabled={running}
            className="px-4 py-2 bg-[#8b4513] text-white rounded-lg text-sm font-semibold hover:bg-[#6d3410] transition-colors disabled:opacity-60"
          >
            {running ? "Running…" : "Run Flow Check"}
          </button>
        </div>
      </div>

      {msg && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 text-sm text-blue-700">
          {msg}
        </div>
      )}

      {/* Summary pills */}
      {analyses.length > 0 && (
        <div className="flex gap-3 flex-wrap">
          <div className="flex items-center gap-2 bg-green-50 border border-green-200 rounded-lg px-4 py-2">
            <span className="w-2 h-2 rounded-full bg-[#1e7e34]" />
            <span className="text-sm font-semibold text-[#1e7e34]">{healthCount} Healthy</span>
          </div>
          <div className="flex items-center gap-2 bg-orange-50 border border-orange-200 rounded-lg px-4 py-2">
            <span className="w-2 h-2 rounded-full bg-[#e07b00]" />
            <span className="text-sm font-semibold text-[#e07b00]">{warnCount} Warning</span>
          </div>
          <div className="flex items-center gap-2 bg-red-50 border border-red-200 rounded-lg px-4 py-2">
            <span className="w-2 h-2 rounded-full bg-[#c0392b]" />
            <span className="text-sm font-semibold text-[#c0392b]">{critCount} Critical</span>
          </div>
        </div>
      )}

      {/* Flow table */}
      <div className="bg-white rounded-xl border border-[#e8dcc8] shadow-sm overflow-hidden">
        <div className="px-5 py-4 border-b border-[#e8dcc8]">
          <h2 className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355]">
            Flow Health
          </h2>
        </div>

        {analyses.length === 0 ? (
          <div className="p-8 text-center">
            <p className="text-gray-400 italic text-sm mb-2">
              No flow data yet — runs weekly Sunday 9:15pm ET.
            </p>
            <p className="text-xs text-gray-400 mb-4">
              Or click the button above to run now (pulls 30-day Klaviyo flow metrics).
            </p>
            <button
              onClick={handleRun}
              disabled={running}
              className="px-5 py-2.5 bg-[#8b4513] text-white rounded-lg text-sm font-semibold hover:bg-[#6d3410] transition-colors disabled:opacity-60"
            >
              {running ? "Running…" : "Run Flow Health Check Now"}
            </button>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b-2 border-[#e8dcc8]">
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-5 py-3">
                    Flow
                  </th>
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3 whitespace-nowrap">
                    30d Revenue
                  </th>
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                    RPR
                  </th>
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                    Status
                  </th>
                </tr>
              </thead>
              <tbody>
                {analyses.map((flow, i) => {
                  const sev = SEV_CONFIG[flow.severity] ?? {
                    label: flow.severity.toUpperCase(),
                    bg: "#888",
                  };
                  return (
                    <tr
                      key={i}
                      className="border-b border-[#f0ece4] last:border-0 hover:bg-[#fdf8f2] transition-colors"
                    >
                      <td className="px-5 py-3 font-medium text-sm">
                        {flow.name.length > 30
                          ? flow.name.slice(0, 30) + "…"
                          : flow.name}
                      </td>
                      <td className="px-3 py-3 text-sm font-semibold">
                        ${Number(flow.revenue).toLocaleString("en-US", {
                          maximumFractionDigits: 0,
                        })}
                      </td>
                      <td className="px-3 py-3 text-sm">
                        ${Number(flow.rpr).toFixed(2)}
                      </td>
                      <td className="px-3 py-3">
                        <div className="flex items-center gap-2">
                          <span
                            className="inline-block px-2 py-0.5 rounded-full text-white text-[11px] font-semibold"
                            style={{ background: sev.bg }}
                          >
                            {sev.label}
                          </span>
                          {flow.fix_queued && (
                            <span className="inline-block px-2 py-0.5 rounded-full bg-[#7b2d8b] text-white text-[11px] font-semibold">
                              Fix queued
                            </span>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Flow benchmark reference */}
      <div className="bg-white rounded-xl border border-[#e8dcc8] shadow-sm p-5">
        <div className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] mb-3">
          Flow RPR Benchmarks
        </div>
        <div className="overflow-x-auto">
          <table className="text-sm border-collapse">
            <thead>
              <tr className="border-b border-[#e8dcc8]">
                <th className="text-left text-[11px] font-semibold uppercase text-[#8b7355] pr-8 py-2">
                  Flow Type
                </th>
                <th className="text-left text-[11px] font-semibold uppercase text-[#8b7355] pr-8 py-2">
                  Min RPR
                </th>
                <th className="text-left text-[11px] font-semibold uppercase text-[#8b7355] py-2">
                  Min Open Rate
                </th>
              </tr>
            </thead>
            <tbody>
              {[
                ["welcome",            "$1.50", "40%"],
                ["abandoned_checkout", "$2.00", "35%"],
                ["abandoned_cart",     "$1.00", "35%"],
                ["browse_abandonment", "$0.50", "30%"],
                ["replenishment",      "$0.50", "30%"],
                ["winback",            "$0.20", "25%"],
                ["post_purchase",      "$0.50", "30%"],
                ["membership",         "$0.50", "25%"],
              ].map(([type, rpr, open]) => (
                <tr key={type} className="border-b border-[#f0ece4] last:border-0">
                  <td className="pr-8 py-1.5 font-mono text-xs text-[#2c2417]">{type}</td>
                  <td className="pr-8 py-1.5 text-sm font-semibold">{rpr}</td>
                  <td className="py-1.5 text-sm text-gray-500">{open}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
