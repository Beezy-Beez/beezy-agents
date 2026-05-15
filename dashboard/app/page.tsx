"use client";

import useSWR from "swr";
import { fetcher, apiPost } from "@/lib/api";
import type { OverviewData, TodaySlot } from "@/lib/types";
import RevenueGauge from "@/components/RevenueGauge";
import StatCard from "@/components/StatCard";
import Badge from "@/components/Badge";
import { useState } from "react";

function fmt$(n: number) {
  return "$" + n.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function SkeletonBlock({ h = "h-4", w = "w-full" }: { h?: string; w?: string }) {
  return <div className={`skeleton rounded ${h} ${w}`} />;
}

function ActionButton({
  label,
  endpoint,
  body,
  variant = "primary",
  onSuccess,
}: {
  label: string;
  endpoint: string;
  body?: Record<string, unknown>;
  variant?: "primary" | "secondary" | "danger" | "warning";
  onSuccess?: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const variantClass = {
    primary: "bg-[#8b4513] hover:bg-[#6d3410] text-white",
    secondary: "bg-[#555] hover:bg-[#444] text-white",
    danger: "bg-[#c0392b] hover:bg-[#a93226] text-white",
    warning: "bg-[#e07b00] hover:bg-[#c56f00] text-white",
  }[variant];

  const handle = async () => {
    setLoading(true);
    setMsg(null);
    try {
      await apiPost(endpoint, body);
      setMsg("Done!");
      onSuccess?.();
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : "Error");
    } finally {
      setLoading(false);
      setTimeout(() => setMsg(null), 4000);
    }
  };

  return (
    <div className="flex flex-col items-start gap-1">
      <button
        onClick={handle}
        disabled={loading}
        className={`${variantClass} px-4 py-2 rounded-lg text-sm font-semibold transition-colors disabled:opacity-60 disabled:cursor-not-allowed`}
      >
        {loading ? "Working…" : label}
      </button>
      {msg && (
        <span className="text-xs text-[#8b7355]">{msg}</span>
      )}
    </div>
  );
}

function TodaySlotRow({ slot, onRetry }: { slot: TodaySlot; onRetry: () => void }) {
  return (
    <div className="flex items-center gap-3 py-2.5 border-b border-[#f0ece4] last:border-0">
      <div className="flex-shrink-0 w-28">
        <Badge contentType={slot.t} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="font-semibold text-sm text-[#2c2417]">{slot.a}</div>
        <div className="text-xs text-gray-400 truncate">{slot.tp}</div>
      </div>
      <div className="flex-shrink-0 flex items-center gap-2">
        <Badge status={slot.s} />
        {slot.rv > 0 && (
          <span className="text-sm font-semibold text-[#8b4513]">{fmt$(slot.rv)}</span>
        )}
        {(slot.s === "failed" || slot.s === "blocked") && slot.id && (
          <button
            onClick={() =>
              apiPost(`/api/retry-slot?id=${slot.id}`).then(onRetry)
            }
            className="text-xs border border-[#c0392b] text-[#c0392b] px-2 py-0.5 rounded hover:bg-[#c0392b] hover:text-white transition-colors"
          >
            Retry
          </button>
        )}
        {slot.kid && (
          <a
            href={`https://www.klaviyo.com/campaign/${slot.kid}/edit`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-gray-400 hover:text-[#8b4513]"
          >
            Klaviyo ↗
          </a>
        )}
      </div>
    </div>
  );
}

export default function OverviewPage() {
  const { data, error, mutate } = useSWR<OverviewData>("/api/data/overview", fetcher, {
    refreshInterval: 30_000,
  });

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-red-700">
        Failed to load overview data: {error.message}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-48 w-full rounded-xl" />
        <div className="grid grid-cols-4 gap-4">
          {[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-24 rounded-xl" />)}
        </div>
        <div className="skeleton h-64 w-full rounded-xl" />
      </div>
    );
  }

  const { pacing: p, today_slots, next_send, approval: apv } = data;

  const statusColor =
    p.status === "AHEAD" || p.status === "ON TRACK" ? "#1e7e34" : "#c0392b";

  const linearExpected = p.goal * (p.days_elapsed / (p.days_elapsed + p.days_left));
  const behindBy =
    p.status === "BEHIND" ? Math.max(0, linearExpected - p.rev) : 0;

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold text-[#8b4513]" style={{ fontFamily: "var(--font-dm-serif)" }}>
        Overview
      </h1>

      {/* Revenue command center */}
      <div className="bg-white rounded-xl border border-[#e8dcc8] p-6 shadow-sm">
        <div className="flex flex-wrap gap-6 items-center">
          {/* Gauge */}
          <RevenueGauge pct={p.pct} status={p.status} rev={p.rev} goal={p.goal} />

          {/* Main stats */}
          <div className="flex-1 min-w-[220px]">
            <div>
              <div className="text-4xl font-bold text-[#2c2417] leading-none">
                {fmt$(p.rev)}
              </div>
              <div className="text-xs text-gray-400 mt-1">
                Revenue MTD
                {p.as_of ? ` · as of ${p.as_of.slice(0, 16)}` : ""}
                {p.stale && (
                  <span className="ml-1 text-[#e07b00]"> ⚠ stale</span>
                )}
              </div>
              <span
                className="inline-block mt-2 px-3 py-0.5 rounded-full text-white text-xs font-bold uppercase tracking-wide"
                style={{ background: statusColor }}
              >
                {p.status}
              </span>
            </div>

            <div className="grid grid-cols-2 gap-3 mt-4">
              <div className="bg-[#faf6ee] rounded-lg px-3 py-2.5">
                <div className="font-semibold text-sm">
                  {fmt$(p.cr)} / {fmt$(p.fr)}
                </div>
                <div className="text-xs text-gray-400 mt-0.5">Campaigns / Flows</div>
              </div>
              <div className="bg-[#faf6ee] rounded-lg px-3 py-2.5">
                <div
                  className={`font-semibold text-sm ${
                    p.forecast >= p.goal ? "text-[#1e7e34]" : "text-[#c0392b]"
                  }`}
                >
                  {fmt$(p.forecast)}
                </div>
                <div className="text-xs text-gray-400 mt-0.5">Forecast month-end</div>
              </div>
              <div className="bg-[#faf6ee] rounded-lg px-3 py-2.5">
                <div className="font-semibold text-sm">{fmt$(p.daily_needed)}/day</div>
                <div className="text-xs text-gray-400 mt-0.5">Needed to hit goal</div>
              </div>
              <div className="bg-[#faf6ee] rounded-lg px-3 py-2.5">
                <div className="font-semibold text-sm">
                  {p.days_left}d left · {p.days_elapsed}d elapsed
                </div>
                <div className="text-xs text-gray-400 mt-0.5">Month progress</div>
              </div>
            </div>
          </div>
        </div>

        {/* Behind warning */}
        {p.status === "BEHIND" && behindBy > 0 && (
          <div className="mt-4 flex items-center gap-4 flex-wrap bg-red-50 border border-red-200 rounded-lg px-4 py-3">
            <span className="text-sm font-medium text-[#c0392b]">
              Behind pace by {fmt$(behindBy)}
            </span>
            <ActionButton
              label="Boost Revenue Now"
              endpoint="/api/boost"
              variant="danger"
              onSuccess={() => mutate()}
            />
          </div>
        )}

        {/* No data state */}
        {p.rev === 0 && !p.as_of && (
          <div className="mt-4 border-t border-[#e8dcc8] pt-4">
            <p className="text-sm text-gray-400 mb-3">
              Revenue cache not loaded yet. Refreshes automatically at 7:35am ET.
            </p>
            <ActionButton
              label="Load Revenue Data"
              endpoint="/api/refresh-pacing"
              onSuccess={() => mutate()}
            />
          </div>
        )}
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="MTD Revenue"
          value={fmt$(p.rev)}
          sub={`${p.pct.toFixed(0)}% of ${fmt$(p.goal)} goal`}
        />
        <StatCard
          label="Campaign Revenue"
          value={fmt$(p.cr)}
          sub={`${p.cc} campaigns sent`}
        />
        <StatCard
          label="Flow Revenue"
          value={fmt$(p.fr)}
          sub="Klaviyo attributed"
        />
        <StatCard
          label="Forecast"
          value={fmt$(p.forecast)}
          sub="At current daily pace"
          valueClassName={p.forecast >= p.goal ? "text-[#1e7e34]" : "text-[#c0392b]"}
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* Approval center */}
        <div className="bg-white rounded-xl border border-[#e8dcc8] p-5 shadow-sm">
          <div className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] mb-4">
            Approval Center
          </div>

          {apv.week_approved ? (
            <div className="flex items-start gap-3 bg-green-50 border border-green-200 rounded-lg p-3 mb-4">
              <span className="text-green-600 text-lg mt-0.5">✓</span>
              <div>
                <div className="font-semibold text-sm text-[#2c2417]">
                  This week — APPROVED
                </div>
                <div className="text-xs text-gray-500 mt-0.5">
                  Campaigns will run automatically each morning.
                </div>
              </div>
            </div>
          ) : (
            <div className="flex items-start gap-3 bg-yellow-50 border border-yellow-200 rounded-lg p-3 mb-4">
              <span className="text-yellow-600 text-lg mt-0.5">⚠</span>
              <div>
                <div className="font-semibold text-sm text-[#2c2417]">
                  This week — PENDING APPROVAL
                </div>
                <div className="text-xs text-gray-500 mt-0.5">
                  {apv.upcoming_count} slots queued · est.{" "}
                  {fmt$(apv.total_estimated_rev)} revenue
                </div>
              </div>
            </div>
          )}

          {!apv.week_approved && (
            <button
              onClick={() =>
                apiPost("/api/approve-week").then(() => mutate())
              }
              className="w-full py-3 bg-[#8b4513] hover:bg-[#6d3410] text-white rounded-lg font-bold text-sm uppercase tracking-wide transition-colors mb-2"
            >
              Approve This Week
            </button>
          )}

          <div className="text-xs text-gray-400 mt-2">
            {apv.month_has_plan
              ? "✓ Calendar plan exists for this month"
              : "⚠ No calendar plan"}
          </div>

          {apv.month_has_plan && (
            <button
              onClick={() =>
                apiPost("/api/approve-month").then(() => mutate())
              }
              className="mt-3 w-full py-2 bg-[#555] hover:bg-[#444] text-white rounded-lg font-semibold text-xs uppercase tracking-wide transition-colors"
            >
              Approve All Weeks This Month
            </button>
          )}
        </div>

        {/* Today's agenda */}
        <div className="bg-white rounded-xl border border-[#e8dcc8] p-5 shadow-sm">
          <div className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] mb-4">
            Today&apos;s Agenda
          </div>

          {today_slots.length === 0 ? (
            <div className="text-sm text-gray-400 italic py-4">
              Rest day — next send:{" "}
              <strong className="text-[#2c2417]">{next_send || "soon"}</strong>
            </div>
          ) : (
            <div>
              {today_slots.map((slot, i) => (
                <TodaySlotRow key={i} slot={slot} onRetry={() => mutate()} />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Quick actions */}
      <div className="bg-white rounded-xl border border-[#e8dcc8] p-5 shadow-sm">
        <div className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] mb-4">
          Quick Actions
        </div>
        <div className="flex flex-wrap gap-3">
          <ActionButton
            label="Deploy Campaigns"
            endpoint="/api/run-orchestrator"
            onSuccess={() => mutate()}
          />
          <ActionButton
            label="Refresh Revenue"
            endpoint="/api/refresh-pacing"
            onSuccess={() => mutate()}
            variant="secondary"
          />
          <ActionButton
            label="Approve This Week"
            endpoint="/api/approve-week"
            onSuccess={() => mutate()}
            variant="secondary"
          />
          <ActionButton
            label="Run Ingestion"
            endpoint="/api/run-ingestion"
            variant="secondary"
          />
          <ActionButton
            label="Approve All Weeks"
            endpoint="/api/approve-month"
            onSuccess={() => mutate()}
            variant="secondary"
          />
        </div>
      </div>
    </div>
  );
}
