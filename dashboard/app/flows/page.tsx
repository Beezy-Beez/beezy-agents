"use client";

import useSWR from "swr";
import { Workflow, ShieldCheck } from "lucide-react";
import { fetcher, apiPost } from "@/lib/api";
import { money, num, pct, statusTone } from "@/lib/format";
import { Card, CardHeader } from "@/components/Card";
import StatCard from "@/components/StatCard";
import Badge from "@/components/Badge";
import PageHeader from "@/components/PageHeader";
import ActionButton from "@/components/ActionButton";
import DataTable from "@/components/DataTable";
import type { Column } from "@/components/DataTable";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/States";

const REFRESH = 30_000;

type Loose = Record<string, unknown>;
type FlowRow = Loose & { _group: string };

function asNumber(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function asString(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

export default function FlowsPage() {
  const deliv = useSWR<Loose>("/api/data/deliverability", fetcher, {
    refreshInterval: REFRESH,
  });
  const flows = useSWR<Loose>("/api/data/flows", fetcher, {
    refreshInterval: REFRESH,
  });

  if (deliv.error || flows.error)
    return <ErrorState msg="backend unreachable" />;
  if (!deliv.data || !flows.data) return <PageSkeleton />;

  const d = deliv.data;
  const f = flows.data;

  const deliveryRate = asNumber(d.delivery_rate);
  const bounceRate = asNumber(d.bounce_rate);
  const unsubRate = asNumber(d.unsub_rate);
  const recipients = asNumber(d.recipients);
  const source = asString(d._source) || "—";
  const checkedAt = asString(d._checked_at);

  // Flatten every array-valued property of the loose flows object.
  const rows: FlowRow[] = [];
  for (const [key, val] of Object.entries(f)) {
    if (Array.isArray(val)) {
      for (const item of val) {
        if (item && typeof item === "object") {
          rows.push({ ...(item as Loose), _group: key });
        }
      }
    }
  }

  const nonUnderscoreKeys = Object.keys(f).filter((k) => !k.startsWith("_"));
  const flowsCheckedAt = asString(f._checked_at);

  const columns: Column<FlowRow>[] = [
    {
      header: "Group",
      render: (row) => <Badge tone="muted">{row._group}</Badge>,
    },
    {
      header: "Flow",
      render: (row) => {
        const r = row as Loose;
        const name = asString(r.name) || asString(r.flow_id) || "—";
        return <span className="font-medium text-ink">{name}</span>;
      },
    },
    {
      header: "Revenue",
      className: "tabular-nums",
      render: (row) => {
        const r = row as Loose;
        return typeof r.revenue === "number" ? money(r.revenue) : "—";
      },
    },
    {
      header: "RPR",
      className: "tabular-nums",
      render: (row) => {
        const r = row as Loose;
        return typeof r.rpr === "number" ? `$${r.rpr.toFixed(3)}` : "—";
      },
    },
    {
      header: "Recipients",
      className: "tabular-nums",
      render: (row) => {
        const r = row as Loose;
        return typeof r.recipients === "number" ? num(r.recipients) : "—";
      },
    },
    {
      header: "Status",
      render: (row) => {
        const r = row as Loose;
        if (r.severity != null) {
          const s = String(r.severity);
          return <Badge tone={statusTone(s)}>{s}</Badge>;
        }
        if (r.fix_queued) return <Badge tone="warn">fix queued</Badge>;
        return "—";
      },
    },
  ];

  return (
    <>
      <PageHeader
        title="Flows & Deliverability"
        sub="Klaviyo flow health and sending reputation"
        actions={
          <>
            <ActionButton
              label="Run flow check"
              icon={<Workflow size={14} />}
              run={() => apiPost("/api/run-flow-check")}
              okMsg="Flow check started — refresh in ~30s"
              onDone={() => flows.mutate()}
            />
            <ActionButton
              label="Run deliverability check"
              icon={<ShieldCheck size={14} />}
              run={() => apiPost("/api/run-deliverability-check")}
              okMsg="Deliverability check started"
              onDone={() => deliv.mutate()}
            />
          </>
        }
      />

      {/* Deliverability */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-2">
        <StatCard
          label="Delivery rate"
          value={deliveryRate != null ? pct(deliveryRate, 2) : "—"}
          delta={
            deliveryRate != null
              ? deliveryRate >= 99
                ? "healthy"
                : "low"
              : undefined
          }
          deltaTone={
            deliveryRate != null
              ? deliveryRate >= 99
                ? "good"
                : "bad"
              : undefined
          }
        />
        <StatCard
          label="Bounce rate"
          value={bounceRate != null ? pct(bounceRate, 3) : "—"}
          delta={
            bounceRate != null
              ? bounceRate > 0.5
                ? "elevated"
                : "clean"
              : undefined
          }
          deltaTone={
            bounceRate != null
              ? bounceRate > 0.5
                ? "bad"
                : "good"
              : undefined
          }
        />
        <StatCard
          label="Unsub rate"
          value={unsubRate != null ? pct(unsubRate, 3) : "—"}
          delta={
            unsubRate != null
              ? unsubRate > 0.3
                ? "elevated"
                : "clean"
              : undefined
          }
          deltaTone={
            unsubRate != null
              ? unsubRate > 0.3
                ? "bad"
                : "good"
              : undefined
          }
        />
        <StatCard
          label="Recipients 30d"
          value={recipients != null ? num(recipients) : "—"}
        />
      </div>
      <p className="text-2xs text-ink-faint mb-5">
        Source: {source}
        {checkedAt ? ` · checked ${checkedAt}` : ""}
      </p>

      {/* Flow health */}
      <Card>
        <CardHeader
          title="Flow health"
          sub={flowsCheckedAt ? `Checked ${flowsCheckedAt}` : undefined}
          action={
            rows.length === 0 && nonUnderscoreKeys.length > 0 ? (
              <span className="text-xs text-ink-muted">
                Raw analysis (no tabular flows detected)
              </span>
            ) : undefined
          }
        />
        {rows.length > 0 ? (
          <DataTable<FlowRow> columns={columns} rows={rows} />
        ) : nonUnderscoreKeys.length > 0 ? (
          <pre className="text-xs text-ink-soft bg-canvas border border-line rounded-lg p-4 overflow-auto">
            {JSON.stringify(f, null, 2)}
          </pre>
        ) : (
          <EmptyState
            title="No flow analysis yet"
            hint="Run a flow check to pull 30-day Klaviyo flow metrics."
          />
        )}
      </Card>
    </>
  );
}
