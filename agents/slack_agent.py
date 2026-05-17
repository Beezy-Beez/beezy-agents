"""
Beezy Slack Agent — watches #beezy-agents autonomously.

Polls every 2 minutes via Slack Web API. Interprets Boris's messages
through Claude Sonnet. Routes to action handlers. No human needed.

Commands Boris can type in #beezy-agents:
  "approved"           → approve current month's calendar
  "approved week"      → approve the current 7-day plan
  "approved issue N"   → approve Hive Mind issue N and write slot to calendar
  "deploy campaigns"   → trigger today's orchestrator
  "what's revenue"     → pull today's pacing from Neon
  "generate calendar"  → regenerate this month's calendar
  "run weekly brief"   → post next 7 days to Slack now
  "deploy latest episode" → trigger episode deployer pipeline
  Any other message    → Claude interprets and acts or explains

Also watches #beezy-new-episodes for ready episodes and deploys them
to Klaviyo automatically using the correct REST endpoints.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timezone

import anthropic
import httpx

SLACK_BOT_TOKEN  = os.environ.get("SLACK_BOT_TOKEN", "")
# LOCKED CHANNEL IDs — confirmed May 2026 — DO NOT CHANGE
# #beezy-agents:       C0B3DEUJS9G  (Boris command channel)
# #beezy-new-episodes: C0B3S0CM2JV  (episode auto-deploy)
BEEZY_AGENTS_CHANNEL = "C0B3DEUJS9G"
NEW_EPISODES_CHANNEL  = "C0B3S0CM2JV"

def _assert_channel_ids() -> None:
    """Runtime guard — corrects channel IDs if any installer wrote wrong values."""
    global BEEZY_AGENTS_CHANNEL, NEW_EPISODES_CHANNEL
    BEEZY_AGENTS_CHANNEL = "C0B3DEUJS9G"
    NEW_EPISODES_CHANNEL  = "C0B3S0CM2JV"

_assert_channel_ids()
SLACK_API = "https://slack.com/api"
MODEL = "claude-sonnet-4-6"


# ── Slack Web API helpers ─────────────────────────────────────────────────────

def _slack_headers() -> dict:
    return {"Authorization": "Bearer " + SLACK_BOT_TOKEN,
            "Content-Type": "application/json"}


def _post_message(channel: str, text: str, blocks: list | None = None) -> None:
    payload: dict = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    httpx.post(SLACK_API + "/chat.postMessage",
               headers=_slack_headers(), json=payload, timeout=15)


def _get_messages_since(channel: str, oldest_ts: str) -> list[dict]:
    resp = httpx.get(
        SLACK_API + "/conversations.history",
        headers=_slack_headers(),
        params={"channel": channel, "oldest": oldest_ts, "limit": 20},
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        print(f"[slack_agent] Slack API error: {data.get('error')}")
        return []
    return data.get("messages", [])


_LAST_READ: dict[str, str] = {}


def _get_last_read_ts(conn, channel: str) -> str:
    return _LAST_READ.get(channel, str(time.time()))  # default: last 5 min


def _save_last_read_ts(conn, channel: str, ts: str) -> None:
    _LAST_READ[channel] = ts


# ── Command interpreter ───────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the Beezy Beez email marketing system agent.
Boris types commands in Slack and you interpret them and take action.

When Boris sends a message, respond with a JSON object:
{
  "action": "one of the actions below",
  "params": {},
  "response": "what to tell Boris in Slack"
}

Actions you can take:
- "approve_calendar" — mark this month's calendar as approved
- "approve_week" — mark the current 7-day plan as approved
- "deploy_today" — run today's orchestrator dispatch
- "revenue_query" — pull today's pacing data
- "generate_calendar" — regenerate the monthly calendar
- "run_weekly_brief" — post next 7 days to Slack
- "deploy_episode" — trigger the episode deployer for latest in #beezy-new-episodes
- "pause_slot" — params: {content_type, date} — skip a slot
- "status" — report what's running today
- "clarify" — ask Boris for more information
- "modify_calendar" — params: {"request": "natural language description of change"}
  Use when Boris wants to add/remove/change slots in the calendar. E.g. "add more SMS",
  "remove flow experiments", "change VIP campaign to Friday", "max 2 emails per day"
- "query_calendar" — params: {"question": "what Boris wants to know about the calendar"}
  Use when Boris asks about the calendar: "what's planned for Thursday?",
  "how many SMS do we have?", "show me next week"
- "help" — post the full list of available commands
- "none" — no action needed, just respond

Always be concise. Boris is busy. No fluff."""


def _interpret_message(text: str) -> dict:
    key = os.environ.get("BEEZY_ANTHROPIC_API_KEY")
    if not key:
        return {"action": "none", "response": "BEEZY_ANTHROPIC_API_KEY not set."}
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=MODEL, max_tokens=512, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    raw = msg.content[0].text.strip()
    s, e = raw.find("{"), raw.rfind("}")
    if s != -1 and e != -1:
        return json.loads(raw[s:e+1])
    return {"action": "none", "response": raw}


# ── Action handlers ───────────────────────────────────────────────────────────

def _handle_approve_calendar(conn) -> str:
    month = date.today().strftime("%Y-%m")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE decisions SET output = output || '{\"approved\": true}'::jsonb "
            "WHERE decision_type = 'calendar_plan' AND output->>'month' = %s",
            (month,)
        )
    conn.commit()
    return "Calendar for " + month + " approved. Weekly briefs will now be generated."


def _handle_approve_week(conn) -> str:
    today = date.today()
    week_start = today.isoformat()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO calendar_approvals (week_start, token, approved_at) "
            "VALUES (%s, 'slack_approval', NOW()) "
            "ON CONFLICT (week_start) DO UPDATE SET approved_at = NOW()",
            (week_start,)
        )
    conn.commit()
    return "Week of " + week_start + " approved. Orchestrator will deploy at 8am ET tomorrow."


def _handle_deploy_today() -> str:
    from pacing.orchestrator import run_daily
    run_daily()
    return "Today's orchestrator run complete. Check above for individual slot results."


def _handle_revenue_query(conn) -> str:
    from pacing.brain import active_goals, compute_pacing_state
    goals = active_goals()
    if not goals:
        return "No active revenue goals found."
    g = goals[0]
    state = compute_pacing_state(g.id)
    return (
        f"*{g.title}*\n"
        f"To-date: ${state.period_to_date_value:,.0f} / ${state.target_to_date_value:,.0f} target\n"
        f"Gap: {state.gap_pct:.1f}% {'behind' if state.gap_pct < 0 else 'ahead'}\n"
        f"Required daily: ${state.required_daily_rate:,.0f}/day\n"
        f"Days remaining: {state.days_remaining}"
    )


def _handle_generate_calendar() -> str:
    from pacing.calendar import run_monthly
    run_monthly()
    return "Calendar regeneration complete. Check Shopify for the updated calendar page."


def _handle_weekly_brief() -> str:
    from pacing.weekly_brief import run_weekly_brief
    run_weekly_brief()
    return "Weekly brief posted above."


def _handle_deploy_episode(conn) -> str:
    """
    Read latest unprocessed message from #beezy-new-episodes and deploy it.
    Uses Klaviyo REST (correct endpoints confirmed) + Shopify MCP pattern.
    """
    from agents.klaviyo_deployer import deploy_episode_from_slack
    result = deploy_episode_from_slack(conn)
    return result


def _handle_status(conn) -> str:
    today = date.today().isoformat()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT content_type, audience, status, executed_at "
            "FROM calendar_executions WHERE slot_date = %s ORDER BY executed_at",
            (today,)
        )
        rows = cur.fetchall()
    if not rows:
        return "No slots dispatched today (" + today + ")."
    lines = ["*Today's dispatch (" + today + "):*"]
    for r in rows:
        lines.append(f"  {r[2]} — {r[0]}/{r[1]}")
    return "\n".join(lines)


HELP_TEXT = """*Beezy Agent Commands* — type any of these in #beezy-agents:

*Calendar:*
`approved` — approve the monthly calendar
`approved week` — approve the next 7 days\n`approved today` — approve just today\n`approved may 20` — approve a specific date\n`approved issue N` — approve Hive Mind issue N and write slot to calendar
`generate calendar` — regenerate this month's calendar
`run weekly brief` — post next 7 days to Slack now
`calendar` — view this month's calendar

*Conversational edits (just describe what you want):*
"add 2 more SMS campaigns this month targeting VIPs"
"remove all flow experiments from the calendar"
"move the Wednesday campaign to Friday"
"max 2 emails per day for the rest of May"
"what's planned for next week?"
"how many SMS campaigns do we have left?"

*Operations:*
`deploy campaigns` — run today's orchestrator now
`what is revenue` — pull today's pacing data
`deploy latest episode` — deploy from #beezy-new-episodes
`status` — see today's dispatch log
`help` — show this list

*Performance:*
`run backfill` — pull revenue for campaigns sent 3+ days ago
`weekly review` — run the weekly performance review now
`pacing check` — mid-month pacing check
`monthly review` — run the monthly retrospective
`flow check` — run flow health check

*Audience management:*
`burn <audience>` — add audience to burn list (validator R5 blocks all sends)
`unburn <audience>` — remove audience from burn list
`burn list` — show current burn list"""

MODIFY_SYSTEM = """You are a calendar editor for Beezy Beez Honey email marketing.

Given a change request, return ONLY a JSON object describing what to add or remove.
Do NOT return the full calendar. Return ONLY this schema:

{
  "action": "add" | "remove" | "update",
  "description": "brief human description of what changed",
  "slots_to_add": [...],
  "slots_to_remove_by_date_and_type": [{"date": "YYYY-MM-DD", "content_type": "..."}]
}

slots_to_add must match the full slot schema:
{"date":"YYYY-MM-DD","content_type":"...","channel":"email|sms","audience":"...",
 "topic_angle":"...","send_time_est":"HH:MM","priority":"high|medium|low",
 "revenue_estimate":0,"needs_page":false,"discount_code":"","discount_pct":0,
 "rationale":"...","goal_alignment":"...","adjustment_lever":"..."}

Return ONLY valid JSON. No prose. No markdown."""


def _handle_modify_calendar(conn, params: dict) -> str:
    request = params.get("request", "")
    if not request:
        return "What change would you like to make to the calendar?"

    month = date.today().strftime("%Y-%m")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, output FROM decisions WHERE decision_type='calendar_plan' "
            "AND output->>'month'=%s ORDER BY created_at DESC LIMIT 1",
            (month,)
        )
        row = cur.fetchone()
    if not row:
        return "No calendar found for " + month + ". Generate one first."

    decision_id = str(row[0])
    calendar    = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    slots       = calendar.get("slots", [])

    import os as _os
    key = _os.environ.get("BEEZY_ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=key)

    # Pass only a summary of existing slots to avoid token overflow
    slot_summary = [
        {"date": s.get("date"), "content_type": s.get("content_type"),
         "audience": s.get("audience"), "topic_angle": s.get("topic_angle","")[:40]}
        for s in slots
    ]
    msg = client.messages.create(
        model=MODEL, max_tokens=2048, system=MODIFY_SYSTEM,
        messages=[{"role": "user", "content":
            "Existing slots summary:\n" + json.dumps(slot_summary, indent=2) +
            "\n\nChange request: " + request
        }],
    )
    raw = msg.content[0].text.strip()
    s, e = raw.find("{"), raw.rfind("}")
    if s == -1:
        return "Could not parse the edit instructions. Try rephrasing."

    diff = json.loads(raw[s:e+1])

    # Ensure all added slots have a non-zero revenue_estimate (compute before saving to DB)
    FALLBACK_RPR = {
        "active_seal": 1.268, "whales": 0.658, "lapsed_30d": 0.267, "vip": 0.161,
        "engaged_customers": 0.101, "one_time_buyers": 0.056, "engaged_prospects": 0.064,
        "super_engaged": 0.120,
    }
    FALLBACK_LIST = {
        "active_seal": 511, "whales": 1038, "lapsed_30d": 3618, "vip": 5424,
        "engaged_customers": 13340, "one_time_buyers": 12951, "engaged_prospects": 12002,
        "super_engaged": 4447,
    }
    for slot in diff.get("slots_to_add", []):
        if not slot.get("revenue_estimate"):
            aud = slot.get("audience", "")
            rpr = FALLBACK_RPR.get(aud, 0.10)
            lst = FALLBACK_LIST.get(aud, 1000)
            slot["revenue_estimate"] = round(rpr * lst, 2)

    # Apply diff to existing slots (never replace the whole calendar)
    to_remove = {
        (r.get("date"), r.get("content_type"))
        for r in diff.get("slots_to_remove_by_date_and_type", [])
    }
    new_slots = [s for s in slots if (s.get("date"), s.get("content_type")) not in to_remove]
    new_slots += diff.get("slots_to_add", [])
    new_slots.sort(key=lambda s: s.get("date",""))

    calendar["slots"] = new_slots

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE decisions SET output=%s::jsonb WHERE id=%s",
            (json.dumps(calendar), decision_id)
        )
    conn.commit()

    # Republish Shopify calendar page
    try:
        from pacing.calendar import _generate_html_report, _publish_calendar_page
        from datetime import date as _date
        month_start = _date(date.today().year, date.today().month, 1)
        html = _generate_html_report(month_start, calendar)
        page_url = _publish_calendar_page(month_start, html)
    except Exception as ex:
        page_url = "(page update failed: " + str(ex) + ")"

    added   = len(diff.get("slots_to_add", []))
    removed = len(diff.get("slots_to_remove_by_date_and_type", []))
    desc    = diff.get("description", request)
    return (
        "Calendar updated for " + month + "\n"
        + ("+" + str(added) + " slots added" if added else "")
        + (" | -" + str(removed) + " slots removed" if removed else "")
        + "\nChange: " + desc
        + "\n" + page_url
    )


def _handle_query_calendar(conn, params: dict) -> str:
    question = params.get("question", "")
    month    = date.today().strftime("%Y-%m")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT output FROM decisions WHERE decision_type='calendar_plan' "
            "AND output->>'month'=%s ORDER BY created_at DESC LIMIT 1",
            (month,)
        )
        row = cur.fetchone()
    if not row:
        return "No calendar found for " + month + "."

    calendar = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    slots    = calendar.get("slots", [])

    import os as _os
    key = _os.environ.get("BEEZY_ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=MODEL, max_tokens=512,
        system="You answer questions about an email marketing calendar concisely. Be specific. Use bullet points if listing multiple slots.",
        messages=[{"role": "user", "content":
            "Calendar slots (JSON):\n" + json.dumps(slots, indent=2)[:5000] +
            "\n\nQuestion: " + question
        }],
    )
    return msg.content[0].text.strip()


def _handle_restore_calendar(conn, params: dict) -> str:
    month = date.today().strftime("%Y-%m")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT output FROM decisions WHERE decision_type='calendar_backup' "
            "ORDER BY created_at DESC LIMIT 1"
        )
        backup = cur.fetchone()
    if not backup:
        return "No calendar backup found."
    backup_data = backup[0] if isinstance(backup[0], dict) else json.loads(backup[0])
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM decisions WHERE decision_type='calendar_plan' "
            "AND output->>'month'=%s ORDER BY created_at DESC LIMIT 1",
            (month,)
        )
        row = cur.fetchone()
    if not row:
        return "No current calendar for " + month
    with conn.cursor() as cur:
        cur.execute("UPDATE decisions SET output=%s::jsonb WHERE id=%s",
                    (json.dumps(backup_data), row[0]))
    conn.commit()
    return "Calendar restored from backup. " + str(len(backup_data.get("slots",[]))) + " slots."


def _handle_approve_day(conn, params: dict) -> str:
    """Approve a single day for campaign dispatch."""
    from datetime import date as _date, timedelta
    day_str = params.get("day", "today")

    if day_str == "today":
        target = _date.today()
    else:
        # Try parsing various formats
        try:
            target = _date.fromisoformat(day_str)
        except ValueError:
            # Try "may 15" format
            import re
            m = re.match(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+(\d{1,2})", day_str)
            if m:
                months = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
                          "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
                target = _date(_date.today().year, months[m.group(1)[:3]], int(m.group(2)))
            else:
                return "Could not parse date: " + day_str + ". Use YYYY-MM-DD or 'may 15' format."

    # Insert approval for just that day (use day as both start and end of a 1-day window)
    week_start = target  # Using the target day as the "week_start" for a 1-day approval
    import uuid
    token = str(uuid.uuid4())[:8]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO calendar_approvals (week_start, token, approved_at, approved_by) "
            "VALUES (%s, %s, NOW(), %s) "
            "ON CONFLICT (week_start) DO UPDATE SET approved_at = NOW(), approved_by = EXCLUDED.approved_by",
            (target, token, "boris_slack")
        )
    conn.commit()

    # Count slots for that day
    month = target.strftime("%Y-%m")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT output FROM decisions WHERE decision_type='calendar_plan' "
            "AND output->>'month'=%s ORDER BY created_at DESC LIMIT 1",
            (month,)
        )
        row = cur.fetchone()

    slot_count = 0
    if row:
        cal = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        day_slots = [s for s in cal.get("slots", []) if s.get("date") == str(target)]
        slot_count = len(day_slots)
        slot_summary = ", ".join(
            s.get("audience","") + " (" + s.get("content_type","") + ")"
            for s in day_slots
        )
    else:
        slot_summary = "no calendar found"

    return (
        "Approved " + str(target) + " (" + target.strftime("%A") + ")\n"
        + str(slot_count) + " slots: " + slot_summary + "\n"
        + "Type `deploy campaigns` to create these as Klaviyo drafts now, "
        + "or they\'ll auto-deploy at 8am ET."
    )


def _handle_view_calendar(conn, params: dict) -> str:
    """Return the current calendar page URL."""
    month = date.today().strftime("%Y-%m")
    url = f"https://trybeezybeez.myshopify.com/pages/calendar-{month}"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT output->>'total_slots', output->>'campaign_revenue_mtd', "
            "output->>'flow_revenue_mtd', output->>'total_projected', output->>'gap_to_goal' "
            "FROM decisions WHERE decision_type='calendar_plan' "
            "AND output->>'month'=%s ORDER BY created_at DESC LIMIT 1",
            (month,)
        )
        row = cur.fetchone()
    if row:
        return (
            f"Calendar {month}\n"
            f"{row[0]} slots | Campaigns MTD: ${float(row[1] or 0):,.2f} | Flows MTD: ${float(row[2] or 0):,.2f}\n"
            f"Total projected: ${float(row[3] or 0):,.2f} | Gap: ${float(row[4] or 0):,.2f}\n"
            f"{url}"
        )
    return f"Calendar: {url}"


def _handle_cancel_campaign(conn, params: dict) -> str:
    campaign_id = params.get("campaign_id", "").strip()
    if not campaign_id:
        return "Please provide a campaign ID: `cancel ABC123`"
    # Mark as cancelled in pending_schedules
    try:
        row = conn.execute("SELECT value FROM agent_state WHERE key='pending_schedules'").fetchone()
        if row:
            pending = json.loads(row[0]) if row[0] else []
            found = False
            for entry in pending:
                if entry.get("campaign_id", "").lower() == campaign_id.lower():
                    entry["cancelled"] = True
                    found = True
            if found:
                conn.execute(
                    "INSERT INTO agent_state (key, value, updated_at) VALUES ('pending_schedules', %s, NOW()) "
                    "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                    (json.dumps(pending),)
                )
                conn.commit()
                return f"✅ Campaign `{campaign_id}` cancelled. It will not be scheduled."
    except Exception as e:
        return f"❌ Error cancelling: {e}"
    return f"⚠️ Campaign `{campaign_id}` not found in pending queue. May already be scheduled or sent."


def _handle_approve_issue(conn, params: dict) -> str:
    """Approve a specific Hive Mind issue and write a calendar_executions slot.

    Computes the send date from Issue 014 baseline (May 15) + 3 days per issue.
    """
    import uuid
    from datetime import date as _date, timedelta

    issue_num = int(params.get("issue", 0))
    if not issue_num:
        return "❌ No issue number provided."

    # Look up issue in DB
    row = conn.execute(
        "SELECT subject_line, topic_summary, status FROM issues WHERE number = %s",
        (issue_num,),
    ).fetchone()
    if not row:
        return f"❌ Issue {issue_num} not found in DB. Run the copywriter first."

    subject, topic, status = row
    if status == "published":
        return f"⚠️ Issue {issue_num} is already published — nothing to approve."

    # Compute send date: Issue 014 = 2026-05-15, every 3 days
    BASE_ISSUE = 14
    BASE_DATE  = _date(2026, 5, 15)
    delta      = (issue_num - BASE_ISSUE) * 3
    send_date  = BASE_DATE + timedelta(days=delta)

    # Check if a slot already exists for this issue
    existing = conn.execute(
        "SELECT id FROM calendar_executions WHERE slot_date = %s AND content_type = 'hive_mind'",
        (send_date,),
    ).fetchone()
    if existing:
        return (
            f"⚠️ A hive_mind slot already exists for {send_date}. "
            "Nothing written."
        )

    conn.execute(
        """INSERT INTO calendar_executions
               (id, slot_date, content_type, audience, topic_angle, status, notes, executed_at)
           VALUES (%s, %s, 'hive_mind', 'hive_mind_prospects', %s, 'pending', %s, NOW())""",
        (
            uuid.uuid4(),
            send_date,
            topic or subject,
            f"Issue {issue_num} — approved via Slack",
        ),
    )
    conn.commit()

    return (
        f"✅ *Issue {issue_num} approved.*\n"
        f"Slot written to calendar: *{send_date}* ({send_date.strftime('%A')})\n"
        f"Subject: _{subject}_\n"
        f"The 10am auto-create cron will build the Klaviyo campaign once the Shopify page is published."
    )


HANDLERS = {
    "approve_calendar":  lambda conn, _: _handle_approve_calendar(conn),
    "approve_week":      lambda conn, _: _handle_approve_week(conn),
    "approve_day":       _handle_approve_day,
    "approve_issue":     _handle_approve_issue,
    "deploy_today":      lambda conn, _: (_handle_deploy_today(), None)[0],
    "revenue_query":     lambda conn, _: _handle_revenue_query(conn),
    "generate_calendar": lambda conn, _: _handle_generate_calendar(),
    "restore_calendar":  _handle_restore_calendar,
    "run_weekly_brief":  lambda conn, _: _handle_weekly_brief(),
    "deploy_episode":    lambda conn, _: _handle_deploy_episode(conn),
    "status":            lambda conn, _: _handle_status(conn),
    "help":              lambda conn, _: HELP_TEXT,
    "flow_check":        lambda conn, _: _handle_flow_check(),
    "run_backfill":      lambda conn, _: _handle_backfill(),
    "weekly_review":     lambda conn, _: _handle_weekly_review(),
    "pacing_check":      lambda conn, _: _handle_pacing_check(),
    "monthly_review":    lambda conn, _: _handle_monthly_review(),
    "burn_list":         lambda conn, _: _handle_burn_list(conn),
    "burn_audience":     lambda conn, p: _handle_burn(conn, p.get("audience", "")),
    "unburn_audience":   lambda conn, p: _handle_unburn(conn, p.get("audience", "")),
    "view_calendar":     _handle_view_calendar,
    "modify_calendar":   _handle_modify_calendar,
    "query_calendar":    _handle_query_calendar,
    "cancel_campaign":   _handle_cancel_campaign,
}


# ── Channel processors ────────────────────────────────────────────────────────

def _process_beezy_agents(conn) -> None:
    """Process new messages from Boris in #beezy-agents."""
    last_ts   = _get_last_read_ts(conn, "beezy_agents")
    messages  = _get_messages_since(BEEZY_AGENTS_CHANNEL, last_ts)
    bot_id    = _get_bot_id()

    for msg in reversed(messages):
        ts   = msg.get("ts", "")
        text = msg.get("text", "").strip()
        user = msg.get("user", "")
        subtype = msg.get("subtype", "")

        # Skip bot's own messages and system messages
        if subtype or user == bot_id or not text:
            continue

        # Save timestamp IMMEDIATELY — prevents re-processing on next poll tick
        _save_last_read_ts(conn, "beezy_agents", ts)

        print(f"[slack_agent] Message from {user}: {text[:80]}")

        try:
            text_lower = text.strip().lower()
            fast_match = {
                "help": {"action": "help"},
                "calendar": {"action": "view_calendar"},
                "status": {"action": "status"},
                "approved": {"action": "approve_calendar"},
                "approved week": {"action": "approve_week"},
                "approved today": {"action": "approve_day", "params": {"day": "today"}},
                "approve today": {"action": "approve_day", "params": {"day": "today"}},
                "deploy campaigns": {"action": "deploy_today"},
                "generate calendar": {"action": "generate_calendar"},
                "run weekly brief": {"action": "run_weekly_brief"},
                "deploy latest episode": {"action": "deploy_episode"},
                "restore calendar": {"action": "restore_calendar"},
                "run backfill": {"action": "run_backfill"},
                "weekly review": {"action": "weekly_review"},
                "pacing check": {"action": "pacing_check"},
                "monthly review": {"action": "monthly_review"},
                "flow check": {"action": "flow_check"},
                "flow health": {"action": "flow_check"},
                "burn list":   {"action": "burn_list"},
            }
            # Cancel campaign — "cancel ABC123"
            import re as _re
            cancel_match       = _re.match(r'^cancel\s+([A-Za-z0-9]+)$', text_lower)
            burn_match         = _re.match(r'^burn\s+([a-z0-9_\- ]+)$', text_lower)
            unburn_match       = _re.match(r'^unburn\s+([a-z0-9_\- ]+)$', text_lower)
            approve_issue_match = _re.match(r'^approved?\s+(?:hive[\s_]?mind\s+)?issue\s+(\d+)$', text_lower)
            if approve_issue_match:
                result = {"action": "approve_issue", "params": {"issue": int(approve_issue_match.group(1))}}
            elif cancel_match:
                result = {"action": "cancel_campaign", "params": {"campaign_id": cancel_match.group(1)}}
            elif burn_match:
                result = {"action": "burn_audience", "params": {"audience": burn_match.group(1).strip()}}
            elif unburn_match:
                result = {"action": "unburn_audience", "params": {"audience": unburn_match.group(1).strip()}}
            else:
                result = fast_match.get(text_lower)
            if result:
                _post_message(BEEZY_AGENTS_CHANNEL, "⏳ On it...")
            else:
                _post_message(BEEZY_AGENTS_CHANNEL, "⏳ Got it — working on it...")
                if "revenue" in text_lower or "money" in text_lower or "sales" in text_lower:
                    result = {"action": "revenue_query"}
                elif any(w in text_lower for w in ["add ", "remove ", "move ", "change ", "max ", "reduce "]):
                    result = {"action": "modify_calendar", "params": {"request": text}}
                elif "?" in text or any(w in text_lower for w in ["what", "how many", "show me", "planned"]):
                    result = {"action": "query_calendar", "params": {"question": text}}
                else:
                    result = _interpret_message(text)
            action   = result.get("action", "none")
            response = result.get("response", "")
            params   = result.get("params", {})

            handler = HANDLERS.get(action)
            if handler:
                try:
                    action_response = handler(conn, params)
                    reply = action_response or "Done."
                except Exception as e:
                    reply = "❌ Error: " + str(e)
            else:
                reply = response or "Got it."

            _post_message(BEEZY_AGENTS_CHANNEL, reply)
        except Exception as e:
            print(f"[slack_agent] Error processing message: {e}")
            _post_message(BEEZY_AGENTS_CHANNEL, "❌ Something went wrong: " + str(e)[:200])



def _post_episode_complete_summary(metadata: dict, result: str) -> None:
    """Post a rich completion summary to #beezy-agents when an episode is deployed."""
    title    = metadata.get("title", "?")
    ep_type  = metadata.get("episode_type", "sleep_story").replace("_", " ").title()
    page_url = metadata.get("shopify_page_url") or metadata.get("buzzsprout_url", "?")
    # Parse campaign URLs out of result string ("Email A: <url>\nEmail B: <url>")
    lines    = result.splitlines()
    camp_a   = next((l.split("Email A: ", 1)[1].strip() for l in lines if "Email A:" in l), "")
    camp_b   = next((l.split("Email B: ", 1)[1].strip() for l in lines if "Email B:" in l), "")
    summary  = [
        f"*Title:* {title}",
        f"*Type:* {ep_type}",
        f"*Page:* {page_url}",
    ]
    if camp_a:
        summary.append(f"*Email A (Engaged Customers):* {camp_a}")
    if camp_b:
        summary.append(f"*Email B (Active Seal):* {camp_b}")
    summary.append("Both campaigns are DRAFT — schedule Email A for 8pm ET, Email B for 8:15pm ET.")
    _post_message(
        BEEZY_AGENTS_CHANNEL,
        "✅ *Episode deployed: " + title + "*\n" +
        "\n".join("• " + l for l in summary),
    )


def _clear_pending_tts_run(conn, episode_id: str) -> None:
    """Remove a completed episode from the 30-min TTS watchdog list."""
    if not episode_id:
        return
    try:
        row = conn.execute(
            "SELECT value FROM agent_state WHERE key='pending_tts_runs'"
        ).fetchone()
        if not row:
            return
        runs = json.loads(row[0])
        updated = [r for r in runs if r.get("episode_id") != episode_id]
        if len(updated) != len(runs):
            conn.execute(
                "INSERT INTO agent_state (key, value, updated_at) VALUES ('pending_tts_runs', %s, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                (json.dumps(updated),),
            )
            conn.commit()
    except Exception as exc:
        print(f"[slack_agent] Failed to clear pending TTS run (non-fatal): {exc}")


def _process_new_episodes(conn) -> None:
    """Check #beezy-new-episodes for unprocessed episode metadata."""
    last_ts  = _get_last_read_ts(conn, "new_episodes")
    messages = _get_messages_since(NEW_EPISODES_CHANNEL, last_ts)

    for msg in reversed(messages):
        ts   = msg.get("ts", "")
        text = msg.get("text", "")

        # Look for JSON episode metadata
        if '"episode_id"' in text and '"buzzsprout_url"' in text:
            try:
                s, e = text.find("{"), text.rfind("}")
                if s != -1:
                    metadata = json.loads(text[s:e+1])
                    title = metadata.get("title", "?")
                    print(f"[slack_agent] Episode ready: {title}")
                    from agents.klaviyo_deployer import deploy_episode
                    result = deploy_episode(metadata, conn)
                    # Plain ack in #beezy-new-episodes
                    _post_message(NEW_EPISODES_CHANNEL, f"Deployed: {title} — {result}")
                    # Rich summary in #beezy-agents (Boris's command channel)
                    _post_episode_complete_summary(metadata, result)
                    # Clear from 30-min watchdog
                    _clear_pending_tts_run(conn, metadata.get("episode_id", ""))
            except Exception as ex:
                print(f"[slack_agent] Episode deploy error: {ex}")
                _post_message(NEW_EPISODES_CHANNEL, "Deploy failed: " + str(ex))
                _post_message(BEEZY_AGENTS_CHANNEL, f"❌ Episode deploy failed: {ex}")

        _save_last_read_ts(conn, "new_episodes", ts)


_bot_id_cache: str = ""

def _get_bot_id() -> str:
    global _bot_id_cache
    if _bot_id_cache:
        return _bot_id_cache
    resp = httpx.get(SLACK_API + "/auth.test",
                     headers=_slack_headers(), timeout=10)
    _bot_id_cache = resp.json().get("user_id", "")
    return _bot_id_cache


# ── Main entry ────────────────────────────────────────────────────────────────



def _handle_flow_check():
    from workers.flow_monitor import run_flow_check
    result = run_flow_check()
    return "Flow health check posted above."

def _handle_backfill():
    from workers.revenue_backfill import run_backfill
    result = run_backfill()
    return "Revenue backfill complete: " + str(result)

def _handle_weekly_review():
    from workers.learning_loop import run_weekly
    result = run_weekly()
    return "Weekly review posted above."

def _handle_pacing_check():
    from workers.learning_loop import run_biweekly
    result = run_biweekly()
    return "Pacing check posted above."

def _handle_monthly_review():
    from workers.learning_loop import run_monthly
    result = run_monthly()
    return "Monthly review posted above."


def _handle_burn(conn, audience: str) -> str:
    """Add audience to the burn list in agent_state['burned_audiences']."""
    import json as _json
    audience = audience.strip().lower().replace(" ", "_").replace("-", "_")
    if not audience:
        return "Usage: `burn <audience>` — e.g. `burn lapsed_90d`"
    row = conn.execute(
        "SELECT value FROM agent_state WHERE key = 'burned_audiences' LIMIT 1"
    ).fetchone()
    data = _json.loads(row[0]) if row else {"audiences": []}
    burned: list[str] = data.get("audiences") or []
    if audience in burned:
        return f"'{audience}' is already on the burn list."
    burned.append(audience)
    data["audiences"] = burned
    data["updated_at"] = __import__("datetime").date.today().isoformat()
    conn.execute(
        "INSERT INTO agent_state (key, value, updated_at) VALUES ('burned_audiences', %s, NOW()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
        (_json.dumps(data),)
    )
    conn.commit()
    return f"🔥 *{audience}* added to burn list — validator R5 will block all sends to this audience."


def _handle_unburn(conn, audience: str) -> str:
    """Remove audience from the burn list."""
    import json as _json
    audience = audience.strip().lower().replace(" ", "_").replace("-", "_")
    if not audience:
        return "Usage: `unburn <audience>` — e.g. `unburn lapsed_90d`"
    row = conn.execute(
        "SELECT value FROM agent_state WHERE key = 'burned_audiences' LIMIT 1"
    ).fetchone()
    if not row:
        return f"No burn list found — '{audience}' was never burned."
    data = _json.loads(row[0])
    burned: list[str] = data.get("audiences") or []
    if audience not in burned:
        return f"'{audience}' is not on the burn list."
    burned.remove(audience)
    data["audiences"] = burned
    data["updated_at"] = __import__("datetime").date.today().isoformat()
    conn.execute(
        "INSERT INTO agent_state (key, value, updated_at) VALUES ('burned_audiences', %s, NOW()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
        (_json.dumps(data),)
    )
    conn.commit()
    return f"✅ *{audience}* removed from burn list — sends are unblocked."


def _handle_burn_list(conn) -> str:
    """Show the current burn list."""
    import json as _json
    row = conn.execute(
        "SELECT value FROM agent_state WHERE key = 'burned_audiences' LIMIT 1"
    ).fetchone()
    if not row:
        return "No burn list defined — all audiences clear."
    data = _json.loads(row[0]) if isinstance(row[0], str) else row[0]
    burned = data.get("audiences") or []
    updated = data.get("updated_at", "unknown")
    if not burned:
        return "Burn list is empty — all audiences clear."
    return "🔥 *Burned audiences* (R5 will block sends):\n" + "\n".join(f"  • {a}" for a in burned) + f"\n_Last updated: {updated}_"


def run_once() -> None:
    """Called every 2 minutes by cron_dispatch.py."""
    if not SLACK_BOT_TOKEN:
        print("[slack_agent] SLACK_BOT_TOKEN not set — skipping.")
        return

    from db.connection import get_conn
    try:
        with get_conn() as conn:
            _process_beezy_agents(conn)
            _process_new_episodes(conn)
    except httpx.NetworkError:
        raise  # let _slack_loop handle logging + backoff
    except Exception as e:
        print(f"[slack_agent] Error: {e}")


if __name__ == "__main__":
    run_once()
