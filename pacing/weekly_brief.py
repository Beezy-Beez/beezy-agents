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
            "SELECT 1 FROM calendar_approvals WHERE week_start = %s AND approved_at IS NOT NULL LIMIT 1",
            (week_start,)
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

    approval_url = "https://" + REPLIT_DOMAIN + "/api/approve-week/" + week_start.isoformat() + "?token=" + token

    # Build slot lines grouped by day
    by_day: dict[str, list[dict]] = {}
    for s in slots:
        d = s.get("date","")
        by_day.setdefault(d, []).append(s)

    body_lines = []
    total_rev = 0
    for d in sorted(by_day.keys()):
        day_slots = by_day[d]
        day_label = date.fromisoformat(d).strftime("%a %b %d")
        body_lines.append("*" + day_label + "*")
        for s in day_slots:
            ct    = s.get("content_type","?")
            emoji = CONTENT_EMOJI.get(ct, "•")
            rev   = s.get("revenue_estimate", 0)
            if ct not in ("seo_blog","flow_experiment"):
                total_rev += rev
            rev_str = "$" + str(int(rev)) if rev else "--"
            body_lines.append(
                "  " + emoji + " " + s.get("send_time_est","?") + " EST  "
                + ct + "  |  " + s.get("audience","?") + "  |  "
                + s.get("topic_angle","")[:60] + "  |  rev:" + rev_str
                + "  |  " + s.get("priority","?")
            )
        body_lines.append("")

    body_lines.append("*Projected revenue this week (campaigns/SMS only):* $" + str(int(total_rev)))
    body_lines.append("")
    body_lines.append("*To approve, run this in Replit shell:*")
    body_lines.append("```python -c \"from db.connection import get_conn; conn=get_conn(); conn.execute(\\\"INSERT INTO calendar_approvals (week_start, token, approved_at) VALUES ('" + week_start.isoformat() + "', 'manual', NOW()) ON CONFLICT (week_start) DO UPDATE SET approved_at = NOW()\\\"); conn.commit()\"```")
    body_lines.append("")
    body_lines.append("_Once approved, slots execute automatically each morning at 8am ET._")
    body_lines.append("_SEO blog posts publish automatically. Campaigns post as Slack drafts for you to build in Klaviyo._")

    week_end_label = (week_start + timedelta(days=6)).strftime("%b %d")
    post_draft(
        title="Weekly Plan -- " + week_start.strftime("%b %d") + " to " + week_end_label + " (PENDING APPROVAL)",
        summary_lines=[
            "Slots:            " + str(len(slots)),
            "Projected rev:    $" + str(int(total_rev)),
            "Week:             " + week_start.isoformat() + " to " + (week_start + timedelta(days=6)).isoformat(),
            "Status:           PENDING -- click link below to approve",
        ],
        body="\n".join(body_lines),
    )
    print("[weekly_brief] Posted " + str(len(slots)) + " slots for week of " + week_start.isoformat())


if __name__ == "__main__":
    run_weekly_brief()
