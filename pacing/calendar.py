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
import re
from datetime import date, timedelta, datetime, timezone
from decimal import Decimal

import anthropic

from db.connection import get_conn
from lib.slack import notify_failure, post_draft
from pacing.brain import active_goals, compute_pacing_state, top_contributors
from pacing.strategy_snapshot import run_snapshot, save_snapshot

MODEL = "claude-opus-4-6"  # TODO: A/B test 4.7 after Task 8 — May 21 2026

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

def _build_context(month_start: date, end_date: date | None = None) -> str:
    """Build the Opus prompt context.

    month_start  — first day of the month containing the planning window
                   (used for snapshot scoping, MTD math, and report labels).
    end_date     — last day of the planning window (default: end of month).
    """
    if end_date is None and month_start.month == 12:
        month_end = date(month_start.year + 1, 1, 1)
    elif end_date is None:
        month_end = date(month_start.year, month_start.month + 1, 1)
    else:
        month_end = end_date + timedelta(days=1)
    days_in_month = (month_end - month_start).days

    pacing       = _fetch_pacing_context()
    contributors = top_contributors(days=90)
    perf         = _fetch_90d_channel_performance()
    strategies   = _fetch_active_strategies()
    upcoming     = _fetch_upcoming_issues()
    last_issue   = _fetch_last_published_issue()

    seg_data = _fetch_segment_rpr()

    planning_first = str(max(month_start, date.today()))
    planning_last  = str(end_date or (month_end - timedelta(days=1)))

    ctx = {
        "brand": "Beezy Beez Honey — DTC botanical extract honey, women 50+, sleep support, ~$54.95 AOV",
        "planning_month": str(month_start)[:7],
        "planning_period_first_day": planning_first,
        "planning_period_last_day": planning_last,
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
            "hive_mind        — prospect newsletter, EVERY 3 DAYS, 8pm ET, audience=engaged_prospects, EXCLUDE all customers",
            "sleep_audio      — Sleep Better podcast (stories/meditations/you-are-ok), EVERY 3 DAYS alternating with hive_mind, TWO sends per episode: one to active_seal (8:15pm) + one to engaged_customers (8pm, EXCLUDE active_seal)",
            "seo_blog         — SEO blog post on Shopify, NO email send, revenue_estimate=0, 4-6/month",
            "klaviyo_campaign — primary email campaign. Includes: JSH lapsed (weekly), $25 credit offers, BOGO, product features, pre-paid subscription offers (whales/VIP 2-3x/month), Hive Club membership pitches (sniper, EXCLUDE active_seal), seasonal/holiday themed. Anchor at 2pm EDT.",
            "sms_campaign     — SMS blast, 2-3x/WEEK max, active_seal (drops), vip (flash), lapsed_30d (urgency)",
            "flow_experiment  — internal flow A/B test or tuning task, revenue_estimate=0, 1-2/week",
        ],
        "flow_campaign_context": (
            "Flows ~13pct of revenue. Target 70pct. Campaigns carry the load. "
            "QUALITY OVER QUANTITY — fewer sends, higher revenue per send. "
            "Best send time: 2pm EDT. "
            "HIGH FREQ: sleep_audio (every 3d, $1065/send), JSH lapsed_30d (weekly, $923/send), "
            "VIP (3-4x/mo, $930/send), active_seal (every audio + features, $503/send), "
            "hive_mind (every 3d, pipeline builder). "
            "MODERATE: one_time_buyers 2-3x/mo ($664/send), whales/pre-paid 2-3x/mo ($607/send). "
            "LOW: lapsed_90_180d MAX 1x/mo ($190/send). NO gummies-to-niche or cold blasts. "
            "If pacing behind mid-month, increase VIP/lapsed_30d/active_seal first — never lapsed_90_180d. "
            "MANDATORY: hive_mind every 3 days, sleep_audio every 3 days alternating. "
            # Members & Subs cadence widened from 3d to 5-7d on 2026-05-21 — improves audience freshness, reduces over-touch (see beezy-system v2.0 skill)
            "MANDATORY: Members & Subscribers Newsletter (newsletter-format klaviyo_campaign to members_subscribers audience): every 5-7 days, target 6 days between sends. "
            "MANDATORY: cover EVERY day planning_period_first_day through planning_period_last_day."
        ),
    }
    ctx["PRODUCT_CATALOG_RULE"] = (
        "CRITICAL: NEVER invent products, flavors, or features. "
        "Only reference these actual Beezy Beez products: "
        "HONEY FLAVORS: Cinnamon ($49/$59), Caramel ($49/$59), Blood Orange ($59.95/$64.95), "
        "Apple Pie ($59.95/$64.95), Vanilla ($49/$59), Chocolate Strawberry ($49/$59), "
        "Original ($49/$59), Graham Cracker, Strawberry Cheesecake. "
        "PREMIUM: Delicious Calm 1500MG ($64.95), Ultra Strength 3000MG ($129.95). "
        "GUMMIES: Mixed Fruit ($59), Black Cherry ($49), Strawberry ($49.95), "
        "CBN Gummies ($59.95), Trio Bundle ($179.85). "
        "OTHER: Botanical Oil ($54.95/$69.95), Balm ($39.95/$69.95), Lotion ($59.95), "
        "Lip Balm 3pk ($39.95), Tea ($24.95), Gift Box ($191.95). "
        "SUBSCRIPTIONS: Hive Club $19.95/mo or $199.50/yr (45% off, free shipping). "
        "Pre-Paid Annual $199.50/yr. "
        "DOES NOT EXIST: ashwagandha honey, wildflower honey, lavender honey, manuka honey."
    )
    ctx["segment_rpr_data"] = seg_data.get("context_text", "")

    # Already-dispatched slots — Opus must not re-schedule these audiences within 7 days
    try:
        from db.connection import get_conn as _gc
        with _gc() as _c:
            _dispatched = _c.execute(
                """SELECT slot_date::text, content_type, audience
                   FROM calendar_executions
                   WHERE slot_date >= %s AND slot_date < %s
                     AND status NOT IN ('failed', 'skipped')
                   ORDER BY slot_date""",
                (str(max(month_start, date.today() - timedelta(days=7))),
                 str(max(month_start, date.today())))
            ).fetchall()
        if _dispatched:
            ctx["already_dispatched_slots"] = [
                {"date": r[0], "content_type": r[1], "audience": r[2]}
                for r in _dispatched
            ]
            ctx["R2_NOTE"] = (
                "Respect the 7-day cooldown (R2): do NOT schedule any audience "
                "within 7 days of its last dispatched slot above."
            )
    except Exception:
        pass

    # Subject pattern learning — bias Opus toward winning subject type per audience
    try:
        from db.connection import get_conn as _gc
        with _gc() as _c:
            _sp_row = _c.execute("SELECT value FROM agent_state WHERE key='subject_patterns'").fetchone()
        if _sp_row:
            import json as _json
            _patterns = _json.loads(_sp_row[0])
            subject_guidance = {}
            for aud, data in _patterns.items():
                wt = data.get("winning_type")
                if wt:
                    subject_guidance[aud] = f"Use {wt}-style subjects (data-backed winner)"
                else:
                    # Show counts so Opus can see which types have been tested
                    c_cnt = data.get("curiosity", {}).get("count", 0)
                    b_cnt = data.get("benefit", {}).get("count", 0)
                    if c_cnt + b_cnt >= 3:
                        subject_guidance[aud] = f"A/B testing: {c_cnt} curiosity vs {b_cnt} benefit sends, no winner yet"
            if subject_guidance:
                ctx["subject_type_guidance"] = subject_guidance
    except Exception:
        pass

    # Content pillar attribution — show which topic angles drive best RPR
    try:
        from pacing.brain import content_strategy_attribution
        pillars = content_strategy_attribution(days=90)
        if pillars:
            ctx["content_pillar_performance_90d"] = {
                p: {"sends": d["sends"], "avg_rpr": d["avg_rpr"], "total_rev": d["total_revenue"]}
                for p, d in sorted(pillars.items(), key=lambda x: -x[1]["avg_rpr"])
            }
    except Exception:
        pass

    return json.dumps(ctx, indent=2, default=str)


SYSTEM_PROMPT = """You are the revenue-focused content calendar strategist for Beezy Beez Honey.

CRITICAL CONTEXT — internalize before planning:
Flows generate ~29-30% of Klaviyo revenue. The long-term target is 70%. Until flows reach
that target, campaigns must carry the revenue load. This means running campaigns aggressively
NOW while simultaneously strengthening flows. The $150K+ monthly Klaviyo goal requires high
campaign volume. This is the explicit strategy — not a bug.

═══════════════════════════════════════════════════════════════════════════════
HARD RULES — MANDATORY. THESE OVERRIDE ALL OTHER GUIDANCE IN THIS PROMPT.
Violating any of these is an invalid plan.
═══════════════════════════════════════════════════════════════════════════════

MANDATORY RULE 1 — Per-audience touch budget:
No audience receives more than 3 email/SMS sends in any 10-day rolling window.
Active Seal (audience: active_seal): max 3 touches per 10 days with minimum 4-day
gap between sends. All Engaged Customers (audience: engaged_customers): max 3
quality touches per 10 days. VIPs (audience: vip): max 3 touches per 10 days.
Lapsed cohorts (lapsed_30d, lapsed_60d, lapsed_90d, lapsed_180d): max 2 touches
per 10 days per cohort.

MANDATORY RULE 2 — $25 Credit format frequency cap:
$25 Credit format (subject lines containing "$25 Credit" or "$15 credit") sent
to any single audience cohort: maximum once per 14 days. Stacking multiple $25
Credit sends to overlapping cohorts within 14 days is forbidden.

MANDATORY RULE 3 — No speculative sniper followups:
Do NOT generate sniper_followup slots in the calendar. SNIPER Hot List dispatches
are triggered by a downstream process that fires automatically when a parent
campaign produces qualified hot opens/clicks. Calendar slots for sniper_followup
are never appropriate. This overrides any earlier guidance suggesting sniper
follow-ups should be planned.

MANDATORY RULE 4 — Members & Subscribers Newsletter cadence:
The plan must include at least one Members & Subscribers Newsletter slot
(content_type: klaviyo_campaign, audience: members_subscribers,
format: members_subscribers_newsletter) every 5-7 days, target 6 days between
sends. The Members & Subs audience is DISTINCT from engaged_customers — do not
fold these into generic klaviyo_campaign slots to engaged_customers.

MANDATORY RULE 5 — Holiday/named-moment requirement:
When the planning window contains a recognized commercial holiday (Mother's Day,
Memorial Day, July 4th, Labor Day, Black Friday, Cyber Monday, Christmas, New
Year's), at least one named-moment campaign slot must anchor the holiday with a
strategic offer to a high-value audience (VIP, High-AOV, or Whales).
Memorial Day = May 25-26. July 4th = July 3-4. Labor Day = first Monday of September.

MANDATORY RULE 6 — Weekly named-moment rotation:
At least one named-moment campaign slot per 7-day window, no more than 2
consecutive same-hook moments. Tuesday rotation: Anchor / Buy X Get Y /
Editorial / SNIPER (rotate weekly). Saturday named-moment rotation acceptable
for offers.

MANDATORY RULE 7 — Strict window enforcement:
Slots MUST have slot_date >= planning_period_first_day AND slot_date <=
planning_period_last_day. Slots dated before planning_period_first_day are
INVALID and must not be generated, even if useful sends could be placed there.
The calendar generates the requested window only.

MANDATORY RULE 8 — Slot density discipline:
Total paid-send slots (email + SMS, excluding seo_blog and flow_experiment with
$0 projected revenue) should average 1.4-2.0 per day across the planning window.
A 10-day window has a target of 14-20 paid sends. Exceeding 25 paid sends in 10
days indicates audience over-touch and is forbidden.

═══════════════════════════════════════════════════════════════════════════════

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

SMS: 2-3x per WEEK. active_seal (product drops), vip (flash sales), lapsed_30d (urgency). Short + high-impact.

FLOW EXPERIMENTS: 1–2 per week. No revenue. Critical to growing flow contribution %.

SEO BLOG: 4–6/month, spread evenly. revenue_estimate = 0. No email send involved.

SLEEP AUDIO — PRODUCTION REQUIREMENTS (mandatory):
Every sleep_audio slot topic_angle MUST be a specific, producible episode concept.
NEVER write "Sleep Better Podcast episode" or any generic placeholder.
Format: "[Episode type]: [Working title / creative direction]"
Valid episode types with example concepts:
  - Sleep story: A slow train crossing the Scottish Highlands at midnight
  - Sleep story: Drifting down the Amazon on a wooden boat after dark
  - Sleep story: An autumn evening alone in a French farmhouse kitchen
  - Sleep story: A lighthouse keeper's last watch of the season
  - Guided meditation: 20-min body scan for women who can't turn off their minds
  - Guided meditation: Releasing the day — shoulder-to-hip tension release for 50+
  - Soundscape: Pacific rainforest at 3am — rain, frogs, distant water
  - Soundscape: Summer thunderstorm rolling across a wheat field at dusk
  - Affirmation: You've done enough today — 12-min evening surrender
Plan 5–6 distinct concepts per month. No repeating episode type on consecutive sleep_audio slots.

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


# ── Strategy Snapshot integration (Task 2) ────────────────────────────────────

class CalendarValidationError(RuntimeError):
    """Raised when generated calendar slots fail snapshot-citation validation."""
    pass


def format_trajectories_table(trajectories: dict) -> str:
    """snapshot.format_trajectories → markdown table.
    Columns: format | sends_30d | rpr_30d | rpr_91-180d | trend | change."""
    if not trajectories:
        return "_(no trajectory data)_"
    lines = [
        "| Format | Sends 30d | RPR 30d | RPR 91–180d | Trend | Change |",
        "|---|---:|---:|---:|---|---:|",
    ]
    def _rpr30(item):
        return (item[1].get("Last 30d") or {}).get("rpr", 0)
    for fmt, t in sorted(trajectories.items(), key=_rpr30, reverse=True):
        recent = t.get("Last 30d") or {}
        old = t.get("91-180d") or {}
        change = t.get("change_pct")
        change_s = f"{change:+.1f}%" if change is not None else "—"
        lines.append(
            f"| {fmt} | {recent.get('sends', 0)} | "
            f"${recent.get('rpr', 0):.3f} | ${old.get('rpr', 0):.3f} | "
            f"{t.get('trend', '?')} | {change_s} |"
        )
    return "\n".join(lines)


def top_rpr_table(rows: list) -> str:
    """Top-RPR slice → markdown table.
    Columns: date | campaign | format | recipients | revenue | rpr."""
    if not rows:
        return "_(no qualifying campaigns)_"
    lines = [
        "| Date | Campaign | Format | Recipients | Revenue | RPR |",
        "|---|---|---|---:|---:|---:|",
    ]
    for r in rows:
        name = (r.get("name") or "")[:60]
        lines.append(
            f"| {r.get('date', '')} | {name} | {r.get('format', '')} | "
            f"{r.get('recipients', 0):,} | ${r.get('revenue', 0):,.0f} | "
            f"${r.get('rpr', 0):.3f} |"
        )
    return "\n".join(lines)


def recency_audit_table(audit: dict) -> str:
    """snapshot.recency_audit → markdown table, sorted FRESH → WARM → REST."""
    if not audit:
        return "_(no audiences touched in the lookback window)_"
    status_order = {"FRESH": 0, "WARM": 1, "REST": 2}
    items = sorted(
        audit.items(),
        key=lambda kv: (status_order.get(kv[1].get("status", "REST"), 99),
                        -kv[1].get("last_touch_days_ago", 0)),
    )
    lines = [
        "| Audience | Status | Last touch (d ago) | Touches last 14d |",
        "|---|---|---:|---:|",
    ]
    for aud, data in items:
        lines.append(
            f"| {aud} | {data.get('status', '?')} | "
            f"{data.get('last_touch_days_ago', '?')} | "
            f"{data.get('touches_last_14d', 0)} |"
        )
    return "\n".join(lines)


def abandoned_winners_table(winners: list) -> str:
    """snapshot.abandoned_winners → markdown table."""
    if not winners:
        return "_(no abandoned winners — all historical formats still in rotation)_"
    lines = [
        "| Last sent | Campaign | Format | Revenue |",
        "|---|---|---|---:|",
    ]
    for w in winners:
        name = (w.get("name") or "")[:60]
        lines.append(
            f"| {w.get('date', '')} | {name} | {w.get('format', '')} | "
            f"${w.get('revenue', 0):,.0f} |"
        )
    return "\n".join(lines)


def _build_snapshot_section(snapshot: dict, revenue_goal: float | None = None) -> str:
    """Compose the snapshot prompt block that Opus must consult and cite."""
    lb = snapshot.get("live_baseline", {})
    locked_rules = snapshot.get("locked_rules_active", [])
    goal_line = f"${revenue_goal:,.0f}" if revenue_goal else "as stated by Alan"
    return (
        "REVENUE BASELINE (LIVE FROM KLAVIYO):\n"
        f"- Campaigns MTD: ${lb.get('campaigns_mtd', 0):,.2f}\n"
        f"- Flows MTD: ${lb.get('flows_mtd', 0):,.2f}\n"
        f"- Daily pace: Campaigns ${lb.get('campaigns_daily_pace', 0):,.2f}/day, "
        f"Flows ${lb.get('flows_daily_pace', 0):,.2f}/day\n"
        f"- Days remaining in month: {lb.get('days_remaining_in_month', '?')}\n"
        f"- Projected exit at current pace: ${lb.get('projected_exit_at_current_pace', 0):,.2f}\n"
        f"- Revenue goal: {goal_line}\n\n"
        "FORMAT TRAJECTORIES (RPR trends):\n"
        + format_trajectories_table(snapshot.get("format_trajectories", {})) + "\n\n"
        "TOP RPR LAST 60d (cite by campaign name or format):\n"
        + top_rpr_table((snapshot.get("top_25_rpr_last_60d") or [])[:10]) + "\n\n"
        "RECENCY AUDIT (do NOT schedule REST audiences):\n"
        + recency_audit_table(snapshot.get("recency_audit", {})) + "\n\n"
        "ABANDONED WINNERS (formats earning $1K+ not used recently — consider reviving):\n"
        + abandoned_winners_table(snapshot.get("abandoned_winners", [])) + "\n\n"
        "LOCKED RULES (must enforce):\n"
        + "\n".join(f"- {r}" for r in locked_rules) + "\n\n"
        "For each slot, the `rationale` field MUST cite a snapshot data point above "
        "by name — a format from FORMAT TRAJECTORIES, an audience from the RECENCY "
        "AUDIT, a campaign from TOP RPR, or an entry from ABANDONED WINNERS. Slots "
        "with non-zero revenue_estimate whose rationale does not cite snapshot data "
        "will be REJECTED.\n"
    )


# Citation matcher — normalizes both sides so that punctuated format names
# (e.g. "Members/Engaged Newsletter", "SNIPER (Hot List)") match the natural-
# language forms Opus writes ("members engaged newsletter", "SNIPER format").
# Parens, ampersands, pipes, slashes, dashes, underscores all collapse to spaces.
_CITATION_PUNCT_RE = re.compile(r"[.,—–\-_|/()&]+")
_CITATION_WS_RE    = re.compile(r"\s+")

def _normalize_citation(s: str) -> str:
    """Lowercase, replace .,—–-_|/()& with spaces, collapse whitespace,
    then strip a leading article ('the ', 'a ', 'an '). Applied to both
    allowlist tokens AND rationale text — so 'The Newsletter Issue 5'
    (token) matches 'Newsletter Issue 5' (rationale) after normalization."""
    s = (s or "").lower()
    s = _CITATION_PUNCT_RE.sub(" ", s)
    s = _CITATION_WS_RE.sub(" ", s).strip()
    for prefix in ("the ", "an ", "a "):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s


# Abbreviation aliases — applied to ALLOWLIST tokens only (not rationale).
# For each token, every alias key that appears in the token spawns a sibling
# token with the key substituted by its value. So 'members subscribers' becomes
# {'members subscribers', 'members subs'} once the subscribers→subs alias is
# applied, and either variant can substring-match a rationale.
#
# This is a list of (canonical, paraphrase) pairs, NOT a dict, so the same
# canonical phrase can expand to several paraphrase variants without key
# collisions. Bidirectional pairs handle both directions: snapshot says
# 'Subscribers', rationale says 'Subs' — and vice-versa.
#
# Aliases match POST-NORMALIZE tokens (lowercase, no .,—–-_|/()& punctuation).
# So 'M&S' arrives as 'm s' and 'Members & Subscribers' as 'members subscribers'.
_ABBREVIATION_ALIASES: list[tuple[str, str]] = [
    ("subs",                        "subscribers"),
    ("subscribers",                 "subs"),
    ("mems",                        "members"),
    ("members",                     "mems"),
    ("members subscribers",         "m s"),               # canonical → post-normalize "M&S"
    ("members engaged newsletter",  "members newsletter"),# canonical → drop the descriptor
    ("newsletter issue",            "issue"),
    ("sleep audio",                 "sleep_audio"),
    ("active seal",                 "active_seal"),
    ("engaged customers",           "engaged_customers"),
    ("hive mind",                   "hive_mind"),
]


def _expand_alias_variants(token: str) -> set[str]:
    """Return {token} plus every single-substitution variant where an alias
    key contained in `token` is replaced by its value."""
    variants = {token}
    for src, dst in _ABBREVIATION_ALIASES:
        if src and src in token:
            variants.add(token.replace(src, dst))
    return variants


# Section header citations Opus naturally writes when grounding a rationale.
# These are sections of the snapshot rendered by _build_snapshot_section;
# referencing them by name is a valid form of citation.
_SNAPSHOT_SECTION_TOKENS = (
    "RECENCY AUDIT", "TOP RPR", "TOP-RPR",
    "ABANDONED WINNERS", "FORMAT TRAJECTORIES", "FORMAT-TRAJECTORIES",
    "LIVE BASELINE", "LOCKED RULES", "MTD",
)


# ── Post-process constraint filter (HARD RULES 1 + 2) ─────────────────────────
# Deterministic enforcement layer that runs AFTER Opus returns but BEFORE
# validation. Opus has been observed to acknowledge the rules in its rationale
# ("CANNOT SCHEDULE") and then schedule the slot anyway. Prompt-level guidance
# is not load-bearing — this filter is.

_TOUCH_BUDGET = {
    "active_seal":         3,
    "engaged_customers":   3,
    "vip":                 3,
    "lapsed_30d":          2,
    "lapsed_60d":          2,
    "lapsed_90d":          2,
    "lapsed_180d":         2,
    "whales":              2,
    "members_subscribers": 2,
    "one_time_buyers":     2,
}
_DEFAULT_TOUCH_BUDGET = 3   # any audience not listed
_ACTIVE_SEAL_MIN_GAP_DAYS = 4
_TOUCH_WINDOW_DAYS = 10
_CREDIT_WINDOW_DAYS = 14
_NON_TOUCH_TYPES = {"seo_blog", "flow_experiment"}


def _is_credit_slot(s: dict) -> bool:
    """True if this slot is a $25/$15 Credit-format send (any phrasing)."""
    haystack = " ".join((
        s.get("format") or "",
        s.get("topic_angle") or "",
    )).lower()
    return any(needle in haystack for needle in ("$25 credit", "$15 credit", "25 credit"))


def _filter_slots_by_constraints(slots: list[dict]) -> tuple[list[dict], list[dict]]:
    """Apply HARD RULES 1 + 2 as a deterministic post-process drop.

    Rule 1: per-audience touch budget in a rolling 10-day window. Threshold
            per audience from _TOUCH_BUDGET (default 3 for unlisted). Slots
            with content_type in {seo_blog, flow_experiment} are exempt — they
            generate no email/SMS touch.
    Rule 1 (active_seal extra): minimum 4-day gap between consecutive
            active_seal sends, regardless of trailing-window count.
    Rule 2: $25/$15 Credit format per audience cohort capped at 1 send per
            14-day rolling window.

    Slots are evaluated in (date, send_time_est) order. For each candidate,
    we look at ALREADY-KEPT slots in the trailing window. This means earlier
    slots win; later duplicates are dropped. Returns (kept, dropped).
    """
    if not slots:
        return [], []

    def _slot_date(s: dict) -> date | None:
        d = s.get("date")
        if not d:
            return None
        try:
            return date.fromisoformat(d)
        except (ValueError, TypeError):
            return None

    ordered = sorted(
        slots,
        key=lambda x: (
            _slot_date(x) or date.max,
            x.get("send_time_est") or "00:00",
        ),
    )

    kept: list[dict] = []
    dropped: list[dict] = []

    for s in ordered:
        d = _slot_date(s)
        if d is None:
            kept.append(s)  # malformed-date slots are not the filter's job
            continue

        ctype = s.get("content_type") or ""
        aud   = (s.get("audience") or "").strip()
        reasons: list[str] = []

        # Non-touch slot types bypass Rule 1, but Rule 2 still applies if the
        # format is somehow Credit-flavored (defensive — shouldn't happen).
        is_touch = ctype not in _NON_TOUCH_TYPES

        # Rule 1: trailing 10-day window per-audience cap.
        if is_touch and aud:
            window_start = d - timedelta(days=_TOUCH_WINDOW_DAYS - 1)
            recent_same_aud = [
                k for k in kept
                if (k.get("audience") or "").strip() == aud
                and (k.get("content_type") or "") not in _NON_TOUCH_TYPES
                and (kd := _slot_date(k)) is not None
                and window_start <= kd <= d
            ]
            cap = _TOUCH_BUDGET.get(aud, _DEFAULT_TOUCH_BUDGET)
            if len(recent_same_aud) >= cap:
                reasons.append("rule_1_touch_budget")

        # Rule 1: active_seal 4-day minimum gap (overlays the cap above).
        if is_touch and aud == "active_seal":
            last_active = max(
                (kd for k in kept
                 if (k.get("audience") or "").strip() == "active_seal"
                 and (k.get("content_type") or "") not in _NON_TOUCH_TYPES
                 and (kd := _slot_date(k)) is not None),
                default=None,
            )
            if last_active and (d - last_active).days < _ACTIVE_SEAL_MIN_GAP_DAYS:
                if "rule_1_touch_budget" not in reasons:
                    reasons.append("rule_1_active_seal_gap")

        # Rule 2: $25/$15 Credit format, 14-day per-cohort cooldown.
        if _is_credit_slot(s) and aud:
            window_start = d - timedelta(days=_CREDIT_WINDOW_DAYS - 1)
            recent_credit = [
                k for k in kept
                if (k.get("audience") or "").strip() == aud
                and _is_credit_slot(k)
                and (kd := _slot_date(k)) is not None
                and window_start <= kd <= d
            ]
            if recent_credit:
                reasons.append("rule_2_credit_cooldown")

        if reasons:
            dropped.append({
                "date":               s.get("date"),
                "content_type":       ctype,
                "audience":           aud,
                "format":             s.get("format") or s.get("topic_angle") or "",
                "projected_revenue":  float(s.get("revenue_estimate", 0) or 0),
                "drop_reason":        " + ".join(reasons),
            })
        else:
            kept.append(s)

    return kept, dropped


def _inject_missing_members_subs_newsletter(
    slots: list[dict],
    snapshot: dict,
    start_date: date,
    end_date: date,
) -> tuple[list[dict], list[dict]]:
    """Cadence guard for Rule 4. If the planning window contains no Members &
    Subs Newsletter slot AND the most recent newsletter send (from snapshot)
    is > 7 days before start_date, inject one on max(start_date, last+6).

    Returns (updated_slots, injected_slots). The injected slot carries
    `injected_by_filter: True` and a rationale citing the snapshot baseline
    (so it passes the citation validator).
    """
    # Already present in window?
    for s in slots:
        aud = (s.get("audience") or "").strip().lower()
        fmt = (s.get("format") or "").lower()
        if aud == "members_subscribers" or "members_subscribers_newsletter" in fmt:
            return slots, []
        if "members" in aud and ("subs" in aud or "subscribers" in aud):
            return slots, []

    # Find most recent M&S newsletter send in snapshot.
    last: dict | None = None
    for r in (snapshot.get("top_25_rpr_last_60d") or []):
        name = r.get("name") or ""
        fmt = r.get("format") or ""
        is_ms = (
            ("Members" in name and ("Subscribers" in name or "Subs" in name))
            or "Newsletter Issue" in name
            or "Members/Engaged Newsletter" in fmt
        )
        if not is_ms:
            continue
        try:
            d = date.fromisoformat(r.get("date") or "")
        except (ValueError, TypeError):
            continue
        if last is None or d > last["date"]:
            last = {
                "date":    d,
                "name":    name,
                "revenue": float(r.get("revenue") or 0),
                "rpr":     float(r.get("rpr") or 0),
            }

    if last is None:
        return slots, []  # no baseline to anchor injection

    days_since = (start_date - last["date"]).days
    if days_since <= 7:
        return slots, []  # cadence not yet overdue

    inject_date = max(start_date, last["date"] + timedelta(days=6))
    if inject_date > end_date:
        return slots, []  # cannot fit in the requested window

    # Pull issue number from the campaign name if we can find it ("Issue 5").
    issue_label = last["name"]
    last_date_str = last["date"].strftime("%-m/%-d")
    rationale = (
        f"AUTO-INJECTED per Rule 4 enforcement. Last Members & Subscribers "
        f"Newsletter was {days_since}d ago — overdue per 5-7d mandatory "
        f"cadence. Prior send ({issue_label}) on {last_date_str} drove "
        f"${last['revenue']:,.0f} at ${last['rpr']:.3f} RPR — top performer."
    )

    injected = {
        "date":             inject_date.isoformat(),
        "day_of_week":      inject_date.strftime("%A"),
        "content_type":     "klaviyo_campaign",
        "channel":          "email",
        "audience":         "members_subscribers",
        "format":           "members_subscribers_newsletter",
        "topic_angle":      "auto-injected: M&S Newsletter cadence guard",
        "send_time_est":    "14:00",
        "priority":         "high",
        "revenue_estimate": 1347,
        "needs_page":       False,
        "discount_code":    "",
        "discount_pct":     0,
        "rationale":        rationale,
        "goal_alignment":   "Maintain Members & Subscribers Newsletter cadence (Rule 4)",
        "adjustment_lever": "Reschedule via Slack if conflicts with another high-value send",
        "injected_by_filter": True,
    }
    return slots + [injected], [injected]


def _validate_slot_citations(cal: dict, snapshot: dict) -> list[dict]:
    """Return list of validation errors — empty list = valid.

    Each error is a dict: {date, content_type, audience, revenue_estimate,
    rationale, reason}. Slots with revenue_estimate <= 0 are exempt (SEO blog
    / flow experiments). Citation = normalized substring match against any
    format name, audience name, top-RPR/revenue campaign name,
    abandoned-winner name, or snapshot section header. Both allowlist tokens
    AND rationale text pass through _normalize_citation before matching so
    that 'Members/Engaged Newsletter' (token) and 'members & subs newsletter'
    (rationale) compare on a level field — punctuation and pipe separators
    don't block the match.
    """
    tokens: set[str] = set()

    def _add(raw: str) -> None:
        """Normalize + alias-expand + insert. Empty/blank tokens are dropped."""
        norm = _normalize_citation(raw)
        if norm:
            tokens.update(_expand_alias_variants(norm))

    # Format-trajectory keys (e.g. "Members/Engaged Newsletter", "SNIPER (Hot List)").
    for fmt in (snapshot.get("format_trajectories") or {}):
        if fmt and fmt != "Other":
            _add(fmt)
    # Recency-audit keys: 10 named audiences (super_engaged, vip, lapsed_30d, ...)
    # plus opaque Klaviyo segment IDs (skipped — too short/cryptic to anchor citations).
    for aud in (snapshot.get("recency_audit") or {}):
        if aud and not (len(aud) == 6 and aud[0].isupper()):
            _add(aud)
    # Top campaigns: extract BOTH name (full pipe-separated title) AND format,
    # plus each pipe-separated segment ≥8 chars (so "$25 Credit | One-Time
    # Buyers Lapsed 60-90d" yields tokens for the format, the audience phrase,
    # and the lapsed-cohort phrase — Opus rarely cites the whole campaign title
    # verbatim, but cites these parts often).
    for r in ((snapshot.get("top_25_rpr_last_60d") or [])[:25]
              + (snapshot.get("top_25_revenue_last_60d") or [])[:25]):
        name = (r.get("name") or "").strip()
        if len(name) >= 8:
            _add(name)
        for part in (name.split("|") if "|" in name else []):
            part = part.strip()
            if len(part) >= 8:
                _add(part)
        fmt = (r.get("format") or "").strip()
        if fmt and fmt != "Other":
            _add(fmt)
    for w in (snapshot.get("abandoned_winners") or []):
        name = (w.get("name") or "").strip()
        if len(name) >= 8:
            _add(name)
        for part in (name.split("|") if "|" in name else []):
            part = part.strip()
            if len(part) >= 8:
                _add(part)
        fmt = (w.get("format") or "").strip()
        if fmt and fmt != "Other":
            _add(fmt)
    # Each slot's own audience identifier — rationale citing the slot's
    # targeted audience (e.g. "one_time_buyers RPR $0.056") is valid grounding
    # even when that audience isn't a recency_audit key.
    for s in cal.get("slots", []):
        aud = (s.get("audience") or "").strip()
        if aud:
            _add(aud)
    for sh in _SNAPSHOT_SECTION_TOKENS:
        _add(sh)
    tokens.discard("")

    errors: list[dict] = []
    for s in cal.get("slots", []):
        rev = float(s.get("revenue_estimate", 0) or 0)
        if rev <= 0:
            continue  # SEO blog / flow experiment exempt
        rationale_norm = _normalize_citation(s.get("rationale") or "")
        if not any(tok in rationale_norm for tok in tokens):
            errors.append({
                "date":             s.get("date"),
                "content_type":     s.get("content_type"),
                "audience":         s.get("audience"),
                "revenue_estimate": rev,
                "rationale":        s.get("rationale") or "",
                "reason":           "rationale does not cite snapshot data",
            })
    return errors


def _format_citation_error(e: dict) -> str:
    """One-line human form of a validation error dict (for logs / Slack / CLI)."""
    return (
        f"slot {e.get('date', '?')} "
        f"({e.get('content_type', '?')} → {e.get('audience', '?')}, "
        f"${float(e.get('revenue_estimate', 0) or 0):,.0f}): "
        f"{e.get('reason', 'invalid citation')} — "
        f"{(e.get('rationale') or '')[:120]!r}"
    )


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


def _call_opus(context_str: str, snapshot_section: str = "") -> dict:
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
                (snapshot_section + "\n\n" if snapshot_section else "")
                + "Here is the current performance and planning context. "
                "Generate the content calendar for the planning period shown below. "
                "Cover EVERY day from planning_period_first_day through planning_period_last_day "
                "(inclusive) — do not stop early. "
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
.card.warn{border-left:3px solid #e74c3c;}
.card.good{border-left:3px solid #27ae60;}
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
  <div class="card"><div class="label">Campaigns MTD</div><div class="value">$""" + f"{float(cal.get('campaign_revenue_mtd', 0)):,.2f}" + """</div></div>
  <div class="card"><div class="label">Calendar Projected</div><div class="value">$""" + f"{planned_total:,.0f}" + """</div></div>
  <div class="card"><div class="label">Flows MTD</div><div class="value">$""" + f"{float(cal.get('flow_revenue_mtd', 0)):,.2f}" + """</div></div>
  <div class="card"><div class="label">Total Projected</div><div class="value" style="font-size:26px;">$""" + f"{float(cal.get('total_projected', planned_total + actual_total)):,.0f}" + """</div></div>
  <div class="card """ + ("good" if float(cal.get('gap_to_goal', 0)) <= 0 else "warn") + """"><div class="label">Goal: $""" + f"{float(cal.get('monthly_goal', 150000)):,.0f}" + """</div><div class="value">Gap: $""" + f"{abs(float(cal.get('gap_to_goal', 0))):,.0f}" + """</div></div>
</div>
<div class="summary">
  <div class="card"><div class="label">Email Campaigns</div><div class="value">""" + str(sum(1 for s in slots if s.get("channel","") == "email" and s.get("content_type","") == "klaviyo_campaign")) + """</div></div>
  <div class="card"><div class="label">Hive Mind Issues</div><div class="value">""" + str(sum(1 for s in slots if s.get("content_type","") == "hive_mind")) + """</div></div>
  <div class="card"><div class="label">Sleep Audio</div><div class="value">""" + str(sum(1 for s in slots if s.get("content_type","") == "sleep_audio")) + """</div></div>
  <div class="card"><div class="label">SMS</div><div class="value">""" + str(sum(1 for s in slots if s.get("content_type","") == "sms_campaign")) + """</div></div>
  <div class="card"><div class="label">SEO Blog</div><div class="value">""" + str(sum(1 for s in slots if s.get("content_type","") == "seo_blog")) + """</div></div>
  <div class="card"><div class="label">Total Slots</div><div class="value">""" + str(len(slots)) + """</div></div>
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

def _persist_calendar(month_start: date, calendar_dict: dict, window_label: str | None = None) -> str:
    summary = calendar_dict.get("summary", "")
    label   = window_label or month_start.strftime("%B %Y")
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
                    f"Calendar for {label}: {summary}",
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

def _post_slack_summary(month_start: date, cal: dict, decision_id: str, report_path: str, page_url: str = "", window_label: str | None = None) -> None:
    """Post a tight executive summary to Slack. No slot dump."""
    month_label   = window_label or month_start.strftime("%B %Y")
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

def generate(
    start_date: date,
    end_date: date | None = None,
    revenue_goal: int | None = None,
    persist: bool = True,
) -> dict:
    """Generate a content calendar for [start_date, end_date]. Returns the calendar dict.

    start_date    — first day of the planning window (inclusive).
    end_date      — last day of the planning window (default: end of start_date's month).
    revenue_goal  — monthly revenue goal in dollars; plumbed into the snapshot section
                    and cal["monthly_goal"]. Default 150000 if not provided.
    persist       — if True (default), write to decisions, generate HTML report,
                    publish to Shopify, post Slack summary. If False, return the
                    calendar dict only — no side effects.
    """
    import time as _time
    month_start = start_date.replace(day=1)
    if end_date is None:
        if month_start.month == 12:
            end_date = date(month_start.year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(month_start.year, month_start.month + 1, 1) - timedelta(days=1)
    _full_month_end = (date(month_start.year + 1, 1, 1) - timedelta(days=1)
                       if month_start.month == 12
                       else date(month_start.year, month_start.month + 1, 1) - timedelta(days=1))
    is_partial_window = not (start_date == month_start and end_date == _full_month_end)
    if is_partial_window:
        window_label = f"{start_date.strftime('%b %-d')}-{end_date.strftime('%-d')}, {start_date.strftime('%Y')}"
    else:
        window_label = start_date.strftime('%B %Y')
    print(f"[calendar] Generating calendar for {window_label}...")

    # Mandatory Strategy Snapshot before any prompt assembly (Task 2).
    print("[calendar] Running Strategy Snapshot...")
    snapshot = run_snapshot()
    snapshot_path = save_snapshot(snapshot)
    print(f"[calendar]   Snapshot saved: {snapshot_path}")
    snapshot_section = _build_snapshot_section(snapshot, revenue_goal=revenue_goal)

    print("[calendar] Fetching context data...")
    context_str = _build_context(month_start, end_date=end_date)
    print(f"[calendar]   Context: {len(context_str):,} chars")

    print("[calendar] Calling Opus (this may take 30-60 seconds)...")
    cal = _call_opus(context_str, snapshot_section)
    slots = cal.get("slots", [])
    print(f"[calendar]   {len(slots)} slots generated")

    # Deterministic constraint filter (HARD RULES 1 + 2). Runs BEFORE
    # validation: validator should only ever see slots that survived the
    # filter. Opus has been observed to acknowledge the touch budget in its
    # rationale and then schedule the slot anyway — the prompt is not
    # load-bearing for these constraints; this filter is.
    kept_slots, dropped_slots = _filter_slots_by_constraints(slots)
    cal["slots"] = kept_slots
    cal["dropped_slots"] = dropped_slots
    if dropped_slots:
        print(f"[calendar]   Post-process filter dropped {len(dropped_slots)} slot(s) "
              f"(Rule 1 touch budget / Rule 2 credit cooldown); "
              f"{len(kept_slots)} kept.")

    # Rule 4 cadence guard — inject a Members & Subs Newsletter slot if the
    # window contains none and the last send (per snapshot) is overdue.
    kept_slots, injected_slots = _inject_missing_members_subs_newsletter(
        kept_slots, snapshot, start_date, end_date,
    )
    cal["slots"] = kept_slots
    cal["injected_slots"] = injected_slots
    if injected_slots:
        for inj in injected_slots:
            print(f"[calendar]   Injected Members & Subs Newsletter slot on "
                  f"{inj['date']} (Rule 4 cadence guard).")
    slots = kept_slots

    # Snapshot-citation validation. Runs BEFORE the auto-fill block below —
    # auto-filled slots have canned rationales that don't cite snapshot data.
    # In dry-run (persist=False) validation is ADVISORY: errors are attached
    # to cal['validation_errors'] so the caller can display them alongside
    # the slot table, but generation continues. In --save-plan mode
    # (persist=True) validation is a hard gate — we raise so a non-citing
    # plan never lands in the decisions table.
    citation_errors = _validate_slot_citations(cal, snapshot)
    cal["validation_errors"] = citation_errors
    if citation_errors:
        msg = (
            f"Calendar validation failed: {len(citation_errors)} slot(s) "
            "missing snapshot citations.\n  - "
            + "\n  - ".join(_format_citation_error(e) for e in citation_errors[:20])
        )
        print(f"[calendar]   {msg}")
        if persist:
            raise CalendarValidationError(msg)
        else:
            print(f"[calendar]   persist=False — validation is advisory, continuing.")

    # Post-process: fill any missing days that Opus skipped
    if slots:
        from datetime import timedelta as _td
        month_end_d = end_date
        start_d = max(start_date, date.today())
        existing_dates = set(s.get("date","") for s in slots)

        # Content-aware auto-fill: Hive Mind every 3 days, sleep audio every 3 days
        _segments = ["lapsed_30d", "vip", "engaged_customers", "one_time_buyers", "whales"]
        _rpr = {"lapsed_30d":0.267,"vip":0.161,"engaged_customers":0.101,"one_time_buyers":0.056,"whales":0.658,"engaged_prospects":0.064,"active_seal":1.268}
        _ls = {"lapsed_30d":3618,"vip":5424,"engaged_customers":13340,"one_time_buyers":12951,"whales":1038,"engaged_prospects":12002,"active_seal":511}
        _seg_idx = 0
        _DAYS = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]

        # Find last Hive Mind and sleep audio dates from existing slots
        hm_dates = sorted([s["date"] for s in slots if s.get("content_type") == "hive_mind"])
        sa_dates = sorted([s["date"] for s in slots if s.get("content_type") == "sleep_audio"])
        last_hm = date.fromisoformat(hm_dates[-1]) if hm_dates else start_d - _td(days=1)
        last_sa = date.fromisoformat(sa_dates[-1]) if sa_dates else start_d

        d = start_d
        added = 0
        while d <= month_end_d:
            ds = str(d)
            if ds not in existing_dates:
                day_slots_added = []
                # Check if Hive Mind is due (every 3 days from last)
                if (d - last_hm).days >= 3:
                    day_slots_added.append({
                        "date": ds, "day_of_week": _DAYS[d.weekday()],
                        "content_type": "hive_mind", "channel": "email",
                        "audience": "engaged_prospects", "topic_angle": "The Hive Mind — prospect newsletter (auto-scheduled)",
                        "send_time_est": "20:00", "priority": "high",
                        "revenue_estimate": round(_rpr["engaged_prospects"] * _ls["engaged_prospects"]),
                        "needs_page": False, "discount_code": "", "discount_pct": 0,
                        "exclusions": "ALL CUSTOMERS",
                        "rationale": "Hive Mind every 3 days", "goal_alignment": "Content cadence",
                        "adjustment_lever": "Adjust via Slack edit",
                    })
                    last_hm = d
                # Check if sleep audio is due (every 3 days, offset from HM)
                elif (d - last_sa).days >= 3:
                    for aud, time_str in [("active_seal", "20:15"), ("engaged_customers", "20:00")]:
                        day_slots_added.append({
                            "date": ds, "day_of_week": _DAYS[d.weekday()],
                            "content_type": "sleep_audio", "channel": "email",
                            "audience": aud, "topic_angle": "Sleep audio episode (auto-scheduled)",
                            "send_time_est": time_str, "priority": "high",
                            "revenue_estimate": round(_rpr[aud] * _ls[aud]),
                            "needs_page": False, "discount_code": "", "discount_pct": 0,
                            "exclusions": "Active Seal sent separately" if aud == "engaged_customers" else "",
                            "rationale": "Sleep audio every 3 days", "goal_alignment": "Content cadence",
                            "adjustment_lever": "Adjust via Slack edit",
                        })
                    last_sa = d

                # Always add a regular campaign slot too
                seg = _segments[_seg_idx % len(_segments)]
                _seg_idx += 1
                day_slots_added.append({
                    "date": ds, "day_of_week": _DAYS[d.weekday()],
                    "content_type": "klaviyo_campaign", "channel": "email",
                    "audience": seg, "topic_angle": "Rotating segment send (auto-filled)",
                    "send_time_est": "14:00", "priority": "medium",
                    "revenue_estimate": round(_rpr.get(seg,0.1) * _ls.get(seg,5000)),
                    "needs_page": False, "discount_code": "", "discount_pct": 0,
                    "rationale": "Auto-filled to cover full month", "goal_alignment": "Fill gap",
                    "adjustment_lever": "Replace via Slack edit",
                })

                slots.extend(day_slots_added)
                added += len(day_slots_added)
            d += _td(days=1)

        if added > 0:
            slots.sort(key=lambda s: s.get("date",""))
            cal["slots"] = slots
            print(f"[calendar]   Auto-filled {added} missing days through {month_end_d}")
        else:
            print(f"[calendar]   Full month coverage verified through {month_end_d}")

    # Live MTD revenue from the Strategy Snapshot. Field names preserved for
    # the HTML dashboard cards (this file) and the Slack `view calendar` command
    # (agents/slack_agent.py) which read campaign_revenue_mtd / flow_revenue_mtd
    # off the persisted decision JSONB.
    planned_total = sum(float(s.get('revenue_estimate', 0)) for s in cal.get('slots', []))
    _cr = float(snapshot['live_baseline'].get('campaigns_mtd', 0))
    _fr = float(snapshot['live_baseline'].get('flows_mtd', 0))
    cal['campaign_revenue_mtd'] = _cr
    cal['flow_revenue_mtd'] = _fr
    cal['calendar_projected_revenue'] = round(planned_total, 2)
    cal['total_projected'] = round(_cr + _fr + planned_total, 2)
    monthly_goal = revenue_goal if revenue_goal is not None else 150000
    cal['monthly_goal'] = monthly_goal
    cal['gap_to_goal'] = round(monthly_goal - (_cr + _fr + planned_total), 2)
    cal['month'] = month_start.strftime("%Y-%m")
    cal['window'] = {"start": start_date.isoformat(), "end": end_date.isoformat()}

    if not persist:
        print(f"[calendar] persist=False — skipping decisions write + HTML + Shopify + Slack.")
        return cal

    print("[calendar] Persisting to decisions table...")
    # Retry DB save in case Neon connection died during Opus call
    for attempt in range(3):
        try:
            decision_id = _persist_calendar(month_start, cal, window_label=window_label)
            print(f"[calendar]   decision_id: {decision_id}")
            break
        except Exception as e:
            print(f"[calendar]   DB save attempt {attempt+1} failed: {e}")
            if attempt < 2:
                _time.sleep(2)
            else:
                raise

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
    _post_slack_summary(month_start, cal, decision_id, report_path, page_url, window_label=window_label)

    print("[calendar] Done.")
    return cal


def run_monthly(month_start: date | None = None) -> dict:
    """Generate calendar for given month (defaults to next month when called 1 week before EOM).

    Legacy entry point — full-month, persisted. Kept for the cron caller.
    """
    if month_start is None:
        today = date.today()
        month_start = date(today.year, today.month, 1)
    try:
        return generate(month_start, end_date=None, revenue_goal=None, persist=True)
    except Exception as e:
        notify_failure(source="calendar/run_monthly", error=str(e))
        raise


# ── CLI ───────────────────────────────────────────────────────────────────────

def _insert_pending_approvals_for_range(start_date: date, end_date: date) -> list[date]:
    """Insert a pending calendar_approvals row for every week_start spanned
    by [start_date, end_date]. ON CONFLICT DO NOTHING — never unapproves an
    existing row. Returns the list of week_starts touched."""
    import uuid as _uuid
    weeks: set[date] = set()
    d = start_date
    while d <= end_date:
        weeks.add(d - timedelta(days=d.weekday()))  # Monday of d's week
        d += timedelta(days=1)
    with get_conn() as conn:
        with conn.cursor() as cur:
            for week_start in sorted(weeks):
                cur.execute(
                    "INSERT INTO calendar_approvals (week_start, token) "
                    "VALUES (%s, %s) ON CONFLICT (week_start) DO NOTHING",
                    (week_start, _uuid.uuid4().hex[:16]),
                )
        conn.commit()
    return sorted(weeks)


def _cli() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="pacing.calendar",
        description="Generate a content calendar via Opus + Strategy Snapshot.",
    )
    parser.add_argument("--start", type=date.fromisoformat, default=date.today(),
                        help="ISO date — first day of the planning window (default: today)")
    parser.add_argument("--end", type=date.fromisoformat, default=None,
                        help="ISO date — last day of the planning window (default: end of start's month)")
    parser.add_argument("--revenue-goal", type=int, default=None,
                        help="Monthly revenue goal in dollars (default: 150000)")
    parser.add_argument("--save-plan", action="store_true",
                        help="Persist to decisions + insert pending calendar_approvals + publish + Slack (default: stdout only)")
    args = parser.parse_args()

    if args.end is None:
        if args.start.month == 12:
            args.end = date(args.start.year + 1, 1, 1) - timedelta(days=1)
        else:
            args.end = date(args.start.year, args.start.month + 1, 1) - timedelta(days=1)
    if args.start > args.end:
        parser.error(f"--start ({args.start}) must be <= --end ({args.end})")
    if (args.end - args.start).days > 60:
        parser.error(f"window {(args.end - args.start).days} days exceeds 60-day sanity cap")
    if args.revenue_goal is not None and args.revenue_goal <= 0:
        parser.error("--revenue-goal must be > 0")

    cal = generate(args.start, args.end, revenue_goal=args.revenue_goal,
                   persist=args.save_plan)
    slots = cal.get("slots", [])

    if args.save_plan:
        weeks = _insert_pending_approvals_for_range(args.start, args.end)
        print(f"\n✅ Plan saved. {len(slots)} slots covering {args.start} → {args.end}.")
        print(f"   Pending calendar_approvals rows for week(s) starting: "
              + ", ".join(w.isoformat() for w in weeks))
        print(f"   Approve via Slack: type `approved week` in #beezy-agents.")
    else:
        print(f"\nDry-run for {args.start} → {args.end} "
              f"(NOT persisted — re-run with --save-plan to commit).\n")

        # 1) Validation outcome
        verrors = cal.get("validation_errors") or []
        if verrors:
            print(f"  Validation outcome: FAIL — {len(verrors)} error(s)")
            for e in verrors:
                print(f"    - {_format_citation_error(e)}")
        else:
            print(f"  Validation outcome: PASS")

        # 1b) Post-process filter — slots dropped by Rules 1 + 2
        dropped = cal.get("dropped_slots") or []
        print(f"\n  DROPPED: {len(dropped)} slot(s) removed by post-process filter")
        for ds in dropped:
            print(f"    - {ds.get('date')} {ds.get('content_type')} → "
                  f"{ds.get('audience')} (${float(ds.get('projected_revenue') or 0):,.0f}) "
                  f"[{ds.get('drop_reason')}]")
        # 1c) Auto-injected slots (Rule 4 cadence guard)
        injected = cal.get("injected_slots") or []
        print(f"  INJECTED: {len(injected)} slot(s) auto-added by filter")
        for inj in injected:
            print(f"    + {inj.get('date')} {inj.get('content_type')} → "
                  f"{inj.get('audience')} (${float(inj.get('revenue_estimate') or 0):,.0f}) "
                  f"[Rule 4 cadence guard]")
        print(f"  KEPT: {len(slots)} slot(s) after filter")

        # 2) Slot count + per-day distribution
        per_day: dict[str, int] = {}
        for s in slots:
            d = s.get("date") or "(no-date)"
            per_day[d] = per_day.get(d, 0) + 1
        print(f"\n  Slot count: {len(slots)} total across {len(per_day)} day(s)")
        for d in sorted(per_day):
            print(f"    {d}: {per_day[d]}")

        # 3) Full slot table — no truncation on row count
        print(f"\n  Slot table:")
        print(f"  {'date':<11} {'content_type':<17} {'audience':<20} {'format':<28} {'$proj':>8}  rationale")
        print(f"  {'-'*11} {'-'*17} {'-'*20} {'-'*28} {'-'*8}  {'-'*80}")
        for s in sorted(slots, key=lambda x: (x.get('date') or '', x.get('send_time_est') or '00:00')):
            rev = float(s.get('revenue_estimate', 0) or 0)
            fmt = (s.get('format') or s.get('topic_angle') or '')[:28]
            rat = (s.get('rationale') or '')[:80]
            print(f"  {(s.get('date') or '?'):<11} {(s.get('content_type') or '?'):<17} "
                  f"{(s.get('audience') or '?'):<20} {fmt:<28} ${rev:>7,.0f}  {rat}")

        proj = cal.get('calendar_projected_revenue', 0)
        goal = cal.get('monthly_goal', 0)
        gap  = cal.get('gap_to_goal', 0)
        print(f"\n  Projected from these slots: ${proj:,.0f}")
        print(f"  Monthly goal:               ${goal:,.0f}")
        print(f"  Gap to goal:                ${gap:,.0f}")


if __name__ == "__main__":
    _cli()
