"use client";

import { useState } from "react";
import useSWR from "swr";
import { Pencil, Mail, FileText, Search, Headphones } from "lucide-react";
import { fetcher, apiPost, apiPatch, apiDelete } from "@/lib/api";
import { fmtDate, statusTone } from "@/lib/format";
import type { ContentData, Issue, SeoTopic, Episode } from "@/lib/types";
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

const PILLAR_OPTIONS = [
  { value: "Signal", label: "Signal" },
  { value: "Surrender", label: "Surrender" },
  { value: "Renewal", label: "Renewal" },
];

const STATUS_OPTIONS = [
  { value: "draft", label: "Draft" },
  { value: "scheduled", label: "Scheduled" },
  { value: "published", label: "Published" },
];

interface IssueForm {
  subject_line: string;
  pillar: string;
  status: string;
  scheduled_send_at: string;
  notes: string;
}

const isDateLike = (s: string): boolean => /^\d{4}-\d{2}-\d{2}/.test(s);

export default function Content() {
  const { toast } = useToast();
  const { data, error, mutate } = useSWR<ContentData>(
    "/api/data/content",
    fetcher,
    { refreshInterval: REFRESH }
  );

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<Issue | null>(null);
  const [form, setForm] = useState<IssueForm>({
    subject_line: "",
    pillar: "Signal",
    status: "draft",
    scheduled_send_at: "",
    notes: "",
  });
  const [saving, setSaving] = useState(false);
  const [newKeyword, setNewKeyword] = useState("");

  if (error) return <ErrorState msg="backend unreachable" />;
  if (!data) return <PageSkeleton />;

  const { issues, seo_topics, episodes } = data;

  const published = issues.filter((i) => i.status === "published").length;
  const queued = seo_topics.filter((s) => s.status === "pending").length;

  function openEdit(issue: Issue) {
    setEditing(issue);
    setForm({
      subject_line: issue.subject_line || "",
      pillar: issue.pillar || "Signal",
      status: issue.status || "draft",
      scheduled_send_at:
        issue.scheduled && isDateLike(issue.scheduled)
          ? issue.scheduled.slice(0, 10)
          : "",
      notes: "",
    });
    setDrawerOpen(true);
  }

  function patch<K extends keyof IssueForm>(key: K, value: IssueForm[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function save() {
    if (!editing) return;
    setSaving(true);
    try {
      await apiPatch("/api/content/issue", {
        number: editing.number,
        fields: {
          subject_line: form.subject_line,
          pillar: form.pillar,
          status: form.status,
          scheduled_send_at: form.scheduled_send_at,
          notes: form.notes,
        },
      });
      toast("Saved", "success");
      mutate();
      setDrawerOpen(false);
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setSaving(false);
    }
  }

  async function addKeyword() {
    const kw = newKeyword.trim();
    if (!kw) return;
    try {
      await apiPost("/api/content/seo-topic", { keyword: kw });
      setNewKeyword("");
      mutate();
      toast("Keyword queued", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    }
  }

  const issueColumns: Column<Issue>[] = [
    {
      header: "#",
      className: "whitespace-nowrap text-ink font-medium tabular-nums",
      render: (r) => r.number,
    },
    {
      header: "Subject",
      render: (r) => (
        <span
          className="block max-w-[22rem] truncate text-ink-soft"
          title={r.subject_line || ""}
        >
          {r.subject_line || "—"}
        </span>
      ),
    },
    {
      header: "Pillar",
      render: (r) => <Badge tone="accent">{r.pillar}</Badge>,
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
      header: "Scheduled",
      className: "whitespace-nowrap tabular-nums",
      render: (r) => fmtDate(r.scheduled),
    },
    {
      header: "Published",
      className: "whitespace-nowrap tabular-nums",
      render: (r) => (r.published ? fmtDate(r.published) : "—"),
    },
    {
      header: "Links",
      className: "whitespace-nowrap",
      render: (r) =>
        r.page_url ? (
          <a
            href={r.page_url}
            target="_blank"
            rel="noreferrer"
            className="text-accent hover:text-accent-ink text-xs font-medium"
          >
            Page ↗
          </a>
        ) : (
          "—"
        ),
    },
    {
      header: "",
      headerClassName: "w-px",
      className: "whitespace-nowrap",
      render: (r) => (
        <div className="flex items-center justify-end">
          <Button
            variant="ghost"
            size="sm"
            icon={<Pencil size={13} />}
            onClick={() => openEdit(r)}
            aria-label="Edit issue"
          />
        </div>
      ),
    },
  ];

  const seoColumns: Column<SeoTopic>[] = [
    {
      header: "Keyword",
      className: "text-ink font-medium",
      render: (r) => r.keyword,
    },
    {
      header: "Status",
      render: (r) => <Badge tone={statusTone(r.status)}>{r.status}</Badge>,
    },
    {
      header: "URL",
      className: "whitespace-nowrap",
      render: (r) =>
        r.url ? (
          <a
            href={r.url}
            target="_blank"
            rel="noreferrer"
            className="text-accent hover:text-accent-ink text-xs font-medium"
          >
            View ↗
          </a>
        ) : (
          "—"
        ),
    },
    {
      header: "Created",
      className: "whitespace-nowrap tabular-nums",
      render: (r) => fmtDate(r.created),
    },
    {
      header: "",
      headerClassName: "w-px",
      className: "whitespace-nowrap",
      render: (r) => (
        <div className="flex items-center justify-end gap-1.5">
          {r.status !== "published" && (
            <ActionButton
              label="Mark published"
              size="sm"
              run={() =>
                apiPatch("/api/content/seo-topic", {
                  keyword: r.keyword,
                  status: "published",
                })
              }
              okMsg="Marked published"
              onDone={() => mutate()}
            />
          )}
          {r.status !== "pending" && (
            <ActionButton
              label="Reset"
              size="sm"
              run={() =>
                apiPatch("/api/content/seo-topic", {
                  keyword: r.keyword,
                  status: "pending",
                })
              }
              okMsg="Reset to pending"
              onDone={() => mutate()}
            />
          )}
          <ActionButton
            label="Delete"
            variant="danger"
            size="sm"
            confirm="Remove this keyword?"
            run={() =>
              apiDelete("/api/content/seo-topic", { keyword: r.keyword })
            }
            okMsg="Keyword removed"
            onDone={() => mutate()}
          />
        </div>
      ),
    },
  ];

  const episodeColumns: Column<Episode>[] = [
    {
      header: "Title",
      render: (r) => (
        <span
          className="block max-w-[24rem] truncate text-ink"
          title={r.title || ""}
        >
          {r.title || "—"}
        </span>
      ),
    },
    {
      header: "Type",
      render: (r) => <Badge tone="muted">{r.type}</Badge>,
    },
    {
      header: "Duration",
      className: "whitespace-nowrap tabular-nums",
      render: (r) => `${r.duration}m`,
    },
    {
      header: "Deployed",
      className: "whitespace-nowrap tabular-nums",
      render: (r) => fmtDate(r.deployed),
    },
    {
      header: "Campaign",
      className: "whitespace-nowrap text-ink-soft",
      render: (r) => r.campaign_a ?? "—",
    },
  ];

  return (
    <>
      <PageHeader
        title="Content"
        sub="Hive Mind newsletter, SEO queue & sleep-audio episodes"
      />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
        <StatCard
          label="Hive Mind issues"
          value={issues.length}
          icon={<Mail size={15} />}
        />
        <StatCard
          label="Published"
          value={published}
          icon={<FileText size={15} />}
          sub="issues live"
        />
        <StatCard
          label="SEO queued"
          value={queued}
          icon={<Search size={15} />}
          sub="pending articles"
        />
        <StatCard
          label="Episodes"
          value={episodes.length}
          icon={<Headphones size={15} />}
        />
      </div>

      <Card className="mb-5">
        <CardHeader
          title="Hive Mind issues"
          sub={`${issues.length} issue${issues.length === 1 ? "" : "s"}`}
        />
        <DataTable<Issue>
          columns={issueColumns}
          rows={issues}
          emptyMessage="No Hive Mind issues yet."
        />
      </Card>

      <Card className="mb-5">
        <CardHeader
          title="SEO topics"
          sub={`${queued} pending`}
          action={
            <div className="flex items-center gap-2">
              <TextInput
                value={newKeyword}
                onChange={(e) => setNewKeyword(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") addKeyword();
                }}
                placeholder="New keyword…"
              />
              <Button variant="primary" size="sm" onClick={addKeyword}>
                Add
              </Button>
            </div>
          }
        />
        <DataTable<SeoTopic>
          columns={seoColumns}
          rows={seo_topics}
          emptyMessage="No SEO topics queued."
        />
      </Card>

      <Card>
        <CardHeader title="Episodes" sub={`${episodes.length} deployed`} />
        <DataTable<Episode>
          columns={episodeColumns}
          rows={episodes}
          emptyMessage="No episodes deployed yet."
        />
      </Card>

      <Drawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={editing ? `Edit issue #${editing.number}` : "Edit issue"}
        sub={editing ? editing.subject_line || undefined : undefined}
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
        <Field label="Subject line">
          <TextInput
            value={form.subject_line}
            onChange={(e) => patch("subject_line", e.target.value)}
            placeholder="Newsletter subject line"
          />
        </Field>
        <Field label="Pillar">
          <Select
            options={PILLAR_OPTIONS}
            value={form.pillar}
            onChange={(e) => patch("pillar", e.target.value)}
          />
        </Field>
        <Field label="Status">
          <Select
            options={STATUS_OPTIONS}
            value={form.status}
            onChange={(e) => patch("status", e.target.value)}
          />
        </Field>
        <Field label="Scheduled send at">
          <TextInput
            type="date"
            value={form.scheduled_send_at}
            onChange={(e) => patch("scheduled_send_at", e.target.value)}
          />
        </Field>
        <Field label="Notes">
          <TextInput
            value={form.notes}
            onChange={(e) => patch("notes", e.target.value)}
            placeholder="Internal notes"
          />
        </Field>
      </Drawer>
    </>
  );
}
