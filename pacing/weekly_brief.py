"""
Weekly lookahead brief.

Every Sunday at 9pm ET, reads the next 7 days of calendar slots from
the decisions table and posts a structured Slack batch with an approval link.

Boris reviews and clicks "Approve this week" in Slack.
The approval endpoint writes to calendar_approvals table.
The orchestrator checks approval before executing any slot.
"""
from __future__ import annotations

import hashlib, json, os
from datetime import date, timedelta

from db.connection import get_conn
from lib.slack import post_draft, notify_failure


REPLIT_DOMAIN = os.environ.get("REPLIT_DOMAIN", "beezy-agents-ingestion.replit.app")


def _latest_calendar(conn) -> tuple[str | None, list[dict]]:
    month = date.today().strftime("%Y-%m")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, output FROM decisions WHERE decision_type = 'calendar_plan' "
            "AND output->>'month' = %s ORDER BY created_at DESC LIMIT 1",
            (month,)
        )
        row = cur.fetchone()
    if not row:
        return None, []
    payload = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    return str(row[0]), payload.get("slots", [])


def _week_slots(slots: list[dict], week_start: date) -> list[dict]:
    week_end = week_start + timedelta(days=7)
    result = []
    for s in slots:
        d = s.get("date","")
        if d and week_start.isoformat() <= d < week_end.isoformat():
            result.append(s)
    return sorted(result, key=lambda x: (x.get("date",""), x.get("send_time_est","")))


def _approval_token(week_start: date) -> str:
    secret = os.environ.get("BEEZY_ANTHROPIC_API_KEY", "secret")
    return hashlib.sha256((str(week_start) + secret).encode()).hexdigest()[:16]


def _is_approved(conn, week_start: date) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM calendar_approvals "
            "WHERE week_start <= %s AND %s < week_start + INTERVAL '7 days' "
            "AND approved_at IS NOT NULL LIMIT 1",
            (week_start, week_start)
        )
        return cur.fetchone() is not None


def _insert_pending(conn, week_start: date, token: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO calendar_approvals (week_start, token) VALUES (%s, %s) "
            "ON CONFLICT (week_start) DO UPDATE SET token = EXCLUDED.token, approved_at = NULL",
            (week_start, token)
        )
    conn.commit()


CONTENT_EMOJI = {
    "hive_mind":        "🌙",
    "klaviyo_campaign": "📧",
    "sniper_followup":  "⚡",
    "seo_blog":         "📝",
    "sleep_audio":      "🎙",
    "sms_campaign":     "📱",
    "flow_experiment":  "🔬",
}

CONTENT_LABEL = {
    "klaviyo_campaign": "Email",
    "sniper_followup":  "Follow-up email",
    "hive_mind":        "Hive Mind newsletter",
    "seo_blog":         "SEO blog post",
    "sleep_audio":      "Sleep audio episode",
    "sms_campaign":     "SMS",
    "flow_experiment":  "Flow experiment",
}

AUDIENCE_LABEL = {
    "lapsed_30d":          "Lapsed 30d customers",
    "lapsed_60d":          "Lapsed 60d customers",
    "lapsed_90d":          "Lapsed 90d customers",
    "lapsed_180d":         "Lapsed 180d customers",
    "lapsed_90_180d":      "Lapsed 90–180d customers",
    "lapsed_180d_plus":    "Lapsed 180d+ customers",
    "winback_180d":        "Winback list",
    "vip":                 "VIP customers",
    "inner_circle":        "Inner Circle",
    "whales":              "Whales",
    "high_aov":            "High-AOV customers",
    "engaged_customers":   "Engaged customers",
    "all_customers":       "All customers",
    "active_seal":         "Active Seal members",
    "active_subscribers":  "Active subscribers",
    "one_time_buyers":     "One-time buyers",
    "otb":                 "One-time buyers",
    "cart_abandoners":     "Cart abandoners",
    "engaged_prospects":   "Engaged prospects",
    "super_engaged":       "Super engaged prospects",
    "hive_mind_prospects": "Hive Mind prospects",
}


def _fmt_time(t: str) -> str:
    """Convert '14:00' → '2:00pm', '08:15' → '8:15am'."""
    try:
        h, m = int(t[:2]), int(t[3:5])
        suffix = "am" if h < 12 else "pm"
        h12 = h % 12 or 12
        return (str(h12) + ":" + f"{m:02d}" + suffix) if m else (str(h12) + suffix)
    except Exception:
        return t


def run_weekly_brief() -> None:
    today      = date.today()
    week_start = today + timedelta(days=1)   # tomorrow through +7

    with get_conn() as conn:
        decision_id, all_slots = _latest_calendar(conn)
        if not decision_id:
            post_draft(
                title="Weekly Brief -- No Calendar Plan Found",
                summary_lines=["No calendar_plan in decisions table for " + today.strftime("%B %Y")],
                body="Run: python -c \"from pacing.calendar import run_monthly; run_monthly()\"",
            )
            return

        slots = _week_slots(all_slots, week_start)
        if not slots:
            post_draft(
                title="Weekly Brief -- No Slots Found",
                summary_lines=["No slots in calendar for " + week_start.isoformat() + " through " + (week_start + timedelta(days=6)).isoformat()],
                body="Calendar may only cover the current month. Check the Shopify calendar page.",
            )
            return

        token = _approval_token(week_start)
        _insert_pending(conn, week_start, token)

    # Build slot lines grouped by day
    by_day: dict[str, list[dict]] = {}
    for s in slots:
        d = s.get("date","")
        by_day.setdefault(d, []).append(s)

    body_lines = []
    total_rev = 0
    for d in sorted(by_day.keys()):
        day_slots = by_day[d]
        day_label = date.fromisoformat(d).strftime("%A, %b %d")
        body_lines.append("*" + day_label + "*")
        for s in day_slots:
            ct      = s.get("content_type", "?")
            emoji   = CONTENT_EMOJI.get(ct, "•")
            label   = CONTENT_LABEL.get(ct, ct)
            aud_raw = s.get("audience", "")
            aud     = AUDIENCE_LABEL.get(aud_raw, aud_raw.replace("_", " ").title()) if aud_raw else ""
            topic   = s.get("topic_angle", "")[:70]
            tm      = _fmt_time(s.get("send_time_est", "?"))
            rev     = float(s.get("revenue_estimate", 0) or 0)
            if ct not in ("seo_blog", "flow_experiment"):
                total_rev += rev
            rev_str  = f"est. ${rev:,.0f}" if rev else ""
            aud_part = f" → {aud}" if aud else ""
            rev_part = f"  ·  {rev_str}" if rev_str else ""
            topic_part = f'  "{topic}"' if topic else ""
            body_lines.append(f"  {emoji} {tm}{aud_part}{topic_part}{rev_part}")
        body_lines.append("")

    body_lines.append(f"*Projected revenue this week: ${total_rev:,.0f}*")
    body_lines.append("")
    body_lines.append("✅ *To approve, simply type:* `approved week`")
    body_lines.append("_Or click the Approve button on the dashboard._")
    body_lines.append("")
    body_lines.append("Once you approve:")
    body_lines.append("  • Campaigns run automatically every morning at 8am")
    body_lines.append("  • You'll get a daily morning update with what went out and how revenue looks")
    body_lines.append("  • Nothing else needed from you until next Sunday")

    week_end_label = (week_start + timedelta(days=6)).strftime("%b %d")
    post_draft(
        title="Your week is ready — " + week_start.strftime("%b %d") + " to " + week_end_label,
        summary_lines=[
            "📅 " + str(len(slots)) + " campaigns planned",
            "💰 Projected revenue: $" + str(int(total_rev)),
            "👆 Type `approved week` to activate everything",
        ],
        body="\n".join(body_lines),
    )
    print("[weekly_brief] Posted " + str(len(slots)) + " slots for week of " + week_start.isoformat())


def run_approval_nudge() -> None:
    """Monday-morning escalation: post a strong reminder if this week is still unapproved.

    Called by cron at 9:30am Monday. No-op if week is already approved.
    """
    today      = date.today()
    week_start = today - timedelta(days=today.weekday())  # Monday of current week

    with get_conn() as conn:
        if _is_approved(conn, week_start):
            print("[weekly_brief/nudge] Week already approved — no nudge needed")
            return

        decision_id, all_slots = _latest_calendar(conn)

    slots = _week_slots(all_slots, week_start) if decision_id else []
    slot_count = len(slots)
    total_rev  = sum(
        float(s.get("revenue_estimate", 0) or 0)
        for s in slots
        if s.get("content_type") not in ("seo_blog", "flow_experiment")
    )

    lines = [
        f"*{slot_count} campaigns are ready to go — but nothing is running yet.*",
        f"Projected revenue this week: *${total_rev:,.0f}*",
        "",
        "Campaigns dispatch automatically every morning at 8am once you approve.",
        "The longer approval waits, the more today's sends slip.",
        "",
        "✅ Type `approved week` in this channel to activate everything.",
        "_Or click *Approve All Weeks* on the dashboard._",
    ]

    post_draft(
        title="Action needed — this week's campaigns are paused",
        summary_lines=[
            f"{slot_count} campaigns waiting  ·  est. ${total_rev:,.0f} this week",
            "Not approved yet — orchestrator is idle",
        ],
        body="\n".join(lines),
    )
    print(f"[weekly_brief/nudge] Approval nudge posted — {slot_count} slots, ${total_rev:,.0f} projected")


if __name__ == "__main__":
    run_weekly_brief()
