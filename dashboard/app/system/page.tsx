"use client";

import { useState } from "react";
import useSWR from "swr";
import {
  Database,
  KeyRound,
  Activity,
  DollarSign,
  DownloadCloud,
  Zap,
  CalendarRange,
  Brain,
  RefreshCw,
  HeartPulse,
  Send,
} from "lucide-react";
import { fetcher, apiPost } from "@/lib/api";
import { money, statusTone } from "@/lib/format";
import type { SystemData, RecentRun } from "@/lib/types";
import { Card, CardHeader } from "@/components/Card";
import PageHeader from "@/components/PageHeader";
import StatCard from "@/components/StatCard";
import Badge from "@/components/Badge";
import Button from "@/components/Button";
import ActionButton from "@/components/ActionButton";
import DataTable from "@/components/DataTable";
import type { Column } from "@/components/DataTable";
import { TextInput } from "@/components/Field";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/States";
import { useToast } from "@/components/Toast";

const REFRESH = 30_000;

interface SentinelRow {
  key: string;
  value: string;
  updated: string;
}

export default function System() {
  const { data, error, mutate } = useSWR<SystemData>(
    "/api/data/system",
    fetcher,
    { refreshInterval: REFRESH }
  );
  const { toast } = useToast();
  const [slackMsg, setSlackMsg] = useState("");
  const [sending, setSending] = useState(false);

  if (error) return <ErrorState msg="backend unreachable" />;
  if (!data) return <PageSkeleton />;

  const envKeys = Object.keys(data.env_status);
  const envSet = Object.values(data.env_status).filter(Boolean).length;
  const allEnvOk = envKeys.length > 0 && envSet === envKeys.length;
  const totalCost = data.recent_runs.reduce((s, r) => s + (r.cost || 0), 0);

  const sentinelRows: SentinelRow[] = Object.entries(data.cron_sentinels).map(
    ([key, v]) => ({ key, value: v.value, updated: v.updated })
  );

  async function sendSlack() {
    const m = slackMsg.trim();
    if (!m) return;
    setSending(true);
    try {
      await apiPost("/api/slack-command", { message: m });
      toast("Sent to #beezy-agents", "success");
      setSlackMsg("");
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setSending(false);
    }
  }

  const sentinelCols: Column<SentinelRow>[] = [
    {
      header: "Key",
      render: (r) => (
        <span className="font-mono text-xs text-ink">{r.key}</span>
      ),
    },
    {
      header: "Value",
      render: (r) => <span className="text-ink-soft">{r.value || "—"}</span>,
    },
    {
      header: "Updated",
      render: (r) => (
        <span className="text-ink-muted tabular-nums">
          {r.updated || "—"}
        </span>
      ),
    },
  ];

  const runCols: Column<RecentRun>[] = [
    {
      header: "Worker",
      render: (r) => (
        <span className="font-medium text-ink">{r.worker}</span>
      ),
    },
    {
      header: "Status",
      render: (r) => (
        <Badge tone={statusTone(r.status)} dot>
          {r.status}
        </Badge>
      ),
    },
    {
      header: "Cost",
      className: "tabular-nums",
      render: (r) => `$${(r.cost || 0).toFixed(4)}`,
    },
    {
      header: "Elapsed",
      className: "tabular-nums",
      render: (r) => `${(r.elapsed || 0).toFixed(1)}s`,
    },
    {
      header: "Created",
      render: (r) => (
        <span className="text-ink-muted tabular-nums">{r.created}</span>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="System"
        sub="Cron health, worker runs, environment & operations"
        actions={
          <ActionButton
            label="Refresh"
            icon={<RefreshCw size={14} />}
            run={async () => {
              await mutate();
            }}
            okMsg="System data refreshed"
          />
        }
      />

      {/* KPI row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
        <StatCard
          label="Database"
          value={
            <Badge tone={data.db_ok ? "good" : "bad"} dot>
              {data.db_ok ? "Connected" : "Down"}
            </Badge>
          }
          icon={<Database size={15} />}
        />
        <StatCard
          label="Env keys"
          value={`${envSet}/${envKeys.length}`}
          icon={<KeyRound size={15} />}
          delta={allEnvOk ? "all set" : "missing keys"}
          deltaTone={allEnvOk ? "good" : "bad"}
        />
        <StatCard
          label="Recent runs"
          value={data.recent_runs.length}
          icon={<Activity size={15} />}
        />
        <StatCard
          label="Run cost"
          value={`$${totalCost.toFixed(2)}`}
          icon={<DollarSign size={15} />}
          sub={money(totalCost, 2)}
        />
      </div>

      {/* Operations */}
      <Card className="mb-5">
        <CardHeader
          title="Operations"
          sub="Trigger pipeline jobs on demand"
        />
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <ActionButton
            label="Run ingestion"
            variant="ghost"
            icon={<DownloadCloud size={14} />}
            run={() => apiPost("/api/run-ingestion")}
            okMsg="Ingestion started"
          />
          <ActionButton
            label="Run orchestrator"
            variant="ghost"
            icon={<Zap size={14} />}
            run={() => apiPost("/api/run-orchestrator")}
            okMsg="Orchestrator dispatched"
            confirm="Dispatch today’s slots now?"
          />
          <ActionButton
            label="Generate calendar"
            variant="ghost"
            icon={<CalendarRange size={14} />}
            run={() => apiPost("/api/generate-calendar")}
            okMsg="Calendar generation started"
            confirm="Regenerate calendar via Opus?"
          />
          <ActionButton
            label="Run learning loop"
            variant="ghost"
            icon={<Brain size={14} />}
            run={() => apiPost("/api/run-learning-loop")}
            okMsg="Learning loop started"
          />
          <ActionButton
            label="Refresh pacing"
            variant="ghost"
            icon={<RefreshCw size={14} />}
            run={() => apiPost("/api/refresh-pacing")}
            okMsg="Pacing refreshed"
            onDone={() => mutate()}
          />
          <ActionButton
            label="Refresh audience health"
            variant="ghost"
            icon={<HeartPulse size={14} />}
            run={() => apiPost("/api/refresh-audience-health")}
            okMsg="Audience health refreshed"
          />
        </div>

        <div className="border-t border-line2 my-4" />

        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
          <span className="label sm:w-36 flex-shrink-0">Slack command</span>
          <div className="flex-1">
            <TextInput
              value={slackMsg}
              onChange={(e) => setSlackMsg(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !sending) sendSlack();
              }}
              placeholder="Message to #beezy-agents…"
            />
          </div>
          <Button
            variant="primary"
            loading={sending}
            disabled={!slackMsg.trim()}
            icon={<Send size={14} />}
            onClick={sendSlack}
            className="flex-shrink-0"
          >
            Send
          </Button>
        </div>
      </Card>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5 mb-5">
        {/* Environment */}
        <Card className="xl:col-span-1">
          <CardHeader
            title="Environment"
            sub={`${envSet}/${envKeys.length} keys present`}
          />
          {envKeys.length === 0 ? (
            <EmptyState title="No environment keys reported" />
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {Object.entries(data.env_status).map(([key, present]) => (
                <div
                  key={key}
                  className="flex items-center gap-2 min-w-0 py-1"
                >
                  <Badge tone={present ? "good" : "bad"} dot>
                    {present ? "set" : "missing"}
                  </Badge>
                  <span className="font-mono text-xs text-ink-soft truncate">
                    {key}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>

        {/* Cron sentinels */}
        <Card className="xl:col-span-2">
          <CardHeader
            title="Cron sentinels"
            sub="Last-ran markers backing the catch-up logic"
          />
          <DataTable<SentinelRow>
            columns={sentinelCols}
            rows={sentinelRows}
            emptyMessage="No cron sentinels recorded."
          />
        </Card>
      </div>

      {/* Recent runs */}
      <Card>
        <CardHeader
          title="Recent runs"
          sub="Latest skill invocations & worker jobs"
        />
        <DataTable<RecentRun>
          columns={runCols}
          rows={data.recent_runs}
          emptyMessage="No runs yet."
        />
      </Card>
    </>
  );
}
