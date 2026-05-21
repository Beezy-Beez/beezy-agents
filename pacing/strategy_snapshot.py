"""
Strategy Snapshot — Mandatory pre-calendar analysis.
Per beezy-system v2.0, this MUST run before any calendar generation.
"""
import json
import os
import time
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Any
import requests

KLAVIYO_API_KEY = (
    os.environ.get("KLAVIYO_PRIVATE_API_KEY")
    or os.environ.get("KLAVIYO_API_KEY")
)
KLAVIYO_BASE = "https://a.klaviyo.com/api"
KLAVIYO_REVISION = "2024-10-15"
# Klaviyo values-report endpoints share a ~1/sec burst limit. Space them.
REPORT_PACING_SECONDS = 1.5

# Klaviyo audiences come back from GET /campaigns/{id} as opaque segment/list IDs.
# Map them to readable names for the Stage 5 recency audit. Source: CLAUDE.md.
SEGMENT_ID_TO_NAME = {
    "Sme9Nq": "super_engaged",
    "Xrp3ha": "engaged_prospects",
    "RArtzN": "vip",
    "UBFUcH": "active_seal",
    "VAUD58": "whales",
    "RvtHdn": "engaged_customers",
    "UEQD6k": "lapsed_30d",
    "UfARWm": "lapsed_60d",
    "XuS7rY": "lapsed_90d",
    "W98qh3": "lapsed_180d",
    "Y6VSre": "hive_mind_list",
    "XFSxZt": "ALL_CUSTOMERS_EXCLUDE",
}

# Locked format taxonomy (from beezy-system v2.0)
FORMAT_PATTERNS = {
    "Members/Engaged Newsletter": ["members & subs", "newsletter issue", "engaged customers | the newsletter"],
    "Prospect Newsletter (Hive Mind)": ["hive mind", "the hive mind issue", "prospects | the"],
    "Sleep Audio": ["sleep story", "sleep audio", "podcast", "meditation", "guided meditation"],
    "Pre-Paid Subscription": ["pre-paid", "pre paid", "prepaid", "12 months pre-paid", "12-month pre-paid"],
    "$25 Credit": ["$25 credit", "$25 sleep credit"],
    "Whale/Long-Term": ["whales", "12-month", "6-month"],
    "VIP": ["vip", "inner circle"],
    "Reactivation": ["lapsed", "reactivation", "winback", "win-back", "win back"],
    "BOGO/Bundle": ["bogo", "b2g1", "b3g1", "b4g2", "buy x", "bundle"],
    "Discount Blast": ["40% off", "30% off", "25% off", "50% off", "sale", "flash"],
    "Product Feature": ["cinnamon", "caramel", "blood orange", "cbn honey", "botanical lotion"],
    "Hive Club": ["hive club", "membership"],
    "Nurture/Relationship": ["just saying hey", "just checking", "founder's note", "founders note"],
    "Named-Moment Campaign": ["the anchor", "the bridge", "ny shipping", "founder's note", "anchor (the high-aov"],
    "SNIPER (Hot List)": ["sniper", "hot click"],
    "Editorial-to-Offer": ["the secret to", "the bridge (the melatonin", "intro to guide", "educational"],
}

def classify_format(name: str) -> str:
    """Classify a campaign name into the locked format taxonomy."""
    n = name.lower()
    for fmt, patterns in FORMAT_PATTERNS.items():
        if any(p in n for p in patterns):
            return fmt
    return "Other"


def _klaviyo_headers() -> dict:
    return {
        "Authorization": f"Klaviyo-API-Key {KLAVIYO_API_KEY}",
        "Content-Type": "application/json",
        "revision": KLAVIYO_REVISION,
        "accept": "application/json",
    }


def klaviyo_post(endpoint: str, payload: dict) -> dict:
    r = requests.post(f"{KLAVIYO_BASE}{endpoint}", headers=_klaviyo_headers(), json=payload, timeout=60)
    r.raise_for_status()
    return r.json()


def klaviyo_get(endpoint: str) -> dict:
    r = requests.get(f"{KLAVIYO_BASE}{endpoint}", headers=_klaviyo_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


# Stats requested from campaign-values-report. Rate fields (open_rate, etc.) and
# value fields (conversion_value, revenue_per_recipient) all live under `statistics`
# in the current API revision — no separate `value_statistics` block.
_CAMPAIGN_STATS = [
    "recipients", "delivered", "opens_unique", "open_rate",
    "clicks_unique", "click_rate", "conversion_rate", "conversion_uniques",
    "conversions", "unsubscribes", "unsubscribe_rate",
    "conversion_value", "revenue_per_recipient",
]

_FLOW_STATS = [
    "recipients", "delivered", "open_rate", "click_rate",
    "conversion_rate", "conversions", "conversion_uniques",
    "unsubscribes", "unsubscribe_rate",
    "conversion_value", "revenue_per_recipient",
]


def pull_campaign_report(start_iso: str, end_iso: str, conversion_metric_id: str = "X93gjq") -> List[dict]:
    """Stage 1a: Pull live campaign data.

    The values-report response only has groupings + statistics; campaign name,
    send_time, and audiences live on GET /campaigns/{id}/. We hydrate per
    unique campaign_id and attach a synthetic `campaign_details` field so the
    downstream normalize_campaigns() can stay unchanged.
    """
    payload = {
        "data": {
            "type": "campaign-values-report",
            "attributes": {
                "statistics": _CAMPAIGN_STATS,
                "timeframe": {"start": start_iso, "end": end_iso},
                "conversion_metric_id": conversion_metric_id,
            }
        }
    }
    result = klaviyo_post("/campaign-values-reports/", payload)
    rows = result.get("data", {}).get("attributes", {}).get("results", []) or []

    # Hydrate per unique campaign_id.
    meta_cache: Dict[str, dict] = {}
    for r in rows:
        cid = (r.get("groupings") or {}).get("campaign_id")
        if not cid or cid in meta_cache:
            continue
        try:
            body = klaviyo_get(f"/campaigns/{cid}/")
            attrs = (body.get("data") or {}).get("attributes") or {}
        except requests.HTTPError as e:
            print(f"[snapshot] WARN: could not hydrate campaign {cid}: {e}")
            attrs = {}
        included_ids = ((attrs.get("audiences") or {}).get("included") or [])
        meta_cache[cid] = {
            "name": attrs.get("name") or "",
            "sendTime": attrs.get("send_time"),
            "audiences": {
                "included": [
                    {"name": SEGMENT_ID_TO_NAME.get(sid, sid)}
                    for sid in included_ids
                ],
            },
        }

    # Attach details on each row so normalize_campaigns() finds them.
    for r in rows:
        cid = (r.get("groupings") or {}).get("campaign_id")
        if cid and cid in meta_cache:
            r["campaign_details"] = {"attributes": meta_cache[cid]}

    return rows


def pull_flow_report(start_iso: str, end_iso: str, conversion_metric_id: str = "X93gjq") -> List[dict]:
    """Stage 1b: Pull live flow data. No hydration — only used for MTD revenue sum."""
    payload = {
        "data": {
            "type": "flow-values-report",
            "attributes": {
                "statistics": _FLOW_STATS,
                "timeframe": {"start": start_iso, "end": end_iso},
                "conversion_metric_id": conversion_metric_id,
            }
        }
    }
    result = klaviyo_post("/flow-values-reports/", payload)
    return result.get("data", {}).get("attributes", {}).get("results", []) or []


def normalize_campaigns(raw: List[dict]) -> List[dict]:
    """Extract relevant fields, sort by send date."""
    rows = []
    for c in raw:
        details = c.get("campaign_details", {}).get("attributes", {})
        stats = c.get("statistics", {})
        send_time = details.get("sendTime") or details.get("scheduledAt")
        if not send_time:
            continue
        send_dt = datetime.fromisoformat(send_time.replace("Z", "+00:00"))
        audiences = [a.get("name", "") for a in details.get("audiences", {}).get("included", []) if a]
        rows.append({
            "name": details.get("name", ""),
            "send_dt": send_dt,
            "send_date": send_dt.strftime("%Y-%m-%d"),
            "audiences": audiences,
            "recipients": stats.get("recipients", 0),
            "open_rate": stats.get("open_rate", 0),
            "conversion_rate": stats.get("conversion_rate", 0),
            "unsubscribe_rate": stats.get("unsubscribe_rate", 0),
            "revenue": stats.get("conversion_value", 0),
            "rpr": stats.get("revenue_per_recipient", 0),
            "format": classify_format(details.get("name", "")),
        })
    rows.sort(key=lambda r: r["send_dt"])
    return rows


def stage_2_bucket(rows: List[dict], today: datetime) -> Dict[str, dict]:
    """Stage 2: Bucket campaigns by time windows."""
    buckets = {"Last 30d": [], "31-90d": [], "91-180d": []}
    for r in rows:
        days = (today - r["send_dt"]).days
        if days <= 30:
            buckets["Last 30d"].append(r)
        elif days <= 90:
            buckets["31-90d"].append(r)
        elif days <= 180:
            buckets["91-180d"].append(r)

    summary = {}
    for label, rs in buckets.items():
        n = len(rs)
        total_rec = sum(r["recipients"] for r in rs)
        total_rev = sum(r["revenue"] for r in rs)
        total_unsub_rec = sum(r["recipients"] * r["unsubscribe_rate"] for r in rs)
        weighted_or = sum(r["open_rate"] * r["recipients"] for r in rs) / total_rec if total_rec else 0
        days_in_bucket = 30 if label == "Last 30d" else (60 if label == "31-90d" else 90)
        summary[label] = {
            "campaigns": n,
            "recipients": total_rec,
            "revenue": round(total_rev, 2),
            "avg_open_rate": round(weighted_or, 4),
            "avg_rpr": round(total_rev / total_rec, 4) if total_rec else 0,
            "avg_unsubscribe_rate": round(total_unsub_rec / total_rec, 5) if total_rec else 0,
            "revenue_per_day": round(total_rev / days_in_bucket, 2),
            "avg_recipients_per_send": round(total_rec / n) if n else 0,
        }
    return summary


def stage_3_format_classification(rows: List[dict]) -> Dict[str, List[dict]]:
    """Stage 3: Group by format. Already classified during normalization."""
    by_format = defaultdict(list)
    for r in rows:
        by_format[r["format"]].append(r)
    return dict(by_format)


def stage_4_whats_working(rows: List[dict], today: datetime) -> dict:
    """Stage 4: Compute the 'what's working' report."""
    # RPR trajectory per format
    fmt_buckets = defaultdict(lambda: defaultdict(list))
    for r in rows:
        days = (today - r["send_dt"]).days
        bucket = "Last 30d" if days <= 30 else ("31-90d" if days <= 90 else ("91-180d" if days <= 180 else None))
        if bucket:
            fmt_buckets[r["format"]][bucket].append(r)

    trajectories = {}
    for fmt, buckets in fmt_buckets.items():
        traj = {}
        for b in ["Last 30d", "31-90d", "91-180d"]:
            rs = buckets.get(b, [])
            if rs:
                total_rec = sum(r["recipients"] for r in rs)
                total_rev = sum(r["revenue"] for r in rs)
                traj[b] = {
                    "sends": len(rs),
                    "rpr": round(total_rev / total_rec, 4) if total_rec else 0,
                    "revenue": round(total_rev, 2),
                }
        # Classify trend
        rpr_30 = traj.get("Last 30d", {}).get("rpr", 0)
        rpr_180 = traj.get("91-180d", {}).get("rpr", 0)
        if rpr_180 > 0:
            change_pct = (rpr_30 - rpr_180) / rpr_180 * 100
            traj["trend"] = "improving" if change_pct > 10 else ("declining" if change_pct < -10 else "stable")
            traj["change_pct"] = round(change_pct, 1)
        else:
            traj["trend"] = "new"
        trajectories[fmt] = traj

    # Top 25 RPR last 60d (min 200 recipients)
    recent_60 = [r for r in rows if (today - r["send_dt"]).days <= 60 and r["recipients"] >= 200]
    top_rpr = sorted(recent_60, key=lambda r: -r["rpr"])[:25]
    top_revenue = sorted(recent_60, key=lambda r: -r["revenue"])[:25]

    # Abandoned winners (>=$1000 in 91-180d, NOT seen in last 60d as a similar format)
    older_winners = [r for r in rows if 91 <= (today - r["send_dt"]).days <= 180 and r["revenue"] >= 1000]
    recent_60_formats = {r["format"] for r in recent_60}
    abandoned = [r for r in older_winners if r["format"] not in recent_60_formats]

    return {
        "format_trajectories": trajectories,
        "top_25_rpr_last_60d": [{
            "date": r["send_date"], "name": r["name"], "format": r["format"],
            "recipients": r["recipients"], "revenue": round(r["revenue"], 2), "rpr": round(r["rpr"], 3)
        } for r in top_rpr],
        "top_25_revenue_last_60d": [{
            "date": r["send_date"], "name": r["name"], "format": r["format"],
            "recipients": r["recipients"], "revenue": round(r["revenue"], 2), "rpr": round(r["rpr"], 3)
        } for r in top_revenue],
        "abandoned_winners": [{
            "date": r["send_date"], "name": r["name"], "format": r["format"],
            "revenue": round(r["revenue"], 2)
        } for r in sorted(abandoned, key=lambda r: -r["revenue"])[:15]],
    }


def stage_5_recency_audit(rows: List[dict], today: datetime, lookback_days: int = 21) -> dict:
    """Stage 5a: Build audience → last touch map."""
    audience_touches = defaultdict(list)
    for r in rows:
        days = (today - r["send_dt"]).days
        if days > lookback_days:
            continue
        for a in r["audiences"]:
            audience_touches[a].append({
                "date": r["send_date"],
                "days_ago": days,
                "campaign": r["name"][:80],
                "revenue": round(r["revenue"], 2),
                "rpr": round(r["rpr"], 3),
            })

    audit = {}
    for aud, touches in audience_touches.items():
        touches.sort(key=lambda t: t["days_ago"])
        most_recent_days = touches[0]["days_ago"] if touches else 999
        count_last_14d = sum(1 for t in touches if t["days_ago"] <= 14)

        # Status per beezy-system v2.0
        if most_recent_days >= 7 and count_last_14d <= 2:
            status = "FRESH"
        elif most_recent_days >= 4 and count_last_14d <= 3:
            status = "WARM"
        else:
            status = "REST"

        audit[aud] = {
            "touches_last_14d": count_last_14d,
            "last_touch_days_ago": most_recent_days,
            "status": status,
            "recent_touches": touches[:5],
        }
    return audit


def run_snapshot(today: datetime = None) -> dict:
    """Main entry: Run the full 6-stage Strategy Snapshot."""
    if today is None:
        today = datetime.now()
    today = today.replace(tzinfo=None) if today.tzinfo else today

    # Time windows
    end_iso = today.strftime("%Y-%m-%dT%H:%M:%SZ")
    start_180d_iso = (today - timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ")
    mtd_start_iso = today.replace(day=1, hour=0, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Stage 1: Pull live data
    print(f"[snapshot] Pulling 180d campaign data ({start_180d_iso} → {end_iso})")
    campaigns_raw = pull_campaign_report(start_180d_iso, end_iso)
    rows = normalize_campaigns(campaigns_raw)
    # Strip tzinfo for comparison
    for r in rows:
        r["send_dt"] = r["send_dt"].replace(tzinfo=None)

    print(f"[snapshot] Pulling MTD flow data ({mtd_start_iso} → {end_iso})")
    time.sleep(REPORT_PACING_SECONDS)  # Klaviyo rate-limits values-reports ~1/sec
    flows_mtd_raw = pull_flow_report(mtd_start_iso, end_iso)
    flows_mtd_total = sum(f.get("statistics", {}).get("conversion_value", 0) for f in flows_mtd_raw)

    campaigns_mtd_total = sum(r["revenue"] for r in rows if r["send_dt"] >= today.replace(day=1, hour=0, minute=0, second=0))

    days_elapsed_in_month = today.day
    days_remaining_in_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1).day  # crude last-day calc
    # Better last-day-of-month calc:
    import calendar
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_remaining = days_in_month - today.day

    # Stage 2: Bucket
    print("[snapshot] Stage 2: Time bucket analysis")
    buckets = stage_2_bucket(rows, today)

    # Stage 3: Format classification (already done in normalize)

    # Stage 4: What's working
    print("[snapshot] Stage 4: 'What's working' report")
    whats_working = stage_4_whats_working(rows, today)

    # Stage 5: Recency audit
    print("[snapshot] Stage 5: Recency audit")
    recency = stage_5_recency_audit(rows, today)

    # Compose final snapshot
    snapshot = {
        "generated_at": today.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "live_baseline": {
            "campaigns_mtd": round(campaigns_mtd_total, 2),
            "flows_mtd": round(flows_mtd_total, 2),
            "attributed_mtd": round(campaigns_mtd_total + flows_mtd_total, 2),
            "campaigns_daily_pace": round(campaigns_mtd_total / days_elapsed_in_month, 2),
            "flows_daily_pace": round(flows_mtd_total / days_elapsed_in_month, 2),
            "days_remaining_in_month": days_remaining,
            "projected_exit_at_current_pace": round(
                (campaigns_mtd_total + flows_mtd_total) * (days_in_month / days_elapsed_in_month), 2
            ),
        },
        "time_buckets": buckets,
        "format_trajectories": whats_working["format_trajectories"],
        "top_25_rpr_last_60d": whats_working["top_25_rpr_last_60d"],
        "top_25_revenue_last_60d": whats_working["top_25_revenue_last_60d"],
        "abandoned_winners": whats_working["abandoned_winners"],
        "recency_audit": recency,
        "locked_rules_active": [
            "Sleep Audio to Engaged Customers: every 7-10 days (rotation)",
            "Sleep Audio to Active Seal: min 5-day gap",
            "Members & Subs Newsletter: every 5-7 days (NOT every 3)",
            "Active Seal: max 3 touches per 10-day window, min 4-day gaps",
            "All Engaged Customers: max 3 quality touches per 10-day window",
            "Hive Mind (prospects): every 3 days",
            "$25 Credit per lapsed cohort: max once per 14 days",
            "Named-moment campaigns: AT LEAST 1 per week, no more than 2 consecutive same hook",
            "Tuesday rotation: Anchor / Buy X Get Y / Editorial / SNIPER",
            "No mass discount blasts to 40K+ in normal calendar (holiday/launch exception only)",
            "Sleep Audio pages MUST use beezy-sleep-story-page v2.0 (Bridge template, no transcript, no Hive Mind gate, no episode- prefix)",
        ],
    }
    return snapshot


def save_snapshot(snapshot: dict, path: str = None) -> str:
    """Save snapshot to disk for calendar.py consumption."""
    if path is None:
        ts = snapshot["generated_at"].replace(":", "-").replace("T", "_")
        path = f"pacing/snapshots/snapshot_{ts}.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2, default=str)
    return path


if __name__ == "__main__":
    snap = run_snapshot()
    path = save_snapshot(snap)
    print(f"\n[snapshot] Saved to {path}")
    print(f"\nLIVE BASELINE:")
    print(f"  Campaigns MTD: ${snap['live_baseline']['campaigns_mtd']:,.2f}")
    print(f"  Flows MTD: ${snap['live_baseline']['flows_mtd']:,.2f}")
    print(f"  Attributed MTD: ${snap['live_baseline']['attributed_mtd']:,.2f}")
    print(f"  Daily pace: Campaigns ${snap['live_baseline']['campaigns_daily_pace']}/day, Flows ${snap['live_baseline']['flows_daily_pace']}/day")
    print(f"  Days remaining: {snap['live_baseline']['days_remaining_in_month']}")
    print(f"  Projected exit at current pace: ${snap['live_baseline']['projected_exit_at_current_pace']:,.2f}")
