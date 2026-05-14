"""
Beezy Slack Agent — watches #beezy-agents autonomously.

Polls every 2 minutes via Slack Web API. Interprets Boris's messages
through Claude Sonnet. Routes to action handlers. No human needed.

Commands Boris can type in #beezy-agents:
  "approved"           → approve current month's calendar
  "approved week"      → approve the current 7-day plan
  "deploy campaigns"   → trigger today's orchestrator
  "what's revenue"     → pull today's pacing from Neon
  "generate calendar"  → regenerate this month's calendar
  "run weekly brief"   → post next 7 days to Slack now
  "deploy latest episode" → trigger episode deployer pipeline
  Any other message    → Claude interprets and acts or explains
 BEEZY_AGENTS_CHANNEL = "C0B3DEUJS9G"
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
BEEZY_AGENTS_CHANNEL = "C0B3DEUJS9G"   # #beezy-agents — update if different
NEW_EPISODES_CHANNEL = "C0B3S0CM2JV"   # #beezy-new-episodes — update with correct ID
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


def _get_last_read_ts(conn, channel: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT value FROM agent_state WHERE key = %s",
            ("slack_last_read_" + channel,)
        )
        row = cur.fetchone()
    if row:
        return row[0]
    return str(time.time() - 300)  # default: last 5 min


def _save_last_read_ts(conn, channel: str, ts: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_state (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            ("slack_last_read_" + channel, ts)
        )
    conn.commit()


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


HANDLERS = {
    "approve_calendar": lambda conn, _: _handle_approve_calendar(conn),
    "approve_week":     lambda conn, _: _handle_approve_week(conn),
    "deploy_today":     lambda conn, _: (_handle_deploy_today(), None)[0],
    "revenue_query":    lambda conn, _: _handle_revenue_query(conn),
    "generate_calendar": lambda conn, _: _handle_generate_calendar(),
    "run_weekly_brief": lambda conn, _: _handle_weekly_brief(),
    "deploy_episode":   lambda conn, _: _handle_deploy_episode(conn),
    "status":           lambda conn, _: _handle_status(conn),
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

        print(f"[slack_agent] Message from {user}: {text[:80]}")

        try:
            result = _interpret_message(text)
            action   = result.get("action", "none")
            response = result.get("response", "")
            params   = result.get("params", {})

            handler = HANDLERS.get(action)
            if handler:
                try:
                    action_response = handler(conn, params)
                    reply = (action_response or "") + ("\n" + response if response else "")
                except Exception as e:
                    reply = "Error running " + action + ": " + str(e)
            else:
                reply = response or "Got it."

            _post_message(BEEZY_AGENTS_CHANNEL, reply)
        except Exception as e:
            print(f"[slack_agent] Error processing message: {e}")

        _save_last_read_ts(conn, "beezy_agents", ts)


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
                    print(f"[slack_agent] Episode ready: {metadata.get('title')}")
                    from agents.klaviyo_deployer import deploy_episode
                    result = deploy_episode(metadata, conn)
                    _post_message(
                        NEW_EPISODES_CHANNEL,
                        "Deployed: " + metadata.get("title", "?") + " — " + result
                    )
            except Exception as ex:
                print(f"[slack_agent] Episode deploy error: {ex}")
                _post_message(NEW_EPISODES_CHANNEL, "Deploy failed: " + str(ex))

        _save_last_read_ts(conn, "new_episodes", ts)


def _get_bot_id() -> str:
    resp = httpx.get(SLACK_API + "/auth.test",
                     headers=_slack_headers(), timeout=10)
    return resp.json().get("user_id", "")


# ── Main entry ────────────────────────────────────────────────────────────────

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
    except Exception as e:
        print(f"[slack_agent] Error: {e}")
        from lib.slack import notify_failure
        notify_failure(source="slack_agent", error=str(e))


if __name__ == "__main__":
    run_once()
