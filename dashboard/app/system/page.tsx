"use client";

import useSWR from "swr";
import { fetcher, apiPost } from "@/lib/api";
import type { SystemData } from "@/lib/types";
import { useState } from "react";

const ENV_LABELS: Record<string, string> = {
  KLAVIYO_API_KEY:       "Klaviyo API Key",
  SHOPIFY_ACCESS_TOKEN:  "Shopify Access Token",
  BEEZY_ANTHROPIC_API_KEY: "Anthropic API Key",
  SLACK_BOT_TOKEN:       "Slack Bot Token",
  DATABASE_URL:          "Database (Neon)",
  HIGGSFIELD_KEY:        "Higgsfield Key",
};

const CRON_SCHEDULE = [
  { job: "Shopify + Klaviyo ingestion sync", time: "Every 4h (0,4,8,12,16,20)", sentinel: "cron_ingestion" },
  { job: "Pacing brain snapshot → Slack",    time: "7:30am daily",              sentinel: "cron_pacing_brain" },
  { job: "Pacing cache refresh (Klaviyo MTD)", time: "7:35am daily",            sentinel: "cron_pacing_cache" },
  { job: "Orchestrator — dispatch slots",    time: "8:00am daily",              sentinel: "cron_orchestrator" },
  { job: "Revenue backfill (72h window)",    time: "9:00am daily",              sentinel: "cron_backfill" },
  { job: "Hive Mind campaign auto-create",   time: "10:00am daily",             sentinel: "cron_hive_mind" },
  { job: "Deliverability check",            time: "10:30am daily",             sentinel: "cron_deliverability" },
  { job: "Weekly learning review",           time: "9pm Sunday",                sentinel: "cron_weekly" },
  { job: "Weekly approval brief",            time: "9pm Sunday",                sentinel: "cron_weekly_brief" },
  { job: "Flow health check",                time: "9:15pm Sunday",             sentinel: "cron_flow_check" },
  { job: "Mid-month pacing check",           time: "9:30am 15th",              sentinel: "cron_biweekly" },
  { job: "Monthly retrospective",            time: "9:30am 1st",               sentinel: "cron_monthly" },
  { job: "Calendar generation",              time: "9am 7 days before month-end", sentinel: "cron_calendar" },
];

const STATUS_COLORS: Record<string, string> = {
  completed:  "#1e7e34",
  dispatched: "#1a73e8",
  failed:     "#c0392b",
  error:      "#c0392b",
  started:    "#e07b00",
  pending:    "#888",
};

function ActionTrigger({
  label,
  endpoint,
  variant = "secondary",
}: {
  label: string;
  endpoint: string;
  variant?: "primary" | "secondary" | "warning";
}) {
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const variantClass = {
    primary:   "bg-[#8b4513] hover:bg-[#6d3410] text-white",
    secondary: "bg-white border border-[#e8dcc8] text-[#2c2417] hover:border-[#8b4513]",
    warning:   "bg-[#e07b00] hover:bg-[#c56f00] text-white",
  }[variant];

  const handle = async () => {
    setLoading(true);
    setMsg(null);
    try {
      const res = await apiPost(endpoint);
      setMsg(res.status ? `Started: ${res.status}` : "Started");
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : "Error");
    } finally {
      setLoading(false);
      setTimeout(() => setMsg(null), 5000);
    }
  };

  return (
    <div className="flex flex-col gap-1">
      <button
        onClick={handle}
        disabled={loading}
        className={`${variantClass} px-4 py-2.5 rounded-lg text-sm font-semibold transition-colors disabled:opacity-60 disabled:cursor-not-allowed`}
      >
        {loading ? "Running…" : label}
      </button>
      {msg && <span className="text-xs text-[#8b7355]">{msg}</span>}
    </div>
  );
}

export default function SystemPage() {
  const { data, error, mutate } = useSWR<SystemData>("/api/data/system", fetcher, {
    refreshInterval: 30_000,
  });
  const [slackMsg, setSlackMsg] = useState("");
  const [slackResult, setSlackResult] = useState<string | null>(null);
  const [slackLoading, setSlackLoading] = useState(false);

  const handleSlack = async () => {
    const msg = slackMsg.trim();
    if (!msg) return;
    setSlackLoading(true);
    setSlackResult(null);
    try {
      await apiPost("/api/slack-command", { message: msg });
      setSlackResult("Sent to #beezy-agents");
      setSlackMsg("");
    } catch (e: unknown) {
      setSlackResult(e instanceof Error ? e.message : "Error sending");
    } finally {
      setSlackLoading(false);
    }
  };

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-red-700">
        Failed to load system data: {error.message}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-10 w-64 rounded-xl" />
        <div className="grid grid-cols-3 gap-4">
          {[0,1,2,3,4,5].map(i => <div key={i} className="skeleton h-20 rounded-xl" />)}
        </div>
        <div className="skeleton h-[400px] w-full rounded-xl" />
      </div>
    );
  }

  const { env_status, cron_sentinels, recent_runs, db_ok } = data;

  return (
    <div className="space-y-5">
      <h1
        className="text-2xl font-bold text-[#8b4513]"
        style={{ fontFamily: "var(--font-dm-serif)" }}
      >
        System
      </h1>

      {/* Environment variables */}
      <div className="bg-white rounded-xl border border-[#e8dcc8] shadow-sm p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355]">
            Environment Variables
          </h2>
          <span
            className={`flex items-center gap-1.5 text-xs font-semibold px-3 py-1 rounded-full ${
              db_ok
                ? "bg-green-50 text-[#1e7e34] border border-green-200"
                : "bg-red-50 text-[#c0392b] border border-red-200"
            }`}
          >
            <span className={`w-2 h-2 rounded-full ${db_ok ? "bg-[#1e7e34]" : "bg-[#c0392b]"}`} />
            DB {db_ok ? "Connected" : "Error"}
          </span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          {Object.entries(ENV_LABELS).map(([key, label]) => {
            const present = env_status[key] ?? false;
            return (
              <div
                key={key}
                className={`flex items-center gap-3 rounded-lg px-4 py-3 border ${
                  present
                    ? "bg-green-50 border-green-200"
                    : "bg-red-50 border-red-200"
                }`}
              >
                <span
                  className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                    present ? "bg-[#1e7e34]" : "bg-[#c0392b]"
                  }`}
                />
                <div>
                  <div
                    className={`text-xs font-semibold ${
                      present ? "text-[#1e7e34]" : "text-[#c0392b]"
                    }`}
                  >
                    {label}
                  </div>
                  <div className="text-[10px] text-gray-400 font-mono">{key}</div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Manual triggers */}
      <div className="bg-white rounded-xl border border-[#e8dcc8] shadow-sm p-5">
        <h2 className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] mb-4">
          Manual Triggers
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <ActionTrigger label="Generate Calendar"   endpoint="/api/generate-calendar"  variant="primary" />
          <ActionTrigger label="Run Orchestrator"    endpoint="/api/run-orchestrator"   variant="primary" />
          <ActionTrigger label="Run Ingestion Sync"  endpoint="/api/run-ingestion"      />
          <ActionTrigger label="Run Learning Loop"   endpoint="/api/run-learning-loop"  />
          <ActionTrigger label="Run Flow Check"      endpoint="/api/run-flow-check"     />
          <ActionTrigger label="Refresh Revenue"     endpoint="/api/refresh-pacing"     />
          <ActionTrigger label="Refresh Audiences"   endpoint="/api/refresh-audience-health" />
          <ActionTrigger label="Approve This Week"   endpoint="/api/approve-week"       variant="primary" />
          <ActionTrigger label="Approve All Weeks"       endpoint="/api/approve-month"              />
          <ActionTrigger label="Deliverability Check"  endpoint="/api/run-deliverability-check"   />
        </div>
      </div>

      {/* Slack command console */}
      <div className="bg-white rounded-xl border border-[#e8dcc8] shadow-sm p-5">
        <h2 className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] mb-1">
          Slack Command Console
        </h2>
        <p className="text-xs text-gray-400 mb-4">
          Send a message to #beezy-agents as if typed by Boris.
        </p>
        <div className="flex gap-2">
          <input
            type="text"
            value={slackMsg}
            onChange={(e) => setSlackMsg(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !slackLoading && handleSlack()}
            placeholder='e.g. "approved week" or "what is revenue"'
            className="flex-1 border border-[#e8dcc8] rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:border-[#8b4513] bg-white"
          />
          <button
            onClick={handleSlack}
            disabled={slackLoading || !slackMsg.trim()}
            className="px-5 py-2.5 bg-[#8b4513] text-white rounded-lg text-sm font-semibold hover:bg-[#6d3410] transition-colors disabled:opacity-50"
          >
            {slackLoading ? "Sending…" : "Send"}
          </button>
        </div>
        {slackResult && (
          <p className="text-xs mt-2 text-[#8b7355]">{slackResult}</p>
        )}
        <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-2">
          {[
            "approved week",
            "approved",
            "deploy campaigns",
            "what is revenue",
            "status",
            "generate calendar",
            "run weekly brief",
            "help",
          ].map((cmd) => (
            <button
              key={cmd}
              onClick={() => setSlackMsg(cmd)}
              className="text-xs px-3 py-1.5 bg-[#faf6ee] border border-[#e8dcc8] rounded-lg text-[#2c2417] hover:border-[#8b4513] transition-colors text-left"
            >
              {cmd}
            </button>
          ))}
        </div>
      </div>

      {/* Cron schedule */}
      <div className="bg-white rounded-xl border border-[#e8dcc8] shadow-sm overflow-hidden">
        <div className="px-5 py-4 border-b border-[#e8dcc8]">
          <h2 className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355]">
            Cron Schedule
          </h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b-2 border-[#e8dcc8]">
                <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-5 py-3">
                  Job
                </th>
                <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3 whitespace-nowrap">
                  Schedule
                </th>
                <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3 whitespace-nowrap">
                  Last Run
                </th>
                <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {CRON_SCHEDULE.map((row, i) => {
                const sentinel = cron_sentinels[row.sentinel];
                const today = new Date().toISOString().slice(0, 10);
                const ranToday = sentinel?.value === today;
                return (
                  <tr
                    key={i}
                    className="border-b border-[#f0ece4] last:border-0 hover:bg-[#fdf8f2] transition-colors"
                  >
                    <td className="px-5 py-2.5 text-sm font-medium">{row.job}</td>
                    <td className="px-3 py-2.5 text-xs text-gray-500 whitespace-nowrap">
                      {row.time}
                    </td>
                    <td className="px-3 py-2.5 text-xs text-gray-500 whitespace-nowrap">
                      {sentinel?.updated || "—"}
                    </td>
                    <td className="px-3 py-2.5">
                      {sentinel ? (
                        <span
                          className={`inline-block px-2 py-0.5 rounded-full text-white text-[11px] font-semibold ${
                            ranToday ? "bg-[#1e7e34]" : "bg-gray-400"
                          }`}
                        >
                          {ranToday ? "Ran today" : sentinel.value}
                        </span>
                      ) : (
                        <span className="text-xs text-gray-300">Not recorded</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Recent runs */}
      <div className="bg-white rounded-xl border border-[#e8dcc8] shadow-sm overflow-hidden">
        <div className="px-5 py-4 border-b border-[#e8dcc8] flex items-center justify-between">
          <h2 className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355]">
            Recent Runs
          </h2>
          <span className="text-xs text-gray-400">Last 20</span>
        </div>
        {recent_runs.length === 0 ? (
          <div className="p-8 text-center text-gray-400 italic text-sm">
            No run history yet.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b-2 border-[#e8dcc8]">
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-5 py-3">
                    Worker
                  </th>
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                    Status
                  </th>
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3 whitespace-nowrap">
                    Cost
                  </th>
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3 whitespace-nowrap">
                    Elapsed
                  </th>
                  <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3 whitespace-nowrap">
                    Time
                  </th>
                </tr>
              </thead>
              <tbody>
                {recent_runs.map((run, i) => {
                  const statusBg = STATUS_COLORS[run.status] ?? "#888";
                  return (
                    <tr
                      key={i}
                      className="border-b border-[#f0ece4] last:border-0 hover:bg-[#fdf8f2] transition-colors"
                    >
                      <td className="px-5 py-2.5 font-mono text-xs text-[#2c2417]">
                        {run.worker}
                      </td>
                      <td className="px-3 py-2.5">
                        <span
                          className="inline-block px-2 py-0.5 rounded-full text-white text-[11px] font-semibold"
                          style={{ background: statusBg }}
                        >
                          {run.status}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-xs text-gray-500">
                        {run.cost > 0 ? `$${run.cost.toFixed(4)}` : "—"}
                      </td>
                      <td className="px-3 py-2.5 text-xs text-gray-500">
                        {run.elapsed > 0 ? `${run.elapsed.toFixed(1)}s` : "—"}
                      </td>
                      <td className="px-3 py-2.5 text-xs text-gray-400 whitespace-nowrap">
                        {run.created}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
