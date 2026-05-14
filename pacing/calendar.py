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

    ctx = {
        "brand": "Beezy Beez Honey — DTC botanical extract honey, women 50+, sleep support, ~$54.95 AOV",
        "planning_month": str(month_start)[:7],
        "days_in_month": days_in_month,
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
                "Generate the content calendar for the planning month. "
                "Keep each text field under 120 characters — be concise and data-specific.\n\n"
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
    month_label   = month_start.strftime("%B %Y")
    pacing_status = cal.get("pacing_status", "unknown")
    gap           = cal.get("monthly_revenue_gap", 0)
    daily_rate    = cal.get("required_daily_rate", 0)
    summary       = cal.get("summary", "")
    adjustments   = cal.get("goal_adjustments", [])
    slots         = cal.get("slots", [])
    counts        = cal.get("content_counts", {})

    pacing_color = {"behind": "#c0392b", "on-track": "#27ae60", "ahead": "#2980b9"}.get(
        pacing_status, "#666"
    )

    # Build adjustments HTML
    adj_html = "".join(f"<li>{a}</li>" for a in adjustments)

    # Build table rows
    rows_html = ""
    for s in slots:
        ct      = s.get("content_type", "")
        color   = CONTENT_TYPE_COLORS.get(ct, "#444")
        badge   = f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:3px;font-size:12px;white-space:nowrap;">{ct}</span>'
        rev_est = s.get("revenue_estimate", 0)
        rev_str = f"${rev_est:,.0f}" if rev_est else "—"
        pri     = s.get("priority", "")
        pri_color = {"high": "#c0392b", "medium": "#e67e22", "low": "#27ae60"}.get(pri, "#666")
        rows_html += f"""
        <tr>
          <td style="white-space:nowrap;font-weight:bold;">{s.get('date','')}</td>
          <td style="white-space:nowrap;">{s.get('day_of_week','')[:3]}</td>
          <td>{badge}</td>
          <td>{s.get('channel','')}</td>
          <td>{s.get('audience','')}</td>
          <td>{s.get('topic_angle','')}</td>
          <td style="white-space:nowrap;">{s.get('send_time_est','')}</td>
          <td style="font-weight:bold;color:{color};white-space:nowrap;">{rev_str}</td>
          <td style="color:{pri_color};font-weight:bold;">{pri}</td>
          <td style="font-size:13px;">{s.get('rationale','')}</td>
          <td style="font-size:13px;">{s.get('goal_alignment','')}</td>
          <td style="font-size:13px;color:#666;">{s.get('adjustment_lever','')}</td>
        </tr>"""

    # Count badges
    count_badges = "".join(
        f'<span style="background:{CONTENT_TYPE_COLORS.get(k,"#666")};color:#fff;'
        f'padding:4px 12px;border-radius:4px;margin:0 4px;font-size:13px;">'
        f'{k}: <strong>{v}</strong></span>'
        for k, v in counts.items() if v
    )

    total_rev_est = sum(s.get("revenue_estimate", 0) for s in slots
                      if s.get("content_type") not in ("seo_blog", "flow_experiment"))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Content Calendar — {month_label}</title>
<style>
  body {{ font-family:Georgia,serif; background:#faf6ee; color:#2c2417; margin:0; padding:24px; }}
  h1 {{ font-size:28px; margin:0 0 4px; }}
  .meta {{ font-size:14px; color:#8b7355; margin:0 0 24px; }}
  .pacing-banner {{ padding:16px 20px; border-radius:6px; margin:0 0 24px;
    background:{pacing_color}22; border-left:4px solid {pacing_color}; }}
  .pacing-banner h2 {{ margin:0 0 6px; color:{pacing_color}; font-size:18px; }}
  .pacing-banner p {{ margin:0; font-size:15px; line-height:1.6; }}
  .stats {{ display:flex; gap:20px; margin:0 0 24px; flex-wrap:wrap; }}
  .stat-box {{ background:#fffdf7; border:1px solid #e8dcc8; border-radius:6px;
    padding:14px 20px; min-width:160px; }}
  .stat-box .label {{ font-size:12px; color:#8b7355; text-transform:uppercase;
    letter-spacing:1px; margin:0 0 4px; }}
  .stat-box .value {{ font-size:22px; font-weight:bold; color:#2c2417; margin:0; }}
  .adjustments {{ background:#fffdf7; border:1px solid #d4a847; border-radius:6px;
    padding:20px 24px; margin:0 0 28px; }}
  .adjustments h3 {{ margin:0 0 12px; font-size:16px; color:#8b4513; }}
  .adjustments ul {{ margin:0; padding-left:20px; }}
  .adjustments li {{ margin:0 0 8px; font-size:15px; line-height:1.5; }}
  .counts {{ margin:0 0 20px; }}
  .table-wrap {{ overflow-x:auto; }}
  table {{ border-collapse:collapse; width:100%; min-width:1200px;
    background:#fffdf7; border-radius:6px; overflow:hidden; font-size:14px; }}
  th {{ background:#2c2417; color:#fffdf7; padding:10px 12px; text-align:left;
    white-space:nowrap; font-size:12px; letter-spacing:0.5px; }}
  td {{ padding:10px 12px; border-bottom:1px solid #f0e8d8; vertical-align:top; }}
  tr:hover td {{ background:#fdf5e6; }}
  .total-row td {{ background:#f5f0e8; font-weight:bold; border-top:2px solid #d4a847; }}
  footer {{ margin:32px 0 0; font-size:12px; color:#8b7355; text-align:center; }}
</style>
</head>
<body>
<h1>📅 Content Calendar — {month_label}</h1>
<p class="meta">Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} ET · Beezy Beez Honey</p>

<div class="pacing-banner">
  <h2>Pacing Status: {pacing_status.upper()}</h2>
  <p>{summary}</p>
</div>

<div class="stats">
  <div class="stat-box">
    <p class="label">Revenue Gap</p>
    <p class="value" style="color:{pacing_color};">${gap:,.0f}</p>
  </div>
  <div class="stat-box">
    <p class="label">Required Daily Rate</p>
    <p class="value">${daily_rate:,.0f}/day</p>
  </div>
  <div class="stat-box">
    <p class="label">Total Slots</p>
    <p class="value">{len(slots)}</p>
  </div>
  <div class="stat-box">
    <p class="label">Projected Revenue</p>
    <p class="value" style="color:#27ae60;">${total_rev_est:,.0f}</p>
  </div>
</div>

<div class="adjustments">
  <h3>🎯 Recommended Adjustments to Exceed Goals</h3>
  <ul>{adj_html}</ul>
</div>

<div class="counts">{count_badges}</div>

<div class="table-wrap">
<table>
<thead>
<tr>
  <th>Date</th><th>Day</th><th>Type</th><th>Channel</th><th>Audience</th>
  <th>Topic / Angle</th><th>Send Time</th><th>Rev. Est.</th><th>Priority</th>
  <th>Rationale</th><th>Goal Alignment</th><th>If It Underperforms</th>
</tr>
</thead>
<tbody>
{rows_html}
<tr class="total-row">
  <td colspan="7">Projected total</td>
  <td>${total_rev_est:,.0f}</td>
  <td colspan="4"></td>
</tr>
</tbody>
</table>
</div>

<footer>Beezy Beez · Content Calendar · {month_label} · Decision stored in decisions table</footer>
</body>
</html>"""


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


def run_monthly() -> dict:
    """Monthly entrypoint — triggered by cron_dispatch.py on the 1st of each month."""
    today = date.today()
    month_start = date(today.year, today.month, 1)
    try:
        return generate(month_start)
    except Exception as e:
        notify_failure(source="calendar/run_monthly", error=str(e))
        raise


if __name__ == "__main__":
    run_monthly()
