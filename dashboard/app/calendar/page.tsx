"use client";

import { useState } from "react";
import useSWR from "swr";
import {
  Plus,
  Pencil,
  Trash2,
  CalendarCheck,
  CalendarRange,
  Sparkles,
  RotateCcw,
} from "lucide-react";
import { fetcher, apiPost, apiPatch, apiDelete } from "@/lib/api";
import {
  money,
  ctLabel,
  audLabel,
  fmtDate,
  fmtTime,
  statusTone,
} from "@/lib/format";
import type { CalendarData, CalendarSlot, ContentType } from "@/lib/types";
import { Card, CardHeader } from "@/components/Card";
import PageHeader from "@/components/PageHeader";
import StatCard from "@/components/StatCard";
import Badge from "@/components/Badge";
import Button from "@/components/Button";
import ActionButton from "@/components/ActionButton";
import DataTable, { type Column } from "@/components/DataTable";
import Drawer from "@/components/Drawer";
import { Field, TextInput, Select } from "@/components/Field";
import { ErrorState, PageSkeleton } from "@/components/States";
import { useToast } from "@/components/Toast";

const REFRESH = 30_000;
const TODAY = new Date().toISOString().slice(0, 10);

const CONTENT_TYPES: ContentType[] = [
  "klaviyo_campaign",
  "sniper_followup",
  "hive_mind",
  "seo_blog",
  "sleep_audio",
  "sms_campaign",
  "flow_experiment",
];

const CT_OPTIONS = CONTENT_TYPES.map((c) => ({
  value: c,
  label: ctLabel(c),
}));

const PRIORITY_OPTIONS = [
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
];

interface SlotForm {
  date: string;
  content_type: ContentType;
  audience: string;
  topic_angle: string;
  send_time_est: string;
  revenue_estimate: string;
  priority: string;
  rationale: string;
}

const emptyForm = (): SlotForm => ({
  date: TODAY,
  content_type: "klaviyo_campaign",
  audience: "",
  topic_angle: "",
  send_time_est: "",
  revenue_estimate: "",
  priority: "medium",
  rationale: "",
});

export default function Calendar() {
  const { toast } = useToast();
  const { data, error, mutate } = useSWR<CalendarData>(
    "/api/data/calendar",
    fetcher,
    { refreshInterval: REFRESH }
  );

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<CalendarSlot | null>(null);
  const [form, setForm] = useState<SlotForm>(emptyForm());
  const [saving, setSaving] = useState(false);

  if (error) return <ErrorState msg="backend unreachable" />;
  if (!data) return <PageSkeleton />;

  const { slots, approval } = data;

  const sorted = [...slots].sort((a, b) =>
    a.date === b.date
      ? (a.tm || "").localeCompare(b.tm || "")
      : a.date.localeCompare(b.date)
  );

  const actualBooked = slots.reduce(
    (acc, s) => acc + (Number(s.actual_rev) || 0),
    0
  );

  function openCreate() {
    setEditing(null);
    setForm(emptyForm());
    setDrawerOpen(true);
  }

  function openEdit(slot: CalendarSlot) {
    setEditing(slot);
    setForm({
      date: slot.date,
      content_type: slot.t,
      audience: slot.a,
      topic_angle: slot.tp || "",
      send_time_est: slot.tm || "",
      revenue_estimate: slot.rv != null ? String(slot.rv) : "",
      priority: "medium",
      rationale: "",
    });
    setDrawerOpen(true);
  }

  function patch<K extends keyof SlotForm>(key: K, value: SlotForm[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function save() {
    setSaving(true);
    try {
      const fields = {
        date: form.date,
        content_type: form.content_type,
        audience: form.audience,
        topic_angle: form.topic_angle,
        send_time_est: form.send_time_est,
        revenue_estimate: Number(form.revenue_estimate) || 0,
        priority: form.priority,
        rationale: form.rationale,
      };
      if (editing) {
        await apiPatch("/api/calendar/slot", {
          locator: {
            date: editing.date,
            content_type: editing.t,
            audience: editing.a,
          },
          fields,
        });
      } else {
        await apiPost("/api/calendar/slot", fields);
      }
      toast("Saved", "success");
      mutate();
      setDrawerOpen(false);
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setSaving(false);
    }
  }

  const columns: Column<CalendarSlot>[] = [
    {
      header: "Date",
      className: "whitespace-nowrap text-ink font-medium",
      render: (r) => fmtDate(r.date),
    },
    {
      header: "Type",
      render: (r) => <Badge tone="accent">{ctLabel(r.t)}</Badge>,
    },
    {
      header: "Audience",
      className: "whitespace-nowrap",
      render: (r) => audLabel(r.a),
    },
    {
      header: "Topic",
      render: (r) => (
        <span
          className="block max-w-[20rem] truncate text-ink-soft"
          title={r.tp || ""}
        >
          {r.tp || "—"}
        </span>
      ),
    },
    {
      header: "Time",
      className: "whitespace-nowrap tabular-nums",
      render: (r) => fmtTime(r.tm) || "—",
    },
    {
      header: "Est",
      className: "tabular-nums whitespace-nowrap",
      render: (r) => money(r.rv),
    },
    {
      header: "Actual",
      className: "tabular-nums whitespace-nowrap",
      render: (r) => {
        const s = (r.status || "").toLowerCase();
        if (["sent", "dispatched", "completed"].includes(s)) {
          const hit = Number(r.actual_rev) >= Number(r.rv);
          return (
            <span
              className={hit ? "text-good font-medium" : "text-bad font-medium"}
            >
              {money(r.actual_rev)}
            </span>
          );
        }
        return <span className="text-ink-faint">—</span>;
      },
    },
    {
      header: "Status",
      render: (r) => <Badge tone={statusTone(r.status)}>{r.status}</Badge>,
    },
    {
      header: "",
      headerClassName: "w-px",
      className: "whitespace-nowrap",
      render: (r) => (
        <div className="flex items-center justify-end gap-1.5">
          <Button
            variant="ghost"
            size="sm"
            icon={<Pencil size={13} />}
            onClick={() => openEdit(r)}
            aria-label="Edit slot"
          />
          {r.status === "failed" && (
            <ActionButton
              label="Retry"
              size="sm"
              icon={<RotateCcw size={13} />}
              run={() => apiPost(`/api/retry-slot?id=${r.exec_id}`)}
              okMsg="Slot re-queued"
              onDone={() => mutate()}
            />
          )}
          <ActionButton
            label=""
            variant="danger"
            size="sm"
            icon={<Trash2 size={13} />}
            confirm="Delete this slot?"
            run={() =>
              apiDelete("/api/calendar/slot", {
                date: r.date,
                content_type: r.t,
                audience: r.a,
              })
            }
            okMsg="Slot deleted"
            onDone={() => mutate()}
          />
        </div>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Calendar"
        sub="Plan vs actual — edit, add, approve"
        actions={
          <>
            <Button
              variant="primary"
              icon={<Plus size={14} />}
              onClick={openCreate}
            >
              Add slot
            </Button>
            <ActionButton
              label="Approve week"
              icon={<CalendarCheck size={14} />}
              run={() => apiPost("/api/approve-week")}
              okMsg="This week approved"
              onDone={() => mutate()}
            />
            <ActionButton
              label="Approve month"
              icon={<CalendarRange size={14} />}
              run={() => apiPost("/api/approve-month")}
              okMsg="Month approved"
              onDone={() => mutate()}
            />
            <ActionButton
              label="Generate calendar"
              icon={<Sparkles size={14} />}
              confirm="Regenerate this month's calendar? This calls Opus."
              run={() => apiPost("/api/generate-calendar")}
              okMsg="Calendar regeneration started"
              onDone={() => mutate()}
            />
          </>
        }
      />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
        <StatCard
          label="Upcoming slots"
          value={approval.upcoming_count}
          icon={<CalendarRange size={15} />}
        />
        <StatCard
          label="Est. revenue"
          value={money(approval.total_estimated_rev)}
          sub="planned this month"
        />
        <StatCard
          label="Actual booked"
          value={money(actualBooked)}
          sub="attributed so far"
        />
        <StatCard
          label="Week status"
          value={
            <Badge tone={approval.week_approved ? "good" : "warn"} dot>
              {approval.week_approved ? "Approved" : "Pending"}
            </Badge>
          }
          sub={
            approval.week_start
              ? `Week of ${fmtDate(approval.week_start)}`
              : undefined
          }
        />
      </div>

      <Card>
        <CardHeader
          title="Schedule"
          sub={`${slots.length} slot${slots.length === 1 ? "" : "s"} this month`}
        />
        <DataTable<CalendarSlot>
          columns={columns}
          rows={sorted}
          rowClassName={(r) => (r.date === TODAY ? "bg-accent-soft/60" : "")}
          emptyMessage="No calendar plan for this month — generate one."
        />
      </Card>

      <Drawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={editing ? "Edit slot" : "New slot"}
        sub={
          editing
            ? `${ctLabel(editing.t)} · ${audLabel(editing.a)}`
            : "Add a slot to this month's calendar"
        }
        footer={
          <>
            <Button variant="ghost" onClick={() => setDrawerOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" loading={saving} onClick={save}>
              Save
            </Button>
          </>
        }
      >
        <Field label="Date">
          <TextInput
            type="date"
            value={form.date}
            onChange={(e) => patch("date", e.target.value)}
          />
        </Field>
        <Field label="Content type">
          <Select
            options={CT_OPTIONS}
            value={form.content_type}
            onChange={(e) =>
              patch("content_type", e.target.value as ContentType)
            }
          />
        </Field>
        <Field label="Audience" hint="Internal segment key, e.g. lapsed_30d">
          <TextInput
            value={form.audience}
            onChange={(e) => patch("audience", e.target.value)}
            placeholder="lapsed_30d"
          />
        </Field>
        <Field label="Topic angle">
          <TextInput
            value={form.topic_angle}
            onChange={(e) => patch("topic_angle", e.target.value)}
            placeholder="Sleep science: why 50+ women wake at 3am"
          />
        </Field>
        <Field label="Send time (ET)">
          <TextInput
            value={form.send_time_est}
            onChange={(e) => patch("send_time_est", e.target.value)}
            placeholder="14:00"
          />
        </Field>
        <Field label="Revenue estimate">
          <TextInput
            type="number"
            value={form.revenue_estimate}
            onChange={(e) => patch("revenue_estimate", e.target.value)}
            placeholder="315"
          />
        </Field>
        <Field label="Priority">
          <Select
            options={PRIORITY_OPTIONS}
            value={form.priority}
            onChange={(e) => patch("priority", e.target.value)}
          />
        </Field>
        <Field label="Rationale">
          <TextInput
            value={form.rationale}
            onChange={(e) => patch("rationale", e.target.value)}
            placeholder="Why this slot, this audience, now"
          />
        </Field>
      </Drawer>
    </>
  );
}
