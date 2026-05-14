"""Monthly content calendar generator — Phase 4.

Generates a data-driven monthly content plan and outputs:
  1. A rich HTML report (saved to workspace + published as Shopify draft page)
  2. A brief Slack executive summary only — no slot dump in Slack

Per-slot data from Opus:
  - revenue_estimate (dollar projection for this send)
  - rationale (why this slot was chosen based on real data)
  - goal_alignment (how it maps to the monthly revenue goal)
  - adjustment_lever (what to change if it underperforms)

Top-level from Opus:
  - summary, monthly_revenue_gap, required_daily_rate, pacing_status
  - goal_adjustments (strategic recommendations to exceed goals)

Reads: active goals, 90d performance, strategies, upcoming issues.
Writes: decisions table row + HTML report + Slack executive summary.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal

import anthropic

from db.connection import get_conn
from lib.slack import notify_failure, post_draft
from pacing.brain import active_goals, compute_pacing_state, top_contributors

MODEL = "claude-opus-4-6"

# Colour map for the HTML report
CONTENT_TYPE_COLORS = {
    "hive_mind":          "#4a7c59",  # forest green
    "sleep_audio":        "#5b4a8b",  # deep purple
    "seo_blog":           "#2c6e8a",  # slate blue
    "klaviyo_campaign":   "#8b4513",  # beezy brown
    "sms_campaign":       "#b5651d",  # sienna
    "sniper_followup":    "#c0392b",  # red
    "flow_experiment":    "#6b6b6b",  # grey
}


# ── Data fetchers ─────────────────────────────────────────────────────────────

def _fetch_active_strategies() -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT component, strategy_text, approved_at
                  FROM strategies
                 WHERE is_active = true
                 ORDER BY component, approved_at DESC NULLS LAST
            """)
            rows = cur.fetchall()
    return [
        {"component": r[0], "strategy": r[1], "approved_at": str(r[2]) if r[2] else None}
        for r in rows
    ]


def _fetch_upcoming_issues() -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT number, status, subject_line, until_next_teaser, drafted_at
                  FROM issues
                 WHERE status IN ('draft', 'scheduled')
                 ORDER BY number ASC
            """)
            rows = cur.fetchall()
    return [
        {"number": r[0], "status": r[1], "subject_line": r[2],
         "until_next_teaser": r[3], "drafted_at": str(r[4]) if r[4] else None}
        for r in rows
    ]


def _fetch_last_published_issue() -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT number, subject_line, until_next_teaser, page_published_at
                  FROM issues
                 WHERE status = 'published'
                 ORDER BY number DESC LIMIT 1
            """)
            row = cur.fetchone()
    if not row:
        return None
    return {"number": row[0], "subject_line": row[1],
            "until_next_teaser": row[2], "published_at": str(row[3]) if row[3] else None}


def _fetch_90d_channel_performance() -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                WITH latest AS (
                  SELECT DISTINCT ON (
                           dimensions->>'entity_id',
                           dimensions->>'campaign_message_id',
                           dimensions->>'send_channel'
                         )
                         dimensions->>'send_channel' AS channel,
                         metric_value
                    FROM performance
                   WHERE source = 'klaviyo'
                     AND metric_name = 'conversion_value'
                     AND dimensions->>'kind' = 'campaign'
                     AND measured_at >= NOW() - INTERVAL '90 days'
                   ORDER BY dimensions->>'entity_id',
                            dimensions->>'campaign_message_id',
                            dimensions->>'send_channel',
                            measured_at DESC
                )
                SELECT channel, SUM(metric_value) AS rev, COUNT(*) AS sends
                  FROM latest GROUP BY channel ORDER BY rev DESC
            """)
            campaign_rows = cur.fetchall()

            cur.execute("""
                WITH latest AS (
                  SELECT DISTINCT ON (
                           dimensions->>'entity_id',
                           dimensions->>'flow_message_id',
                           dimensions->>'send_channel'
                         )
                         dimensions->>'send_channel' AS channel,
                         metric_value
                    FROM performance
                   WHERE source = 'klaviyo'
                     AND metric_name = 'conversion_value'
                     AND dimensions->>'kind' = 'flow'
                     AND measured_at >= NOW() - INTERVAL '90 days'
                   ORDER BY dimensions->>'entity_id',
                            dimensions->>'flow_message_id',
                            dimensions->>'send_channel',
                            measured_at DESC
                )
                SELECT channel, SUM(metric_value) AS rev
                  FROM latest GROUP BY channel ORDER BY rev DESC
            """)
            flow_rows = cur.fetchall()

            cur.execute("""
                WITH latest_per_order AS (
                  SELECT DISTINCT ON (dimensions->>'order_id') metric_value
                    FROM performance
                   WHERE source = 'shopify'
                     AND metric_name = 'order_revenue'
                     AND measured_at >= NOW() - INTERVAL '90 days'
                   ORDER BY dimensions->>'order_id', measured_at DESC
                )
                SELECT COALESCE(SUM(metric_value), 0) FROM latest_per_order
            """)
            (shopify_90d,) = cur.fetchone()

    return {
        "shopify_revenue_90d": float(shopify_90d or 0),
        "campaigns_by_channel": [
            {"channel": r[0], "revenue": float(r[1] or 0), "sends": int(r[2])}
            for r in campaign_rows
        ],
        "flows_by_channel": [
            {"channel": r[0], "revenue": float(r[1] or 0)}
            for r in flow_rows
        ],
    }


def _fetch_pacing_context() -> list[dict]:
    goals = active_goals()
    result = []
    for goal in goals:
        try:
            state = compute_pacing_state(goal.id)
            result.append({
                "goal": goal.title,
                "target_metric": goal.target_metric,
                "target_value": float(goal.target_value),
                "period": f"{goal.period_start} to {goal.period_end}",
                "period_to_date": float(state.period_to_date_value),
                "target_to_date": float(state.target_to_date_value),
                "gap_pct": float(state.gap_pct),
                "status": state.status,
                "days_remaining": state.days_remaining,
                "required_daily_rate": float(state.required_daily_rate),
            })
        except Exception as e:
            result.append({"goal": goal.title, "error": str(e)})
    return result


# ── Opus call ─────────────────────────────────────────────────────────────────

def _build_context(month_start: date) -> str:
    if month_start.month == 12:
        month_end = date(month_start.year + 1, 1, 1)
    else:
        month_end = date(month_start.year, month_start.month + 1, 1)
    days_in_month = (month_end - month_start).days

    pacing       = _fetch_pacing_context()
    contributors = top_contributors(days=90)
    perf         = _fetch_90d_channel_performance()
    strategies   = _fetch_active_strategies()
    upcoming     = _fetch_upcoming_issues()
    last_issue   = _fetch_last_published_issue()

    seg_data = _fetch_segment_rpr()

    ctx = {
        "brand": "Beezy Beez Honey — DTC botanical extract honey, women 50+, sleep support, ~$54.95 AOV",
        "planning_month": str(month_start)[:7],
        "planning_period_first_day": str(month_start),
        "planning_period_last_day": str(month_end - __import__("datetime").timedelta(days=1)),
        "days_in_month": days_in_month,
        "IMPORTANT": "Generate slots covering ALL days from planning_period_first_day through planning_period_last_day inclusive. Do not stop before the last day.",
        "goals_and_pacing": pacing,
        "performance_90d": perf,
        "top_campaign_contributors_90d": [
            {"name": c.entity_name, "channel": c.send_channel, "revenue": float(c.conversion_value)}
            for c in contributors["campaigns"]
        ],
        "top_flow_contributors_90d": [
            {"name": c.entity_name, "revenue": float(c.conversion_value)}
            for c in contributors["flows"]
        ],
        "active_strategies": strategies,
        "hive_mind_already_drafted": upcoming,
        "last_published_hive_mind_issue": last_issue,
        "content_types_available": [
            "hive_mind        — prospect newsletter email, Mon/Thu 8pm EST only",
            "sleep_audio      — Sleep Better podcast episode email, customer list",
            "seo_blog         — SEO blog post on Shopify, NO email send, revenue_estimate=0",
            "klaviyo_campaign — primary email campaign to a customer segment, anchor at 2pm EDT",
            "sniper_followup  — non-opener follow-up 4-6h after primary, different subject line",
            "sms_campaign     — SMS blast, max 2x/month, high-value moments only",
            "flow_experiment  — internal flow A/B test or tuning task, revenue_estimate=0",
        ],
        "flow_campaign_context": (
            "Flows currently ~29-30pct of Klaviyo revenue. Target 70pct. Campaigns must carry "
            "revenue load until flows mature. Plan 2-4 email sends/day to non-overlapping segments. "
            "Sniper follow-ups to non-openers are standard and expected. Best send time: 2pm EDT."
        ),
    }
    ctx["segment_rpr_data"] = seg_data.get("context_text", "")
    return json.dumps(ctx, indent=2, default=str)


SYSTEM_PROMPT = """You are the revenue-focused content calendar strategist for Beezy Beez Honey.

CRITICAL CONTEXT — internalize before planning:
Flows generate ~29-30% of Klaviyo revenue. The long-term target is 70%. Until flows reach
that target, campaigns must carry the revenue load. This means running campaigns aggressively
NOW while simultaneously strengthening flows. The $150K+ monthly Klaviyo goal requires high
campaign volume. This is the explicit strategy — not a bug.

CAMPAIGN VOLUME RULES:
- A day CAN and SHOULD have 2–4 email sends. Each send MUST target a distinct segment
  with zero audience overlap. This is mandatory — segment discipline prevents fatigue.
- Sniper follow-ups: 4–6 hours after a primary send, to NON-OPENERS only. Different
  subject line. Recovers 15–25% additional revenue from the same send. Plan these daily.
- Bridge emails: mid-day send to a segment not touched that morning or evening.
- Late-night sends (9–10pm EST): high-engagement segments only (VIP, active buyers).
- Best performing send time confirmed: 2pm EDT — anchor primary sends here.
- Rotate across: lapsed_30d, lapsed_60d, lapsed_90d, vip, one_time_buyers,
  active_subscribers, engaged_prospects, winback_180d, cart_abandoners.
  Never two sends to overlapping segments same day.

HIVE MIND: Mon + Thu 8pm EST only. Prospect list. Never conflicts with campaign audience.

SMS: max 2x/month. High-value moments only (VIP credit, flash close, restock).

FLOW EXPERIMENTS: 1–2 per week. No revenue. Critical to growing flow contribution %.

SEO BLOG: 4–6/month, spread evenly. revenue_estimate = 0. No email send involved.

WEEKEND RULE: ALL 7 DAYS ARE VALID SEND DAYS. Do NOT skip Saturdays or Sundays.
  Women 50+ check email on weekends. Distribute sends evenly across all 7 days of the week.

2026 HOLIDAYS (use for themed campaigns where relevant to sleep/honey/wellness/gifting):
  May 25 Memorial Day, Jun 19 Juneteenth, Jul 3/4 Independence Day,
  Sep 7 Labor Day, Oct 31 Halloween, Nov 11 Veterans Day,
  Nov 26 Thanksgiving, Nov 27 Black Friday, Nov 28 Small Business Saturday,
  Dec 24-25 Christmas, Dec 31 New Year's Eve
  Brand-relevant: Mother's Day May 10, Earth Day Apr 22, Valentine's Feb 14

LANDING PAGES:
- Set needs_page: true when the email angle is content-driven (sleep science, research,
  story, discovery, audio, meditation). The system will create a Shopify page first,
  then build the email to drive traffic there.
- Set needs_page: false for pure offer/discount/reactivation emails — those link
  directly to the product or discount URL. No page needed.

DISCOUNT CODES (include for win-back / lapsed / flash sale / VIP slots):
- Add discount_code (e.g. SLEEP20, HONEY30, WAKE25 — ALL CAPS, max 10 chars) and
  discount_pct (integer 15-35) to the slot when the angle is promotional.
- Omit for relationship/editorial/brand-story sends (no hard sell).
- Sniper follow-ups: use the same code as their parent send.
- Code format: brand-relevant, memorable, specific to the offer.

REVENUE ESTIMATES:
- Ground every estimate in the 90d performance data provided. Use top contributor
  figures as benchmarks. Do not invent numbers.
- seo_blog and flow_experiment always get revenue_estimate = 0.
- Sniper follow-ups: 15–25% of parent send revenue estimate.

IF PACING IS BEHIND: every day needs 2+ revenue sends. Front-load the month.
  Maximize sniper follow-ups. Fill every viable segment slot.
IF PACING IS ON-TRACK OR AHEAD: maintain volume, increase flow experiment investment.

OUTPUT: valid JSON only. No markdown, no preamble, no trailing text. Schema:
{
  "month": "YYYY-MM",
  "pacing_status": "behind|on-track|ahead",
  "monthly_revenue_gap": 12345.00,
  "required_daily_rate": 567.89,
  "flow_revenue_pct": 29.5,
  "summary": "2-3 sentence executive summary grounded in pacing data and flow/campaign split",
  "goal_adjustments": [
    "Specific actionable recommendation to close the gap or exceed goals"
  ],
  "content_counts": {
    "hive_mind": 0, "sleep_audio": 0, "seo_blog": 0,
    "klaviyo_campaign": 0, "sniper_followup": 0,
    "sms_campaign": 0, "flow_experiment": 0
  },
  "slots": [
    {
      "date": "YYYY-MM-DD",
      "day_of_week": "Monday",
      "content_type": "klaviyo_campaign|sniper_followup|hive_mind|sleep_audio|seo_blog|sms_campaign|flow_experiment",
      "channel": "email|sms",
      "audience": "segment_name",
      "parent_send": "for sniper_followup: describe the primary send this follows",
      "topic_angle": "subject line direction or content angle",
      "send_time_est": "14:00",
      "priority": "high|medium|low",
      "revenue_estimate": 1250.00,
      "needs_page": false,
      "discount_code": "SLEEP20",
      "discount_pct": 20,
      "rationale": "Why this slot — cite specific data (segment last touched X days, drove $Y in 90d)",
      "goal_alignment": "How this addresses the monthly revenue goal or flow ramp strategy",
      "adjustment_lever": "If underperforms: do X instead"
    }
  ]
}"""


def _repair_json(raw: str) -> dict:
    """
    Attempt to recover a valid calendar dict from a malformed JSON string.
    Strategy: truncate at the last complete slot object and close the structure.
    """
    # Find the last complete slot entry — ends with closing brace + newline
    # Try progressively shorter truncations until we get valid JSON
    for pattern in ['}\n    ]\n}', '    }\n  ]\n}', '}\n  ]\n}', '  }\n]\n}']:
        idx = raw.rfind(pattern)
        if idx != -1:
            candidate = raw[:idx + len(pattern)]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    # Last resort: truncate at last complete slot and manually close
    last_slot_end = raw.rfind('"adjustment_lever"')
    if last_slot_end != -1:
        # Find the closing } after this field
        close = raw.find('}', last_slot_end)
        if close != -1:
            candidate = raw[:close + 1] + '\n  ]\n}'
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

    raise ValueError("Could not repair malformed JSON from Opus response.")


def _fetch_segment_rpr() -> dict:
    """
    Pull actual RPR by segment from performance + calendar_executions tables.
    Falls back to conservative estimates if no real data.
    """
    from pacing.calendar_live_data import (
        get_performance_by_segment, get_pacing_context,
        build_performance_context_text, FALLBACK_RPR, FALLBACK_LIST_SIZE
    )
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            perf   = get_performance_by_segment(conn)
            pacing = get_pacing_context(conn)
        return {"perf": perf, "pacing": pacing,
                "context_text": build_performance_context_text(perf, pacing)}
    except Exception as ex:
        print("[calendar] _fetch_segment_rpr failed: " + str(ex))
        # Build fallback text
        lines = ["=== PERFORMANCE DATA (estimated — no real data available) ==="]
        for aud, rpr in FALLBACK_RPR.items():
            size = FALLBACK_LIST_SIZE.get(aud, 4000)
            lines.append(f"  {aud:<22} RPR ${rpr:.3f}  list ~{size:,}  ≈ ${rpr*size:,.0f}/send  (estimated)")
        return {"perf": {}, "pacing": {}, "context_text": "\n".join(lines)}


def _call_opus(context_str: str) -> dict:
    api_key = os.environ.get("BEEZY_ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("BEEZY_ANTHROPIC_API_KEY is not set.")
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                "Here is the current performance and planning context. "
                "Generate the content calendar for the FULL calendar month shown below. "
                "Cover EVERY day from the 1st through the last day of the month — do not stop early. "
                "Include weekends (Sat + Sun). All 7 days are valid send days. "
                "Keep each text field under 120 characters — be concise and data-specific.\n"
                "USE THE SEGMENT RPR DATA to set revenue_estimate per slot. "
                "Do NOT invent revenue numbers. "
                "The pacing gap tells you how aggressive to be.\n\n"
                + context_str
            ),
        }],
    )
    raw = msg.content[0].text.strip()

    # Strip markdown fences if present
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                raw = part
                break

    # Extract the outermost JSON object
    start = raw.find("{")
    end   = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end + 1]

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[calendar] JSON parse failed ({e}), attempting repair...")
        return _repair_json(raw)


# ── HTML report ───────────────────────────────────────────────────────────────

def _generate_html_report(month_start: date, cal: dict) -> str:
    """Build calendar HTML with planned vs actual revenue columns."""
    # Fetch actual revenue from calendar_executions
    actual_by_date_audience: dict = {}
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT slot_date::text, audience, COALESCE(actual_revenue,0), status "
                    "FROM calendar_executions WHERE slot_date >= %s",
                    (month_start.isoformat(),)
                )
                for row in cur.fetchall():
                    key = str(row[0]) + "|" + str(row[1])
                    actual_by_date_audience[key] = {"revenue": float(row[2]), "status": row[3]}
    except Exception as ex:
        print("[calendar] actual revenue fetch failed: " + str(ex))

    slots    = cal.get("slots", [])
    month_lbl = month_start.strftime("%B %Y")

    planned_total = sum(float(s.get("revenue_estimate", 0)) for s in slots)
    actual_total  = sum(v["revenue"] for v in actual_by_date_audience.values())

    rows_html = ""
    for s in slots:
        key    = str(s.get("date","")) + "|" + str(s.get("audience",""))
        actual = actual_by_date_audience.get(key, {})
        act_rev = actual.get("revenue", 0)
        status  = actual.get("status", "")

        status_badge = ""
        if status == "completed":
            status_badge = ' <span style="background:#27ae60;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;">sent</span>'
        elif status == "dispatched":
            status_badge = ' <span style="background:#2980b9;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;">queued</span>'
        elif status == "failed":
            status_badge = ' <span style="background:#e74c3c;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;">failed</span>'

        needs_page = "✓" if s.get("needs_page") else ""
        disc_code  = s.get("discount_code","")
        act_cell   = ("$" + f"{act_rev:,.0f}" if act_rev > 0 else "—")

        rows_html += (
            "<tr>"
            "<td>" + str(s.get("date","")) + "</td>"
            "<td>" + str(s.get("content_type","")) + "</td>"
            "<td>" + str(s.get("audience","")) + "</td>"
            "<td>" + str(s.get("topic_angle",""))[:60] + "</td>"
            "<td>" + str(s.get("send_time_est","")) + "</td>"
            "<td>" + str(s.get("priority","")) + "</td>"
            "<td style=\"text-align:right\">$" + f"{float(s.get('revenue_estimate',0)):,.0f}" + "</td>"
            "<td style=\"text-align:right;font-weight:bold\">" + act_cell + "</td>"
            "<td>" + needs_page + "</td>"
            "<td><code>" + disc_code + "</code></td>"
            "<td>" + str(s.get("rationale",""))[:80] + status_badge + "</td>"
            "</tr>\n"
        )

    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Content Calendar — """ + month_lbl + """</title>
<style>
body{font-family:-apple-system,sans-serif;background:#f8f4ed;color:#2c2417;padding:20px;}
h1{color:#8b4513;font-size:28px;margin:0 0 4px;}
.summary{display:flex;gap:20px;margin:16px 0 24px;flex-wrap:wrap;}
.card{background:#fff;border-radius:8px;padding:14px 20px;min-width:160px;
  box-shadow:0 1px 4px rgba(0,0,0,.08);}
.card .label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#8b7355;margin-bottom:4px;}
.card .value{font-size:22px;font-weight:600;color:#2c2417;}
.card.warn .value{color:#c0392b;}
.card.good .value{color:#27ae60;}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;
  overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);font-size:13px;}
th{background:#8b4513;color:#fff;padding:10px 12px;text-align:left;font-weight:500;}
td{padding:9px 12px;border-bottom:1px solid #f0e8d8;vertical-align:top;}
tr:last-child td{border-bottom:none;}
tr:hover td{background:#fdf5e6;}
code{background:#f0e8d8;padding:2px 5px;border-radius:3px;font-size:12px;}
</style></head><body>
<h1>Content Calendar — """ + month_lbl + """</h1>
<p style="color:#8b7355;margin:0 0 16px;">""" + str(len(slots)) + """ slots &middot; Generated """ + str(month_start.today()) + """</p>
<div class="summary">
  <div class="card"><div class="label">Planned Revenue</div><div class="value">$""" + f"{planned_total:,.0f}" + """</div></div>
  <div class="card """ + ("good" if actual_total > 0 else "") + """"><div class="label">Actual Revenue</div><div class="value">$""" + f"{actual_total:,.0f}" + """</div></div>
  <div class="card"><div class="label">Total Slots</div><div class="value">""" + str(len(slots)) + """</div></div>
  <div class="card"><div class="label">Campaigns</div><div class="value">""" + str(sum(1 for s in slots if "campaign" in s.get("content_type",""))) + """</div></div>
  <div class="card"><div class="label">SMS</div><div class="value">""" + str(sum(1 for s in slots if "sms" in s.get("content_type",""))) + """</div></div>
</div>
<table>
<tr><th>Date</th><th>Type</th><th>Audience</th><th>Topic</th><th>Time</th>
<th>Priority</th><th>Planned $</th><th>Actual $</th><th>Page</th><th>Discount</th><th>Rationale</th></tr>
""" + rows_html + """
</table>
</body></html>"""


def _save_html_report(month_start: date, html: str) -> str:
    """Save HTML report to workspace. Returns file path."""
    filename = f"calendar_{month_start.strftime('%Y_%m')}.html"
    path = f"/home/runner/workspace/{filename}"
    with open(path, "w") as f:
        f.write(html)
    return path


# ── Persistence ───────────────────────────────────────────────────────────────

def _persist_calendar(month_start: date, calendar_dict: dict) -> str:
    summary = calendar_dict.get("summary", "")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO decisions (decided_by, decision_type, reasoning, output)
                VALUES (%s, %s, %s, %s::jsonb)
                RETURNING id
                """,
                (
                    "calendar",
                    "calendar_plan",
                    f"Calendar for {month_start.strftime('%B %Y')}: {summary}",
                    json.dumps(calendar_dict),
                ),
            )
            (row_id,) = cur.fetchone()
            conn.commit()
    return str(row_id)



# ── Shopify page publish ──────────────────────────────────────────────────────

def _publish_calendar_page(month_start: date, html: str) -> str:
    """
    Publish the calendar HTML as a Shopify page.
    Returns the live page URL.
    Handle: calendar-YYYY-MM (e.g. calendar-2026-05)
    Published: true — accessible directly in browser, not linked from site nav.
    """
    import httpx

    shop  = os.environ.get("SHOPIFY_SHOP_DOMAIN")
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    if not shop or not token:
        raise RuntimeError("SHOPIFY_SHOP_DOMAIN or SHOPIFY_ACCESS_TOKEN not set.")

    handle  = f"calendar-{month_start.strftime('%Y-%m')}"
    title   = f"Content Calendar — {month_start.strftime('%B %Y')}"
    api_url = f"https://{shop}/admin/api/2025-10/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}

    # Check if page already exists for this month
    check = httpx.post(api_url, headers=headers, timeout=30, json={
        "query": '{ pages(first: 1, query: "handle:%s") { edges { node { id } } } }' % handle
    })
    edges = check.json().get("data", {}).get("pages", {}).get("edges", [])

    if edges:
        page_id = edges[0]["node"]["id"]
        resp = httpx.post(api_url, headers=headers, timeout=30, json={
            "query": """mutation pageUpdate($id: ID!, $page: PageUpdateInput!) {
                pageUpdate(id: $id, page: $page) {
                    page { id } userErrors { field message }
                }}""",
            "variables": {"id": page_id, "page": {"body": html, "isPublished": True}},
        })
        errors = resp.json()["data"]["pageUpdate"]["userErrors"]
    else:
        resp = httpx.post(api_url, headers=headers, timeout=30, json={
            "query": """mutation pageCreate($page: PageCreateInput!) {
                pageCreate(page: $page) {
                    page { id } userErrors { field message }
                }}""",
            "variables": {"page": {
                "title": title, "handle": handle,
                "body": html, "isPublished": True,
            }},
        })
        errors = resp.json()["data"]["pageCreate"]["userErrors"]

    if errors:
        raise RuntimeError(f"Shopify calendar page failed: {errors}")

    return f"https://{shop}/pages/{handle}"


# ── Slack — executive summary only ───────────────────────────────────────────

def _post_slack_summary(month_start: date, cal: dict, decision_id: str, report_path: str, page_url: str = "") -> None:
    """Post a tight executive summary to Slack. No slot dump."""
    month_label   = month_start.strftime("%B %Y")
    pacing_status = cal.get("pacing_status", "?")
    gap           = cal.get("monthly_revenue_gap", 0)
    daily_rate    = cal.get("required_daily_rate", 0)
    counts        = cal.get("content_counts", {})
    adjustments   = cal.get("goal_adjustments", [])[:3]  # top 3 only
    total_rev_est = sum(s.get("revenue_estimate", 0) for s in cal.get("slots", []))
    summary       = cal.get("summary", "")

    status_emoji = {"behind": "🔴", "on-track": "🟡", "ahead": "🟢"}.get(pacing_status, "⚪")

    adj_text = "\n".join(f"  • {a}" for a in adjustments)

    post_draft(
        title=f"📅 Content Calendar — {month_label}",
        summary_lines=[
            f"{status_emoji} Pacing: {pacing_status.upper()}",
            f"Revenue gap:       ${gap:,.0f}",
            f"Required daily:    ${daily_rate:,.0f}/day",
            f"Projected revenue: ${total_rev_est:,.0f} from {len(cal.get('slots', []))} slots",
            f"Hive Mind: {counts.get('hive_mind',0)}  |  Campaigns: {counts.get('klaviyo_campaign',0)}  |  SMS: {counts.get('sms_campaign',0)}  |  SEO: {counts.get('seo_blog',0)}",
        ],
        body=(
            f"*Summary:*\n{summary}\n\n"
            f"*Top adjustments to exceed goals:*\n{adj_text}\n\n"
            f"*Full calendar (table with rationale + revenue est):*\n"
            f"{page_url}\n\n"
            f"Decision ID: {decision_id}"
        ),
    )


# ── Public API ────────────────────────────────────────────────────────────────

def generate(month_start: date) -> dict:
    """Generate a monthly content calendar. Returns the calendar dict."""
    print(f"[calendar] Generating calendar for {month_start.strftime('%B %Y')}...")

    print("[calendar] Fetching context data...")
    context_str = _build_context(month_start)
    print(f"[calendar]   Context: {len(context_str):,} chars")

    print("[calendar] Calling Opus...")
    cal = _call_opus(context_str)
    print(f"[calendar]   {len(cal.get('slots', []))} slots generated")

    print("[calendar] Persisting to decisions table...")
    decision_id = _persist_calendar(month_start, cal)
    print(f"[calendar]   decision_id: {decision_id}")

    print("[calendar] Building HTML report...")
    html = _generate_html_report(month_start, cal)
    report_path = _save_html_report(month_start, html)
    print(f"[calendar]   Report saved: {report_path}")

    print("[calendar] Publishing calendar to Shopify...")
    try:
        page_url = _publish_calendar_page(month_start, html)
        print(f"[calendar]   Published: {page_url}")
    except Exception as e:
        print(f"[calendar]   Shopify publish failed: {e}")
        page_url = f"(Shopify publish failed — open {report_path} in Replit)"

    print("[calendar] Posting Slack summary...")
    _post_slack_summary(month_start, cal, decision_id, report_path, page_url)

    print("[calendar] Done.")
    return cal


def run_monthly(month_start: date | None = None) -> dict:
    """Generate calendar for given month (defaults to next month when called 1 week before EOM)."""
    if month_start is None:
        today = date.today()
        month_start = date(today.year, today.month, 1)
    try:
        return generate(month_start)
    except Exception as e:
        notify_failure(source="calendar/run_monthly", error=str(e))
        raise


if __name__ == "__main__":
    run_monthly()
