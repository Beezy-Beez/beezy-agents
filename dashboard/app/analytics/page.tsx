"use client";

import useSWR from "swr";
import { Mail, DollarSign, Zap, Target } from "lucide-react";
import { fetcher } from "@/lib/api";
import {
  money,
  moneyK,
  pct,
  rpr,
  fmtDate,
  audLabel,
  ctLabel,
} from "@/lib/format";
import type {
  AnalyticsData,
  BusinessData,
  PacingHistoryData,
  TopPerformer,
} from "@/lib/types";
import { Card, CardHeader } from "@/components/Card";
import PageHeader from "@/components/PageHeader";
import StatCard from "@/components/StatCard";
import Badge from "@/components/Badge";
import DataTable from "@/components/DataTable";
import type { Column } from "@/components/DataTable";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/States";
import { RevenueArea, PacingLines, HBars } from "@/components/charts";

const REFRESH = 30_000;

export default function Analytics() {
  const an = useSWR<AnalyticsData>("/api/data/analytics", fetcher, {
    refreshInterval: REFRESH,
  });
  const ph = useSWR<PacingHistoryData>("/api/data/pacing-history", fetcher, {
    refreshInterval: REFRESH,
  });
  const biz = useSWR<BusinessData>("/api/data/business", fetcher, {
    refreshInterval: REFRESH,
  });

  if (an.error || ph.error || biz.error)
    return <ErrorState msg="backend unreachable" />;
  if (!an.data || !ph.data || !biz.data) return <PageSkeleton />;

  const { top_performers, learning, revenue_trend } = an.data;
  const history = ph.data.history;
  const s = biz.data.store;
  const p = biz.data.pacing;

  const hitsGoal = s.goal <= p.forecast;

  const rprRows = Object.entries(learning.rpr_by_audience || {})
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 12)
    .map(([k, v]) => ({ name: audLabel(k), value: v }));

  const cols: Column<TopPerformer>[] = [
    {
      header: "Rank",
      headerClassName: "w-14",
      render: (_r, i) => (
        <span className="text-ink-faint tabular-nums">#{i + 1}</span>
      ),
    },
    {
      header: "Audience",
      render: (r) => (
        <span className="font-medium text-ink">{audLabel(r.a)}</span>
      ),
    },
    {
      header: "Type",
      render: (r) => <Badge tone="accent">{ctLabel(r.t)}</Badge>,
    },
    {
      header: "Revenue",
      className: "tabular-nums text-ink",
      render: (r) => money(r.rv),
    },
    {
      header: "RPR",
      className: "tabular-nums",
      render: (r) => rpr(r.rpr),
    },
    {
      header: "Date",
      className: "text-ink-muted",
      render: (r) => fmtDate(r.d),
    },
  ];

  return (
    <>
      <PageHeader
        title="Analytics"
        sub="Revenue, attribution, pacing trajectory & learning"
      />

      {/* KPI row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
        <StatCard
          label="Email revenue"
          value={moneyK(s.attributed)}
          icon={<Mail size={15} />}
          sub={`${pct(s.pct_attributed)} of store`}
        />
        <StatCard
          label="Campaigns"
          value={moneyK(s.campaign_rev)}
          icon={<DollarSign size={15} />}
          sub={`${p.cc} sent`}
        />
        <StatCard
          label="Flows"
          value={moneyK(s.flow_rev)}
          icon={<Zap size={15} />}
        />
        <StatCard
          label="Forecast"
          value={moneyK(p.forecast)}
          icon={<Target size={15} />}
          delta={hitsGoal ? "hits goal" : "under"}
          deltaTone={hitsGoal ? "good" : "bad"}
        />
      </div>

      {/* Revenue + attribution */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        <Card className="xl:col-span-2">
          <CardHeader
            title="Email revenue — 30 days"
            sub="Klaviyo-attributed daily revenue"
          />
          {revenue_trend.length ? (
            <RevenueArea data={revenue_trend} />
          ) : (
            <EmptyState title="No attributed revenue in the last 30 days" />
          )}
        </Card>

        <Card>
          <CardHeader
            title="Attribution"
            sub={`${money(s.attributed)} email-attributed`}
          />
          <div className="space-y-4 mt-1">
            <SplitRow
              label="Campaigns"
              value={s.campaign_rev}
              total={s.attributed}
              tone="bg-accent"
            />
            <SplitRow
              label="Flows"
              value={s.flow_rev}
              total={s.attributed}
              tone="bg-good"
            />
          </div>
          <div className="mt-6 pt-5 border-t border-line2 grid grid-cols-2 gap-4">
            <div>
              <div className="label mb-1">Of store</div>
              <div className="text-lg font-semibold text-ink tabular-nums">
                {pct(s.pct_attributed)}
              </div>
            </div>
            <div>
              <div className="label mb-1">AOV</div>
              <div className="text-lg font-semibold text-ink tabular-nums">
                {money(s.aov, 2)}
              </div>
            </div>
          </div>
        </Card>
      </div>

      {/* Pacing + RPR */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5 mt-5">
        <Card className="xl:col-span-2">
          <CardHeader
            title="Pacing trajectory"
            sub="Actual store revenue vs target line"
          />
          {history.length < 2 ? (
            <EmptyState title="Need at least 2 daily snapshots" />
          ) : (
            <PacingLines
              data={history.map((h) => ({
                date: h.date,
                actual: h.actual,
                target: h.target,
              }))}
            />
          )}
        </Card>

        <Card>
          <CardHeader
            title="RPR by audience"
            sub="90-day revenue per recipient"
          />
          {rprRows.length ? (
            <HBars data={rprRows} fmt={(n) => `$${n.toFixed(3)}`} />
          ) : (
            <EmptyState title="No RPR data yet" />
          )}
        </Card>
      </div>

      {/* Top performers */}
      <Card className="mt-5">
        <CardHeader
          title="Top performers"
          sub="Finalized campaigns by attributed revenue"
        />
        <DataTable<TopPerformer>
          columns={cols}
          rows={top_performers}
          emptyMessage="No finalized performance yet"
        />
      </Card>

      {/* Learning loop */}
      <Card className="mt-5">
        <CardHeader
          title="Learning loop"
          sub="Self-correction history from weekly / monthly retros"
        />
        {learning.entries.length ? (
          <div className="divide-y divide-line2">
            {learning.entries.map((e, i) => (
              <div
                key={i}
                className="flex items-start gap-4 py-3 first:pt-0 last:pb-0"
              >
                <Badge tone="muted">{e.component}</Badge>
                <p className="text-sm text-ink-soft flex-1 min-w-0">
                  {e.summary}
                </p>
                <span className="text-2xs text-ink-faint whitespace-nowrap tabular-nums">
                  {fmtDate(e.at)}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState title="No learning-loop entries yet" />
        )}
      </Card>
    </>
  );
}

function SplitRow({
  label,
  value,
  total,
  tone,
}: {
  label: string;
  value: number;
  total: number;
  tone: string;
}) {
  const w = total > 0 ? (value / total) * 100 : 0;
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-ink-soft">{label}</span>
        <span className="text-ink font-medium tabular-nums">
          {money(value)} · {pct(w, 0)}
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-line2 overflow-hidden">
        <div
          className={`h-full rounded-full ${tone}`}
          style={{ width: `${w}%` }}
        />
      </div>
    </div>
  );
}
