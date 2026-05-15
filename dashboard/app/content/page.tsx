"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import type { ContentData, Issue, SeoTopic, Episode } from "@/lib/types";
import { useState } from "react";

const ISSUE_STATUS_CONFIG = {
  draft:     { label: "Draft",     bg: "#888" },
  scheduled: { label: "Scheduled", bg: "#1a73e8" },
  published: { label: "Published", bg: "#1e7e34" },
};

const SEO_STATUS_CONFIG = {
  pending:   { label: "Pending",   bg: "#e07b00" },
  published: { label: "Published", bg: "#1e7e34" },
  error:     { label: "Error",     bg: "#c0392b" },
};

const PILLAR_CONFIG: Record<string, string> = {
  Signal:    "#7b2d8b",
  Surrender: "#0e7c7b",
  Renewal:   "#1a73e8",
};

const TABS = ["Hive Mind", "SEO Topics", "Episodes"] as const;
type Tab = typeof TABS[number];

function IssueRow({ issue }: { issue: Issue }) {
  const s = ISSUE_STATUS_CONFIG[issue.status] ?? { label: issue.status, bg: "#888" };
  const pillarColor = PILLAR_CONFIG[issue.pillar] ?? "#8b7355";
  return (
    <tr className="border-b border-[#f0ece4] last:border-0 hover:bg-[#fdf8f2] transition-colors">
      <td className="px-4 py-3 font-bold text-sm text-[#8b4513]">
        #{issue.number.toString().padStart(3, "0")}
      </td>
      <td className="px-3 py-3 text-sm max-w-[240px]">
        <span className="block truncate font-medium">{issue.subject_line}</span>
      </td>
      <td className="px-3 py-3">
        <span
          className="inline-block px-2 py-0.5 rounded-full text-white text-[11px] font-semibold"
          style={{ background: pillarColor }}
        >
          {issue.pillar}
        </span>
      </td>
      <td className="px-3 py-3">
        <span
          className="inline-block px-2 py-0.5 rounded-full text-white text-[11px] font-semibold"
          style={{ background: s.bg }}
        >
          {s.label}
        </span>
      </td>
      <td className="px-3 py-3 text-sm text-gray-500">
        {issue.scheduled || issue.published || "—"}
      </td>
      <td className="px-3 py-3">
        <div className="flex items-center gap-2">
          {issue.page_url && (
            <a
              href={issue.page_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-[#8b4513] hover:underline"
            >
              Page ↗
            </a>
          )}
          {issue.campaign_id && (
            <a
              href={`https://www.klaviyo.com/campaign/${issue.campaign_id}/edit`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-gray-400 hover:text-[#8b4513]"
            >
              Klaviyo ↗
            </a>
          )}
        </div>
      </td>
    </tr>
  );
}

function SeoRow({ topic }: { topic: SeoTopic }) {
  const s = SEO_STATUS_CONFIG[topic.status] ?? { label: topic.status, bg: "#888" };
  return (
    <tr className="border-b border-[#f0ece4] last:border-0 hover:bg-[#fdf8f2] transition-colors">
      <td className="px-4 py-3 text-sm font-medium max-w-[280px]">
        <span className="block truncate">{topic.keyword}</span>
      </td>
      <td className="px-3 py-3">
        <span
          className="inline-block px-2 py-0.5 rounded-full text-white text-[11px] font-semibold"
          style={{ background: s.bg }}
        >
          {s.label}
        </span>
      </td>
      <td className="px-3 py-3 text-sm">
        {topic.url ? (
          <a
            href={topic.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[#8b4513] hover:underline text-xs"
          >
            View ↗
          </a>
        ) : topic.error ? (
          <span className="text-xs text-[#c0392b]" title={topic.error}>
            Error
          </span>
        ) : (
          <span className="text-xs text-gray-400">—</span>
        )}
      </td>
      <td className="px-3 py-3 text-xs text-gray-400">{topic.created}</td>
    </tr>
  );
}

function EpisodeRow({ episode }: { episode: Episode }) {
  const typeLabel = episode.type
    ?.split("_")
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ") ?? episode.type;

  return (
    <tr className="border-b border-[#f0ece4] last:border-0 hover:bg-[#fdf8f2] transition-colors">
      <td className="px-4 py-3 text-sm font-medium max-w-[240px]">
        <span className="block truncate">{episode.title}</span>
      </td>
      <td className="px-3 py-3">
        <span className="inline-block px-2 py-0.5 rounded-full bg-[#0e7c7b] text-white text-[11px] font-semibold">
          {typeLabel}
        </span>
      </td>
      <td className="px-3 py-3 text-sm text-gray-500">
        {episode.duration ? `${episode.duration}min` : "—"}
      </td>
      <td className="px-3 py-3 text-xs text-gray-400">{episode.deployed || "—"}</td>
      <td className="px-3 py-3">
        <div className="flex items-center gap-2">
          {episode.url && (
            <a
              href={episode.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-[#8b4513] hover:underline"
            >
              Page ↗
            </a>
          )}
          {episode.campaign_a && (
            <a
              href={`https://www.klaviyo.com/campaign/${episode.campaign_a}/edit`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-gray-400 hover:text-[#8b4513]"
            >
              Klaviyo ↗
            </a>
          )}
        </div>
      </td>
    </tr>
  );
}

export default function ContentPage() {
  const { data, error } = useSWR<ContentData>("/api/data/content", fetcher, {
    refreshInterval: 30_000,
  });
  const [tab, setTab] = useState<Tab>("Hive Mind");

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-red-700">
        Failed to load content data: {error.message}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-10 w-64 rounded-xl" />
        <div className="skeleton h-12 w-full rounded-xl" />
        <div className="skeleton h-[400px] w-full rounded-xl" />
      </div>
    );
  }

  const { issues, seo_topics, episodes } = data;

  const tabCounts: Record<Tab, number> = {
    "Hive Mind": issues.length,
    "SEO Topics": seo_topics.length,
    Episodes: episodes.length,
  };

  return (
    <div className="space-y-5">
      <h1
        className="text-2xl font-bold text-[#8b4513]"
        style={{ fontFamily: "var(--font-dm-serif)" }}
      >
        Content
      </h1>

      {/* Tabs */}
      <div className="flex gap-1 bg-[#f0ece4] p-1 rounded-xl w-fit">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-5 py-2 rounded-lg text-sm font-semibold transition-colors ${
              tab === t
                ? "bg-white text-[#8b4513] shadow-sm"
                : "text-[#8b7355] hover:text-[#2c2417]"
            }`}
          >
            {t}
            <span
              className={`ml-1.5 text-[11px] px-1.5 py-0.5 rounded-full ${
                tab === t ? "bg-[#faf6ee] text-[#8b4513]" : "bg-white/50 text-gray-500"
              }`}
            >
              {tabCounts[t]}
            </span>
          </button>
        ))}
      </div>

      {/* Hive Mind tab */}
      {tab === "Hive Mind" && (
        <div className="bg-white rounded-xl border border-[#e8dcc8] shadow-sm overflow-hidden">
          <div className="px-5 py-4 border-b border-[#e8dcc8] flex items-center justify-between">
            <h2 className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355]">
              Hive Mind Issues
            </h2>
            <span className="text-xs text-gray-400">3-pillar newsletter · every 3 days</span>
          </div>
          {issues.length === 0 ? (
            <div className="p-8 text-center text-gray-400 italic text-sm">
              No issues found.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="border-b-2 border-[#e8dcc8]">
                    <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-4 py-3">
                      #
                    </th>
                    <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                      Subject
                    </th>
                    <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                      Pillar
                    </th>
                    <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                      Status
                    </th>
                    <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                      Date
                    </th>
                    <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                      Links
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {issues.map((issue) => (
                    <IssueRow key={issue.number} issue={issue} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* SEO Topics tab */}
      {tab === "SEO Topics" && (
        <div className="bg-white rounded-xl border border-[#e8dcc8] shadow-sm overflow-hidden">
          <div className="px-5 py-4 border-b border-[#e8dcc8] flex items-center justify-between">
            <h2 className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355]">
              SEO Topic Queue
            </h2>
            <span className="text-xs text-gray-400">2,000-word articles · Tier 1 auto</span>
          </div>
          {seo_topics.length === 0 ? (
            <div className="p-8 text-center text-gray-400 italic text-sm">
              No SEO topics in queue.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="border-b-2 border-[#e8dcc8]">
                    <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-4 py-3">
                      Keyword
                    </th>
                    <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                      Status
                    </th>
                    <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                      Published URL
                    </th>
                    <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                      Created
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {seo_topics.map((topic, i) => (
                    <SeoRow key={i} topic={topic} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Episodes tab */}
      {tab === "Episodes" && (
        <div className="bg-white rounded-xl border border-[#e8dcc8] shadow-sm overflow-hidden">
          <div className="px-5 py-4 border-b border-[#e8dcc8] flex items-center justify-between">
            <h2 className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355]">
              Sleep Audio Episodes
            </h2>
            <span className="text-xs text-gray-400">Deep Bear Sleep · every 3 days</span>
          </div>
          {episodes.length === 0 ? (
            <div className="p-8 text-center text-gray-400 italic text-sm">
              No episodes deployed yet.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="border-b-2 border-[#e8dcc8]">
                    <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-4 py-3">
                      Title
                    </th>
                    <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                      Type
                    </th>
                    <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                      Duration
                    </th>
                    <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                      Deployed
                    </th>
                    <th className="text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-3">
                      Links
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {episodes.map((ep, i) => (
                    <EpisodeRow key={i} episode={ep} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
