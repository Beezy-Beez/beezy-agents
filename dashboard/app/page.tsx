"use client";

import useSWR from "swr";
import Link from "next/link";
import {
  DollarSign,
  Mail,
  ShoppingBag,
  Target,
  Zap,
  CalendarCheck,
  RefreshCw,
} from "lucide-react";
import { fetcher, apiPost } from "@/lib/api";
import {
  money,
  moneyK,
  num,
  pct,
  ctLabel,
  audLabel,
  fmtDate,
  statusTone,
} from "@/lib/format";
import type { BusinessData, OverviewData } from "@/lib/types";
import { Card, CardHeader } from "@/components/Card";
import StatCard from "@/components/StatCard";
import ProgressBar from "@/components/ProgressBar";
import Badge from "@/components/Badge";
import PageHeader from "@/components/PageHeader";
import ActionButton from "@/components/ActionButton";
import { RevenueArea } from "@/components/charts";
import { EmptyState, PageSkeleton, ErrorState } from "@/components/States";

const REFRESH = 30_000;

export default function Overview() {
  const biz = useSWR<BusinessData>("/api/data/business", fetcher, {
    refreshInterval: REFRESH,
  });
  const ov = useSWR<OverviewData>("/api/data/overview", fetcher, {
    refreshInterval: REFRESH,
  });

  if (biz.error || ov.error) return <ErrorState msg="backend unreachable" />;
  if (!biz.data || !ov.data) return <PageSkeleton />;

  const s = biz.data.store;
  const p = biz.data.pacing;
  const { today_slots, approval, next_send } = ov.data;

  const goalPct = (s.store_mtd / s.goal) * 100;
  const pacePct = (p.days_elapsed / (p.days_elapsed + p.days_left)) * 100;
  const onPace = goalPct >= pacePct * 0.95;
  const statusLabel =
    p.status === "AHEAD"
      ? "Ahead of pace"
      : p.status === "BEHIND"
      ? "Behind pace"
      : "On pace";
  const statusTone2: "good" | "bad" =
    p.status === "BEHIND" ? "bad" : "good";

  return (
    <>
      <PageHeader
        title="Overview"
        sub="Live store & email-marketing performance — May 2026"
        actions={
          <ActionButton
            label="Refresh data"
            icon={<RefreshCw size={14} />}
            run={() => apiPost("/api/refresh-pacing")}
            okMsg="Pulled fresh revenue from Klaviyo + Shopify"
            onDone={() => {
              biz.mutate();
              ov.mutate();
            }}
          />
        }
      />

      {/* Revenue hero */}
      <Card className="mb-5">
        <div className="flex flex-col lg:flex-row gap-8">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 mb-1">
              <span className="label">Store revenue · month to date</span>
              <Badge tone={statusTone2} dot>
                {statusLabel}
              </Badge>
            </div>
            <div className="flex items-end gap-3 flex-wrap">
              <span className="text-[2.6rem] leading-none font-semibold tracking-tight text-ink tabular-nums">
                {money(s.store_mtd)}
              </span>
              <span className="text-ink-muted text-sm mb-1.5">
                of {money(s.goal)} goal · {pct(goalPct)}
              </span>
            </div>
            <div className="mt-4">
              <ProgressBar
                pct={goalPct}
                tone={onPace ? "good" : "warn"}
                marker={pacePct}
                height="h-3"
              />
              <div className="flex justify-between text-2xs text-ink-faint mt-1.5">
                <span>Day {p.days_elapsed}</span>
                <span>
                  Pace line {pct(pacePct, 0)} · {p.days_left} days left
                </span>
                <span>{money(s.goal)}</span>
              </div>
            </div>
            <div className="grid grid-cols-3 gap-4 mt-6">
              <Mini
                label="Forecast"
                value={money(p.forecast)}
                sub="at current pace"
              />
              <Mini
                label="Daily needed"
                value={money(p.daily_needed)}
                sub="to hit goal"
              />
              <Mini
                label="Orders"
                value={num(s.order_count)}
                sub={`AOV ${money(s.aov, 2)}`}
              />
            </div>
          </div>

          {/* Attribution split */}
          <div className="lg:w-72 lg:border-l lg:border-line lg:pl-8 flex flex-col">
            <span className="label mb-3">Email-attributed</span>
            <div className="text-[1.7rem] leading-none stat-num">
              {money(s.attributed)}
            </div>
            <div className="text-xs text-ink-muted mt-1">
              {pct(s.pct_attributed)} of store revenue
            </div>
            <div className="mt-5 space-y-3">
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
            <Link
              href="/analytics"
              className="text-xs font-medium text-accent hover:text-accent-ink mt-auto pt-5"
            >
              View analytics →
            </Link>
          </div>
        </div>
      </Card>

      {/* KPI row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
        <StatCard
          label="Store MTD"
          value={moneyK(s.store_mtd)}
          icon={<ShoppingBag size={15} />}
          delta={pct(goalPct, 0) + " of goal"}
          deltaTone={onPace ? "good" : "bad"}
        />
        <StatCard
          label="Email revenue"
          value={moneyK(s.attributed)}
          icon={<Mail size={15} />}
          sub={`${pct(s.pct_attributed)} attributed`}
        />
        <StatCard
          label="Campaigns"
          value={moneyK(s.campaign_rev)}
          icon={<DollarSign size={15} />}
          sub={`${p.cc} sent`}
        />
        <StatCard
          label="Forecast"
          value={moneyK(p.forecast)}
          icon={<Target size={15} />}
          delta={p.forecast >= s.goal ? "hits goal" : "under goal"}
          deltaTone={p.forecast >= s.goal ? "good" : "bad"}
        />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        {/* Trend */}
        <Card className="xl:col-span-2">
          <CardHeader
            title="Store revenue — last 30 days"
            sub="Net Shopify orders, post-refund"
          />
          {s.store_trend.length ? (
            <RevenueArea data={s.store_trend} />
          ) : (
            <EmptyState title="No order data in the last 30 days" />
          )}
        </Card>

        {/* Approval */}
        <Card>
          <CardHeader title="Approval gate" />
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-medium text-ink">This week</div>
                <div className="text-xs text-ink-muted">
                  {approval.week_start
                    ? `Week of ${fmtDate(approval.week_start)}`
                    : "—"}
                </div>
              </div>
              <Badge tone={approval.week_approved ? "good" : "warn"} dot>
                {approval.week_approved ? "Approved" : "Pending"}
              </Badge>
            </div>
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-medium text-ink">Month plan</div>
                <div className="text-xs text-ink-muted">
                  {approval.upcoming_count} upcoming ·{" "}
                  {money(approval.total_estimated_rev)} est.
                </div>
              </div>
              <Badge tone={approval.month_has_plan ? "good" : "bad"} dot>
                {approval.month_has_plan ? "Generated" : "Missing"}
              </Badge>
            </div>
            <div className="flex gap-2 pt-1">
              {!approval.week_approved && (
                <ActionButton
                  label="Approve week"
                  variant="primary"
                  size="sm"
                  icon={<CalendarCheck size={13} />}
                  run={() => apiPost("/api/approve-week")}
                  okMsg="This week approved"
                  onDone={() => ov.mutate()}
                />
              )}
              <Link href="/calendar" className="btn-ghost btn-sm">
                Open calendar
              </Link>
            </div>
          </div>
        </Card>
      </div>

      {/* Today */}
      <Card className="mt-5">
        <CardHeader
          title="Today’s agenda"
          sub={
            today_slots.length
              ? `${today_slots.length} scheduled`
              : next_send
              ? `Rest day · next send ${next_send}`
              : "Rest day"
          }
          action={
            <ActionButton
              label="Run orchestrator"
              size="sm"
              icon={<Zap size={13} />}
              run={() => apiPost("/api/run-orchestrator")}
              okMsg="Orchestrator dispatched"
              confirm="Dispatch today’s slots now?"
              onDone={() => ov.mutate()}
            />
          }
        />
        {today_slots.length === 0 ? (
          <EmptyState
            title="Nothing scheduled today"
            hint={next_send ? `Next send: ${next_send}` : undefined}
          />
        ) : (
          <div className="divide-y divide-line2">
            {today_slots.map((t, i) => (
              <div
                key={i}
                className="flex items-center gap-4 py-3 first:pt-0 last:pb-0"
              >
                <Badge tone="accent">{ctLabel(t.t)}</Badge>
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-ink truncate">
                    {audLabel(t.a)}
                  </div>
                  <div className="text-xs text-ink-muted truncate">
                    {t.tp || "—"}
                  </div>
                </div>
                <div className="text-sm text-ink-soft tabular-nums hidden sm:block">
                  {t.rv ? money(t.rv) : "—"}
                </div>
                <Badge tone={statusTone(t.s)}>{t.s}</Badge>
                {t.kid && (
                  <a
                    href={`https://www.klaviyo.com/campaign/${t.kid}/reports/overview`}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs font-medium text-accent hover:text-accent-ink"
                  >
                    Klaviyo →
                  </a>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}

function Mini({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub: string;
}) {
  return (
    <div>
      <div className="label mb-1">{label}</div>
      <div className="text-lg font-semibold text-ink tabular-nums">{value}</div>
      <div className="text-2xs text-ink-muted">{sub}</div>
    </div>
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
          {money(value)}
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
