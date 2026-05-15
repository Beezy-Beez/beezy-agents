// Formatting + label maps. Mirrors CONTENT_LABEL / AUDIENCE_LABEL in the backend
// so operators never see raw slugs.

export const money = (n: number | null | undefined, dp = 0): string =>
  n == null
    ? "—"
    : `$${Number(n).toLocaleString("en-US", {
        minimumFractionDigits: dp,
        maximumFractionDigits: dp,
      })}`;

export const moneyK = (n: number | null | undefined): string => {
  if (n == null) return "—";
  const v = Number(n);
  if (Math.abs(v) >= 1000) return `$${(v / 1000).toFixed(1)}k`;
  return `$${v.toFixed(0)}`;
};

export const num = (n: number | null | undefined): string =>
  n == null ? "—" : Number(n).toLocaleString("en-US");

export const pct = (n: number | null | undefined, dp = 1): string =>
  n == null ? "—" : `${Number(n).toFixed(dp)}%`;

export const rpr = (n: number | null | undefined): string =>
  n == null ? "—" : `$${Number(n).toFixed(3)}`;

export function fmtDate(s?: string | null): string {
  if (!s) return "—";
  const d = new Date(s + (s.length === 10 ? "T00:00:00" : ""));
  if (isNaN(d.getTime())) return s;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function fmtTime(t?: string): string {
  if (!t) return "";
  const [h, m] = t.split(":").map(Number);
  if (isNaN(h)) return t;
  const ap = h >= 12 ? "pm" : "am";
  const hr = h % 12 || 12;
  return `${hr}:${String(m || 0).padStart(2, "0")}${ap}`;
}

export const CONTENT_LABEL: Record<string, string> = {
  klaviyo_campaign: "Email Campaign",
  sniper_followup: "Sniper Follow-up",
  hive_mind: "Hive Mind",
  seo_blog: "SEO Article",
  sleep_audio: "Sleep Audio",
  sms_campaign: "SMS",
  flow_experiment: "Flow Experiment",
};

export const AUDIENCE_LABEL: Record<string, string> = {
  lapsed_30d: "Lapsed 30d",
  lapsed_60d: "Lapsed 60d",
  lapsed_60_90d: "Lapsed 60–90d",
  lapsed_90d: "Lapsed 90d",
  lapsed_90_180d: "Lapsed 90–180d",
  lapsed_180d: "Lapsed 180d",
  lapsed_180d_plus: "Lapsed 180d+",
  winback_180d: "Winback 180d",
  vip: "VIP",
  inner_circle: "Inner Circle",
  engaged_customers: "Engaged Customers",
  all_customers: "All Customers",
  active_seal: "Active Seal",
  active_subscribers: "Active Subscribers",
  whales: "Whales",
  high_aov: "High AOV",
  one_time_buyers: "One-Time Buyers",
  otb: "One-Time Buyers",
  cart_abandoners: "Cart Abandoners",
  engaged_prospects: "Engaged Prospects",
  super_engaged: "Super Engaged",
  hive_mind_prospects: "Hive Mind Prospects",
  prospect_list: "Prospect List",
};

export const label = (
  map: Record<string, string>,
  key: string
): string =>
  map[key] ||
  (key
    ? key
        .replace(/_/g, " ")
        .replace(/\b\w/g, (c) => c.toUpperCase())
    : "—");

export const ctLabel = (k: string) => label(CONTENT_LABEL, k);
export const audLabel = (k: string) => label(AUDIENCE_LABEL, k);

export type Tone = "good" | "warn" | "bad" | "accent" | "muted";

export function statusTone(s: string): Tone {
  const v = (s || "").toLowerCase();
  if (["completed", "published", "sent", "dispatched", "ok", "success"].includes(v))
    return "good";
  if (["failed", "error", "blocked", "not_sent", "critical"].includes(v))
    return "bad";
  if (["pending", "planned", "draft", "scheduled", "warn", "warning"].includes(v))
    return "warn";
  return "muted";
}
