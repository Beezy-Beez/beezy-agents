"use client";

import useSWR from "swr";
import { RefreshCw, Users, DollarSign, Sparkles, Flame } from "lucide-react";
import { fetcher, apiPost } from "@/lib/api";
import { rpr, num, fmtDate, audLabel } from "@/lib/format";
import type { AudiencesData, AudienceHealth } from "@/lib/types";
import { Card, CardHeader } from "@/components/Card";
import StatCard from "@/components/StatCard";
import Badge from "@/components/Badge";
import PageHeader from "@/components/PageHeader";
import ActionButton from "@/components/ActionButton";
import DataTable from "@/components/DataTable";
import type { Column } from "@/components/DataTable";
import { HBars } from "@/components/charts";
import { EmptyState, PageSkeleton, ErrorState } from "@/components/States";

const REFRESH = 30_000;

type HealthLevel = AudienceHealth["health"];

const healthTone = (h: HealthLevel): "good" | "warn" | "muted" =>
  h === "RECENT" ? "good" : h === "WARM" ? "warn" : "muted";

export default function Audiences() {
  const aud = useSWR<AudiencesData>("/api/data/audiences", fetcher, {
    refreshInterval: REFRESH,
  });

  if (aud.error) return <ErrorState msg="backend unreachable" />;
  if (!aud.data) return <PageSkeleton />;

  const { health, burn_list } = aud.data;
  const burned = new Set(burn_list);

  const sorted = [...health].sort((a, b) => b.rpr_90d - a.rpr_90d);

  const withRpr = health.filter((h) => h.rpr_90d > 0);
  const avgRpr =
    withRpr.length > 0
      ? withRpr.reduce((s, h) => s + h.rpr_90d, 0) / withRpr.length
      : 0;
  const freshest =
    health.length > 0 ? Math.min(...health.map((h) => h.days_since)) : null;

  const barData = withRpr
    .slice()
    .sort((a, b) => b.rpr_90d - a.rpr_90d)
    .slice(0, 12)
    .map((h) => ({ name: audLabel(h.audience), value: h.rpr_90d }));

  const columns: Column<AudienceHealth>[] = [
    {
      header: "Audience",
      render: (r) => (
        <span className="inline-flex items-center gap-2">
          <span className="font-medium text-ink">{audLabel(r.audience)}</span>
          {burned.has(r.audience) && <Badge tone="bad">Burned</Badge>}
        </span>
      ),
    },
    {
      header: "Last send",
      render: (r) => fmtDate(r.last_send),
      className: "text-ink-soft tabular-nums",
    },
    {
      header: "Days since",
      render: (r) => num(r.days_since),
      className: "text-ink-soft tabular-nums",
    },
    {
      header: "90d RPR",
      render: (r) => (
        <span className="text-ink font-medium tabular-nums">
          {rpr(r.rpr_90d)}
        </span>
      ),
    },
    {
      header: "30d RPR",
      render: (r) => rpr(r.rpr_30d),
      className: "text-ink-soft tabular-nums",
    },
    {
      header: "Sends 90d",
      render: (r) => num(r.sends_90d),
      className: "text-ink-soft tabular-nums",
    },
    {
      header: "Health",
      render: (r) => (
        <Badge tone={healthTone(r.health)} dot>
          {r.health}
        </Badge>
      ),
    },
    {
      header: "",
      headerClassName: "text-right",
      className: "text-right",
      render: (r) =>
        burned.has(r.audience) ? (
          <ActionButton
            label="Unburn"
            size="sm"
            run={() =>
              apiPost(
                `/api/unburn-audience?audience=${encodeURIComponent(
                  r.audience
                )}`
              )
            }
            okMsg={`${audLabel(r.audience)} unburned`}
            onDone={() => aud.mutate()}
          />
        ) : (
          <ActionButton
            label="Burn"
            size="sm"
            variant="danger"
            confirm="Burn this audience? It will be blocked by validator R5."
            run={() =>
              apiPost(
                `/api/burn-audience?audience=${encodeURIComponent(r.audience)}`
              )
            }
            okMsg={`${audLabel(r.audience)} burned`}
            onDone={() => aud.mutate()}
          />
        ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Audiences"
        sub="RPR & freshness per segment — live Klaviyo 90-day"
        actions={
          <ActionButton
            label="Refresh from Klaviyo"
            icon={<RefreshCw size={14} />}
            run={() => apiPost("/api/refresh-audience-health")}
            okMsg="Pulled fresh campaign history from Klaviyo"
            onDone={() => aud.mutate()}
          />
        }
      />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
        <StatCard
          label="Tracked"
          value={num(health.length)}
          icon={<Users size={15} />}
          sub="segments monitored"
        />
        <StatCard
          label="Avg 90d RPR"
          value={rpr(avgRpr)}
          icon={<DollarSign size={15} />}
          sub={`${withRpr.length} with revenue`}
        />
        <StatCard
          label="Freshest"
          value={freshest == null ? "—" : `${freshest}d ago`}
          icon={<Sparkles size={15} />}
          sub="most recent contact"
        />
        <StatCard
          label="Burned"
          value={num(burn_list.length)}
          icon={<Flame size={15} />}
          delta={burn_list.length > 0 ? "blocked by R5" : "none blocked"}
          deltaTone={burn_list.length > 0 ? "bad" : "good"}
        />
      </div>

      <Card className="mb-5">
        <CardHeader
          title="RPR by audience (90d)"
          sub="Top segments by revenue per recipient — live Klaviyo"
        />
        {barData.length ? (
          <HBars data={barData} fmt={(n) => `$${n.toFixed(3)}`} />
        ) : (
          <EmptyState
            title="No RPR data yet"
            hint="Click Refresh from Klaviyo to pull 90-day campaign history."
          />
        )}
      </Card>

      <Card>
        <CardHeader
          title="Segment health"
          sub="Sorted by 90-day RPR · RECENT < 7d · WARM 7–14d · stale otherwise"
        />
        {health.length === 0 ? (
          <EmptyState
            title="No audience health data"
            hint="Click Refresh from Klaviyo to pull fresh campaign history."
          />
        ) : (
          <DataTable<AudienceHealth> columns={columns} rows={sorted} />
        )}
      </Card>
    </>
  );
}
