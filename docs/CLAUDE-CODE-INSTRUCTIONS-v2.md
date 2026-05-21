# Claude Code Implementation Instructions — beezy-agents-ingestion v2.0

**Generated:** May 21, 2026  
**Project:** beezy-agents-ingestion (Replit)  
**Working dir:** `/home/runner/workspace/`

This document is the implementation playbook for Claude Code to execute in the Replit shell. Each task is independent and can be done sequentially. Test after each.

---

## Task 1: Create `pacing/strategy_snapshot.py` (NEW MODULE)

**Purpose:** Run the 6-stage Strategy Snapshot before any calendar generation. Output JSON artifact consumed by calendar.py.

**File:** `pacing/strategy_snapshot.py`

```python
"""
Strategy Snapshot — Mandatory pre-calendar analysis.
Per beezy-system v2.0, this MUST run before any calendar generation.
"""
import json
import os
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Any
import requests

KLAVIYO_API_KEY = os.environ.get("KLAVIYO_PRIVATE_API_KEY")
KLAVIYO_BASE = "https://a.klaviyo.com/api"
KLAVIYO_REVISION = "2024-10-15"

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


def klaviyo_post(endpoint: str, payload: dict) -> dict:
    headers = {
        "Authorization": f"Klaviyo-API-Key {KLAVIYO_API_KEY}",
        "Content-Type": "application/json",
        "revision": KLAVIYO_REVISION,
        "accept": "application/json",
    }
    r = requests.post(f"{KLAVIYO_BASE}{endpoint}", headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()


def pull_campaign_report(start_iso: str, end_iso: str, conversion_metric_id: str = "X93gjq") -> List[dict]:
    """Stage 1a: Pull live campaign data."""
    payload = {
        "data": {
            "type": "campaign-values-report",
            "attributes": {
                "statistics": ["recipients", "delivered", "opens_unique", "open_rate", "clicks_unique",
                               "click_rate", "conversion_rate", "conversion_uniques", "conversions",
                               "unsubscribes", "unsubscribe_rate"],
                "value_statistics": ["conversion_value", "average_order_value", "revenue_per_recipient"],
                "filter": f"and(equals(send_channel,\"email\"),greater-or-equal(send_time,{start_iso}),less-or-equal(send_time,{end_iso}))",
                "group_by": ["campaign_id", "campaign_message_id", "send_channel"],
                "conversion_metric_id": conversion_metric_id,
            }
        }
    }
    result = klaviyo_post("/campaign-values-reports/", payload)
    return result.get("data", {}).get("attributes", {}).get("results", [])


def pull_flow_report(start_iso: str, end_iso: str, conversion_metric_id: str = "X93gjq") -> List[dict]:
    """Stage 1b: Pull live flow data."""
    payload = {
        "data": {
            "type": "flow-values-report",
            "attributes": {
                "statistics": ["recipients", "delivered", "open_rate", "click_rate",
                               "conversion_rate", "conversions", "conversion_uniques",
                               "unsubscribes", "unsubscribe_rate"],
                "value_statistics": ["conversion_value", "average_order_value", "revenue_per_recipient"],
                "filter": f"and(equals(send_channel,\"email\"),greater-or-equal(send_time,{start_iso}),less-or-equal(send_time,{end_iso}))",
                "group_by": ["flow_id", "send_channel"],
                "conversion_metric_id": conversion_metric_id,
            }
        }
    }
    result = klaviyo_post("/flow-values-reports/", payload)
    return result.get("data", {}).get("attributes", {}).get("results", [])


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
```

**Test:**
```bash
cd /home/runner/workspace
python -m pacing.strategy_snapshot
```

Expected: outputs live baseline numbers and saves JSON to `pacing/snapshots/`.

---

## Task 2: Modify `pacing/calendar.py` to consume snapshot

**File:** `pacing/calendar.py`

**Change required:** Before calling Opus to generate the calendar, FIRST call `strategy_snapshot.run_snapshot()` and INCLUDE the snapshot data in the Opus prompt as required context. The Opus prompt must reference the snapshot.

**Pseudocode for the modification:**

```python
# At top of file
from pacing.strategy_snapshot import run_snapshot, save_snapshot

def generate_calendar(start_date, end_date, revenue_goal=None):
    # NEW: Mandatory snapshot
    snapshot = run_snapshot()
    snapshot_path = save_snapshot(snapshot)
    print(f"[calendar] Snapshot generated: {snapshot_path}")

    # Existing Opus prompt assembly
    opus_prompt = f"""
You are the Beezy Beez Strategist (Paperclip). Generate a campaign calendar.

REVENUE BASELINE (LIVE FROM KLAVIYO):
- Campaigns MTD: ${snapshot['live_baseline']['campaigns_mtd']:,.2f}
- Flows MTD: ${snapshot['live_baseline']['flows_mtd']:,.2f}
- Daily pace: Campaigns ${snapshot['live_baseline']['campaigns_daily_pace']}/day
- Days remaining: {snapshot['live_baseline']['days_remaining_in_month']}
- Revenue goal: ${revenue_goal or 'as stated by Alan'}

FORMAT TRAJECTORIES (RPR trends):
{format_trajectories_table(snapshot['format_trajectories'])}

TOP RPR LAST 60d (use these as reference):
{top_rpr_table(snapshot['top_25_rpr_last_60d'][:10])}

RECENCY AUDIT (status per audience):
{recency_audit_table(snapshot['recency_audit'])}

ABANDONED WINNERS (formats earning $1K+ that haven't been used recently):
{abandoned_winners_table(snapshot['abandoned_winners'])}

LOCKED RULES (must enforce):
{chr(10).join(f"- {r}" for r in snapshot['locked_rules_active'])}

GENERATE THE CALENDAR from {start_date} to {end_date}. For each slot, cite the snapshot data point that justifies it.
"""
    # Continue with existing Opus call...
    response = call_opus(opus_prompt)
    # ...
```

**Helper functions to add:** `format_trajectories_table()`, `top_rpr_table()`, `recency_audit_table()`, `abandoned_winners_table()` — each formats the snapshot dict slice as a markdown table for the prompt.

**Validator addition:** After Opus returns the calendar, validate that the calendar JSON references snapshot data points. If any slot has a projected revenue without citing a snapshot row, REJECT and re-prompt.

---

## Task 3: Fix Members & Subs Newsletter cadence (3 days → 5-7 days)

**Find where this is currently hardcoded.** Likely in:
- `pacing/calendar.py`
- `workers/email_builder.py`
- `workers/members_newsletter.py` (if exists)
- Or a config file like `config.py` or `pacing/config.json`

**Search command:**
```bash
cd /home/runner/workspace
grep -rn "3 days\|every 3\|days=3\|MEMBERS_NEWSLETTER" --include="*.py"
```

**Change:**
- If a constant like `MEMBERS_NEWSLETTER_INTERVAL_DAYS = 3` exists, change to `7` (use the upper bound; Strategist can shorten to 5 when scheduling).
- If hardcoded in calendar logic, replace with: `MEMBERS_NEWSLETTER_INTERVAL_DAYS = 7` and document the 5-7 range in the constant docstring.

**Test:** Generate a 14-day calendar and verify only 2 Members & Subs Newsletter sends appear (not 4-5).

---

## Task 4: Sleep audio audience rotation in deployer

**File:** Wherever the episode deployer writes Klaviyo campaigns. Likely:
- `workers/sleep_audio_deployer.py`
- `workers/episode_deployer.py`

**Current behavior (to remove):** Every new episode sends to BOTH Active Seal AND Engaged Customers excl Active Seal.

**New behavior:** Check the last 14 days of sleep audio sends per audience. Apply the decision matrix from beezy-episode-deployer v1.5:

```python
def determine_sleep_audio_audiences(today: datetime) -> List[str]:
    """Return list of audiences to send to for this episode (may be empty)."""
    # Query last 14 days of sleep audio campaigns
    recent_sleep_audio = query_recent_sleep_audio_sends(today - timedelta(days=14))

    # Find last send per audience
    last_active_seal = max((s["send_date"] for s in recent_sleep_audio if "Active Seal" in s["audience"]), default=None)
    last_engaged = max((s["send_date"] for s in recent_sleep_audio if "Engaged Customers" in s["audience"] and "Active Seal" not in s["audience"]), default=None)

    active_seal_days = (today - last_active_seal).days if last_active_seal else 999
    engaged_days = (today - last_engaged).days if last_engaged else 999

    # Also check total recent touch count (not just sleep audio)
    active_seal_total_touches = count_total_touches(today - timedelta(days=7), "Active Seal")
    engaged_total_touches = count_total_touches(today - timedelta(days=7), "Engaged Customers")

    audiences = []
    if active_seal_days >= 6 and active_seal_total_touches < 3:
        audiences.append("Active Seal")
    if engaged_days >= 6 and engaged_total_touches < 3:
        audiences.append("Engaged Customers excl Active Seal")

    return audiences
```

If `audiences == []`, post Slack note to `#beezy-new-episodes`:
> "Episode '{title}' page published but no email sent — Active Seal {active_seal_days}d ago (need 6), Engaged Customers {engaged_days}d ago (need 6). Page captures organic traffic. Next episode will rotate."

---

## Task 5: Enforce v2.0 sleep story page template

**File:** Wherever pages are created. Likely:
- `workers/sleep_story_page.py`
- Or a Shopify GraphQL mutation wrapper

**Pre-creation validation (NEW):**

```python
FORBIDDEN_PATTERNS = [
    r'<section\s+class=["\']epis-transcript',  # Full transcript section
    r'id=["\']hm-gate',                          # Hive Mind subscribe gate
    r'class=["\']epis-crumb',                    # Library breadcrumb
    r'Back to the (Meditation|Sleep) Library',   # Back link
    r'About this meditation',                    # Academic framing header
]

REQUIRED_PATTERNS = [
    r'/pages/sleep-science-hub',                 # Bottom CTA
    r'(Cinnamon CBN Sleep Honey|Botanical Extract Lotion|Caramel.*Honey).+(Cinnamon CBN Sleep Honey|Botanical Extract Lotion|Caramel.*Honey).+(Cinnamon CBN Sleep Honey|Botanical Extract Lotion|Caramel.*Honey)',  # 3-product stack
]

def validate_handle(handle: str) -> List[str]:
    errors = []
    if handle.startswith("episode-"):
        errors.append(f"Handle has forbidden 'episode-' prefix: {handle}")
    if len(handle) > 50:
        errors.append(f"Handle exceeds 50 char limit ({len(handle)} chars): {handle}")
    return errors

def validate_body(body: str) -> List[str]:
    errors = []
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, body):
            errors.append(f"Body contains forbidden pattern: {pattern}")
    for pattern in REQUIRED_PATTERNS:
        if not re.search(pattern, body):
            errors.append(f"Body missing required pattern: {pattern}")
    return errors

def create_sleep_story_page(handle, title, body, ...):
    handle_errors = validate_handle(handle)
    body_errors = validate_body(body)

    if handle_errors or body_errors:
        # HALT - post to Slack
        post_slack_error(f"Page validation failed for {title}:\n" +
                         "\n".join(handle_errors + body_errors))
        raise PageValidationError(handle_errors + body_errors)

    # Proceed with Shopify pageCreate mutation
    ...
```

---

## Task 6: Health check + recovery test

After all tasks complete, run:

```bash
cd /home/runner/workspace

# 1. Strategy Snapshot works
python -m pacing.strategy_snapshot

# 2. Calendar generation uses snapshot
python -m pacing.calendar --start 2026-05-22 --end 2026-05-31 --revenue-goal 150000

# 3. Verify forbidden patterns are caught
python -c "from workers.sleep_story_page import validate_body; print(validate_body('<section class=\"epis-transcript\">test</section>'))"

# 4. Verify clean handles required
python -c "from workers.sleep_story_page import validate_handle; print(validate_handle('episode-the-bridge-of-incidents'))"

# 5. Check Members Newsletter cadence is now 7 days
grep -rn "MEMBERS_NEWSLETTER_INTERVAL_DAYS" --include="*.py"
```

All should pass without errors. If any fails, fix before deploying.

---

## Task 7: Trigger the May 22-31 calendar generation

Once Tasks 1-6 are done, run:

```bash
cd /home/runner/workspace
python -m pacing.calendar --start 2026-05-22 --end 2026-05-31 --revenue-goal 150000 --insert-pending
```

The `--insert-pending` flag should insert all 14 calendar slots into `calendar_executions` table with `requires_approval=true`, per beezy-agents-system spec.

Verify in DB:
```sql
SELECT slot_date, slot_time, audience, format, projected_revenue, requires_approval 
FROM calendar_executions 
WHERE slot_date BETWEEN '2026-05-22' AND '2026-05-31' 
ORDER BY slot_date, slot_time;
```

Should return 14 rows. Post summary to Slack `#beezy-agents` for Alan's review.

---

## Task 8: Audit + rebuild broken sleep audio pages

The May 18-20 episode pages were created with the broken v1.1 template (transcript section, episode- prefix, Hive Mind gate, single-product CTA). Rebuild them with the v2.0 Bridge template.

**Pages to rebuild (priority order):**

1. `episode-releasing-the-day-a-shoulder-to-shoulder-meditation` → `releasing-the-day`
2. `episode-the-storm-across-the-wheat` → `the-storm-across-the-wheat`
3. `episode-you-are-enough-tonight` → `you-are-enough-tonight`
4. `episode-the-golden-hour` (consolidate 5 variants into one) → `the-golden-hour`
5. `episode-the-first-medicine-a-walk-through-ancient-dreams` → `the-first-medicine`
6. `episode-the-hour-before-midnight` → `the-hour-before-midnight`

**For each:**

1. Fetch existing page body
2. Extract: title, description_short, description_long (or generate from broken transcript), buzzsprout_url, hero_image_url
3. Generate new body using v2.0 Bridge template (NO transcript, 3-product stack, sleep-science-hub CTA)
4. Create new page with clean handle
5. Old page → set `isPublished: false` (don't delete in case of inbound links)
6. Set up 301 redirects from old URLs to new URLs (via Shopify URL Redirects)

Post completion summary to Slack `#beezy-agents`.

---

## Sequencing recommendation

Do tasks in this order:

1. **Task 1** (Strategy Snapshot module) — most important infrastructure
2. **Task 6 Step 1** (test snapshot works)
3. **Task 3** (Members Newsletter cadence) — quick fix, immediate effect
4. **Task 5** (page template enforcement) — prevents new broken pages
5. **Task 4** (sleep audio rotation) — prevents new audience over-touching  
6. **Task 2** (calendar.py consumes snapshot) — connects snapshot to generation
7. **Task 7** (generate May 22-31 calendar)
8. **Task 8** (rebuild broken pages) — can run in parallel with normal operations

Total estimated time: 3-5 hours of focused work.

---

## Rollback plan

If any task breaks production:

1. `git reset --hard HEAD~1` to revert last commit
2. Restart deployment via `Restart Deployment` in Replit UI
3. Verify cron loop alive: `curl https://beezy-agents-ingestion.replit.app/api/deploy/health`
4. Notify Alan on Slack `#beezy-agents`

---

## Done means

- ✅ Strategy Snapshot runs successfully and saves JSON
- ✅ Calendar generation prompts include snapshot data
- ✅ Members Newsletter cadence shows 7 days in any calendar generated
- ✅ Sleep audio deployer rotates audiences (logs show which audience selected per episode)
- ✅ Page template validator rejects forbidden patterns
- ✅ May 22-31 calendar inserted with 14 pending slots
- ✅ Slack notification posted with summary
- ✅ Broken pages rebuilt (this task can lag)

Once all checked, post to `#beezy-agents` Slack:
> "✅ beezy-agents v2.0 deployed. Strategy Snapshot, audience rotation, Bridge template lock, and Members Newsletter cadence (5-7d) all active. May 22-31 calendar pending Alan's review."

---

## Task 1.5: Add SMS channel to Strategy Snapshot (deferred from Task 1)

**Status:** Deferred per Alan on May 21, 2026. Revisit after Task 7.

**Context:** Task 1 snapshot pulls only `send_channel = email`. Klaviyo dashboard shows ~$8K/month in SMS attribution that snapshot is missing — primarily from SMS flow triggers. Current snapshot flows MTD ($13,686) is ~35% lower than dashboard flows MTD ($21,043) because of this gap.

**Spec:**
- Add parallel SMS pulls alongside email pulls in `pacing/strategy_snapshot.py`
- New top-level keys in snapshot JSON: `email_baseline` and `sms_baseline`, each with the same shape as the current `live_baseline`
- Keep `live_baseline` as combined (email + SMS) for total accuracy
- Format trajectories stay email-only (SMS has different format taxonomy)
- Recency audit stays email-only (SMS uses Smart Sending and different cadence rules)

**Why deferred:** Calendar generation governs email campaigns. SMS strategy is governed separately (different cadence, copy length, opt-in management). Email-only snapshot is sufficient for Tasks 2-7. Adding SMS now would expand scope without immediate calendar-quality benefit.

---

## Task 4.5: Sleep audio slot generation — single slot per episode

**Status:** Deferred per Alan on 2026-05-21. Surface after Task 8.

**Bug:** `pacing/calendar.py:1041-1054` autofill generates 2 calendar_executions slots per sleep audio episode (one per audience). Orchestrator dedupe key (orchestrator.py:81-89) includes audience, so both fire `_handle_sleep_audio`. The deployer's `_deploy_pre_produced` ignores slot.audience and creates campaigns for both audiences regardless. With Task 4's rotation check, the second firing finds both audiences in cooldown and skips — bug is masked but not fixed.

**Fix:** Calendar autofill should generate ONE slot per episode with audience='sleep_audio' (or 'both', or null). Deployer's audience-rotation logic decides which (if any) audience actually gets the email.

**Why deferred:** Task 4's rotation fix neutralizes the double-deploy risk operationally. Refactoring slot generation is its own concern.
