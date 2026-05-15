export type PacingStatus = "AHEAD" | "ON TRACK" | "BEHIND";

export interface Pacing {
  rev: number;
  goal: number;
  pct: number;
  cr: number;
  fr: number;
  cc: number;
  days_elapsed: number;
  days_left: number;
  daily_needed: number;
  daily_actual: number;
  forecast: number;
  status: PacingStatus;
  as_of: string | null;
  stale: boolean;
}

export type ContentType =
  | "klaviyo_campaign"
  | "sniper_followup"
  | "hive_mind"
  | "seo_blog"
  | "sleep_audio"
  | "sms_campaign"
  | "flow_experiment";

export type SlotStatus =
  | "planned"
  | "dispatched"
  | "completed"
  | "failed"
  | "blocked"
  | "not_sent"
  | "pending"
  | "cancelled"
  | "skipped";

export interface TodaySlot {
  id: string;
  t: ContentType;
  a: string;
  tp: string;
  s: SlotStatus;
  n: string;
  rv: number;
  kid: string;
}

export interface CalendarSlot {
  date: string;
  t: ContentType;
  a: string;
  tp: string;
  tm: string;
  rv: number;
  actual_rev: number;
  status: SlotStatus;
  exec_id: string;
  kid: string;
}

export interface Approval {
  week_approved: boolean;
  week_start: string | null;
  month_has_plan: boolean;
  upcoming_count: number;
  total_estimated_rev: number;
}

export interface OverviewData {
  pacing: Pacing;
  today_slots: TodaySlot[];
  next_send: string;
  approval: Approval;
}

export interface CalendarData {
  slots: CalendarSlot[];
  approval: Approval;
}

export interface AudienceHealth {
  audience: string;
  last_send: string;
  days_since: number;
  rpr_90d: number;
  rpr_30d: number;
  sends_90d: number;
  health: "FRESH" | "WARM" | "RECENT";
}

export interface AudiencesData {
  health: AudienceHealth[];
  burn_list: string[];
}

export interface TopPerformer {
  a: string;
  t: ContentType;
  rv: number;
  rpr: number;
  d: string;
}

export interface LearningLoop {
  entries: Array<{ component: string; summary: string; at: string }>;
  rpr_by_audience: Record<string, number>;
}

export interface RevenueTrend {
  date: string;
  revenue: number;
}

export interface AnalyticsData {
  top_performers: TopPerformer[];
  learning: LearningLoop;
  revenue_trend: RevenueTrend[];
}

export interface FlowAnalysis {
  name: string;
  revenue: number;
  rpr: number;
  severity: "ok" | "warn" | "critical";
  fix_queued: boolean;
  flow_id?: string;
}

export interface FlowsData {
  analyses: FlowAnalysis[];
  _checked_at: string;
}

export interface Issue {
  number: number;
  subject_line: string;
  pillar: string;
  status: "draft" | "scheduled" | "published";
  page_url: string | null;
  cover_url: string | null;
  campaign_id: string | null;
  scheduled: string;
  published: string;
}

export interface SeoTopic {
  keyword: string;
  status: "pending" | "published" | "error";
  url: string | null;
  error: string | null;
  created: string;
}

export interface Episode {
  title: string;
  type: string;
  url: string | null;
  duration: number;
  deployed: string;
  campaign_a: string | null;
}

export interface ContentData {
  issues: Issue[];
  seo_topics: SeoTopic[];
  episodes: Episode[];
}

export interface CronSentinel {
  value: string;
  updated: string;
}

export interface RecentRun {
  id: string;
  worker: string;
  status: string;
  cost: number;
  elapsed: number;
  created: string;
}

export interface SystemData {
  cron_sentinels: Record<string, CronSentinel>;
  recent_runs: RecentRun[];
  env_status: Record<string, boolean>;
  db_ok: boolean;
}

export interface StoreRevenue {
  store_mtd: number;
  order_count: number;
  aov: number;
  attributed: number;
  campaign_rev: number;
  flow_rev: number;
  pct_attributed: number;
  store_trend: RevenueTrend[];
  goal: number;
}

export interface BusinessData {
  store: StoreRevenue;
  pacing: Pacing;
}

export interface PacingPoint {
  date: string;
  actual: number;
  target: number;
  gap_pct: number;
  required_daily: number;
}

export interface PacingHistoryData {
  history: PacingPoint[];
}

export interface Deliverability {
  _source: string;
  _checked_at?: string;
  recipients?: number;
  deliveries?: number;
  bounce_rate?: number;
  unsub_rate?: number;
  delivery_rate?: number;
  spam_rate?: number;
  [k: string]: unknown;
}
