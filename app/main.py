"""
Beezy Agents — unified web server.
Slack agent runs every 5 seconds.
All cron jobs run on time-based schedule in background.
Single deployment handles everything.
"""
import sys
import os
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.dashboard import router as dashboard_router

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NY = ZoneInfo("America/New_York")
_last_cron_minute = -1


async def _slack_loop():
    """Polls Slack every 5 seconds; backs off to 30s on network errors."""
    loop = asyncio.get_event_loop()
    while True:
        sleep_secs = 5
        try:
            from agents.slack_agent import run_once
            await loop.run_in_executor(None, run_once)
        except Exception as e:
            import httpx as _httpx
            if isinstance(e, _httpx.NetworkError):
                sleep_secs = 30
            print(f"[slack_loop] {e}")
        await asyncio.sleep(sleep_secs)


def _run_cron_jobs(now: datetime) -> None:
    """Synchronous cron dispatch — runs in a thread executor."""
    h, m = now.hour, now.minute

    if h % 4 == 0 and m < 2:
        try:
            from ingestion.sync import run_shopify_sync, run_klaviyo_sync
            print("[cron] ingestion sync")
            run_shopify_sync()
            run_klaviyo_sync()
        except Exception as e:
            print(f"[cron] ingestion error: {e}")

    if h == 7 and m == 30:
        try:
            from pacing.cron import run_daily as pacing_daily
            print("[cron] pacing brain")
            pacing_daily()
        except Exception as e:
            print(f"[cron] pacing error: {e}")

    if h == 8 and m == 0:
        try:
            from pacing.orchestrator import run_daily
            print("[cron] orchestrator")
            run_daily()
        except Exception as e:
            print(f"[cron] orchestrator error: {e}")

    # Audience health monitor — daily at 7:40am
    if h == 7 and m == 40:
        try:
            from workers.audience_health import run_audience_health
            print("[cron] audience health")
            run_audience_health()
        except Exception as e:
            print(f"[cron] audience health error: {e}")

    # Pacing cache refresh — daily at 7:35am (after pacing brain)
    if h == 7 and m == 35:
        try:
            from workers.pacing_cache import refresh_pacing_cache
            print('[cron] pacing cache refresh')
            refresh_pacing_cache()
        except Exception as e:
            print(f'[cron] pacing cache error: {e}')

    # Revenue backfill — daily at 9am, pulls 72h-old campaign performance
    if h == 9 and m == 0:
        try:
            from workers.revenue_backfill import run_backfill
            print("[cron] revenue backfill")
            run_backfill()
        except Exception as e:
            print(f"[cron] backfill error: {e}")

    # Weekly learning review — Sunday at 9pm ET
    if now.weekday() == 6 and h == 21 and m == 0:
        try:
            from workers.learning_loop import run_weekly
            print("[cron] weekly learning review")
            run_weekly()
        except Exception as e:
            print(f"[cron] weekly review error: {e}")

    # Weekly flow health check — Sunday at 9:15pm ET (after campaign review)
    if now.weekday() == 6 and h == 21 and m == 15:
        try:
            from workers.flow_monitor import run_flow_check
            print("[cron] flow health check")
            run_flow_check()
        except Exception as e:
            print(f"[cron] flow monitor error: {e}")

    # Bi-weekly pacing check — 15th of month at 9am
    if now.day == 15 and h == 9 and m == 30:
        try:
            from workers.learning_loop import run_biweekly
            print("[cron] bi-weekly pacing check")
            run_biweekly()
        except Exception as e:
            print(f"[cron] biweekly error: {e}")

    # Monthly retrospective — 1st of month at 9am
    if now.day == 1 and h == 9 and m == 30:
        try:
            from workers.learning_loop import run_monthly
            print("[cron] monthly retrospective")
            run_monthly()
        except Exception as e:
            print(f"[cron] monthly retro error: {e}")

    # Pending campaign auto-schedule — every 5 minutes
    if m % 5 == 0:
        try:
            from workers.beezy_campaign import check_pending_schedules
            check_pending_schedules()
        except Exception as e:
            print(f"[cron] pending_schedules error: {e}")

    # Morning briefing — 8:05am daily (after orchestrator)
    if h == 8 and m == 5:
        try:
            from workers.morning_brief import run_morning_brief
            print("[cron] morning brief")
            run_morning_brief()
        except Exception as e:
            print(f"[cron] morning brief error: {e}")

    if h == 10 and m == 0:
        try:
            from workers.klaviyo_campaign import auto_create_pending
            print("[cron] hive mind campaign")
            auto_create_pending()
        except Exception as e:
            print(f"[cron] hive mind error: {e}")

    if now.weekday() == 6 and h == 21 and m == 0:
        try:
            from pacing.weekly_brief import run_weekly_brief
            print("[cron] weekly brief")
            run_weekly_brief()
        except Exception as e:
            print(f"[cron] weekly brief error: {e}")

    import calendar as _cal
    last_day = _cal.monthrange(now.year, now.month)[1]
    if now.day == last_day - 7 and h == 9 and m == 0:
        try:
            from pacing.calendar import run_monthly
            from datetime import timedelta
            next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
            print("[cron] calendar generation")
            run_monthly(month_start=next_month.date())
        except Exception as e:
            print(f"[cron] calendar error: {e}")


async def _cron_loop():
    """Runs time-gated jobs every 60 seconds. Sync work runs in a thread."""
    global _last_cron_minute
    loop = asyncio.get_event_loop()
    while True:
        try:
            now = datetime.now(NY)
            if now.minute != _last_cron_minute:
                _last_cron_minute = now.minute
                await loop.run_in_executor(None, _run_cron_jobs, now)
        except Exception as e:
            print(f"[cron_loop] {e}")
        await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app):
    slack_task = asyncio.create_task(_slack_loop())
    cron_task = asyncio.create_task(_cron_loop())
    print("[app] Started: Slack agent (5s) + cron jobs (time-gated)")
    yield
    slack_task.cancel()
    cron_task.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/debug/pacing")
def debug_pacing():
    """Diagnose pacing data in the deployed container."""
    import json, os
    result = {"database_url_set": bool(os.environ.get("DATABASE_URL")),
              "error": None, "raw_value": None, "parsed": None}
    try:
        from db.connection import get_conn
        with get_conn() as c:
            row = c.execute("SELECT value FROM agent_state WHERE key='pacing_cache'").fetchone()
            if row:
                result["raw_value"] = row[0][:200] if isinstance(row[0], str) else str(row[0])[:200]
                result["parsed"] = json.loads(row[0])
            else:
                result["error"] = "no pacing_cache row found"
    except Exception as e:
        result["error"] = str(e)
    return result


app.include_router(dashboard_router)


@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard", status_code=302)


@app.post("/api/approve-week")
async def approve_week():
    """Write approval for the current week — same as typing 'approved week' in Slack."""
    from datetime import date, timedelta
    today = date.today()
    import hashlib, os
    secret = os.environ.get("BEEZY_ANTHROPIC_API_KEY", "secret")
    token = hashlib.sha256((str(today) + secret).encode()).hexdigest()[:16]
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO calendar_approvals (week_start, token, approved_at, approved_by) "
                "VALUES (%s, %s, NOW(), 'dashboard') "
                "ON CONFLICT (week_start) DO UPDATE SET approved_at=NOW(), approved_by='dashboard'",
                (today, token)
            )
            conn.commit()
        return JSONResponse({"status": "approved", "week_start": today.isoformat()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/approve-month")
async def approve_month():
    """Mark this month's calendar as approved — same as typing 'approved' in Slack."""
    from datetime import date
    today = date.today()
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            month_start = today.replace(day=1)
            import calendar as _cal
            last_day = _cal.monthrange(today.year, today.month)[1]
            month_end = today.replace(day=last_day)
            import hashlib, os
            secret = os.environ.get("BEEZY_ANTHROPIC_API_KEY", "secret")
            current = month_start - timedelta(days=month_start.weekday())
            weeks_approved = 0
            while current <= month_end:
                token = hashlib.sha256((str(current) + secret).encode()).hexdigest()[:16]
                conn.execute(
                    "INSERT INTO calendar_approvals (week_start, token, approved_at, approved_by) "
                    "VALUES (%s, %s, NOW(), 'dashboard') "
                    "ON CONFLICT (week_start) DO UPDATE SET approved_at=NOW(), approved_by='dashboard'",
                    (current, token)
                )
                weeks_approved += 1
                current += timedelta(days=7)
            conn.commit()
        return JSONResponse({"status": "approved", "weeks_approved": weeks_approved})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/retry-slot")
async def retry_slot(id: str):
    """Re-queue a failed calendar_executions row by setting status='pending'."""
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            conn.execute(
                "UPDATE calendar_executions SET status='pending', notes='retry requested from dashboard' WHERE id=%s",
                (id,)
            )
            conn.commit()
        return JSONResponse({"status": "queued", "id": id})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/status")
async def api_status():
    """JSON snapshot: today's slots, approval status, pacing."""
    import json
    from datetime import date
    today = date.today()
    result = {"date": today.isoformat(), "slots": [], "week_approved": False, "pacing": {}}
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            slots = conn.execute(
                "SELECT id, content_type, audience, status, actual_revenue FROM calendar_executions WHERE slot_date=%s",
                (today,)
            ).fetchall()
            result["slots"] = [{"id": str(r[0]), "type": r[1], "audience": r[2], "status": r[3], "revenue": float(r[4] or 0)} for r in slots]

            apv = conn.execute(
                "SELECT approved_at FROM calendar_approvals WHERE week_start <= %s AND %s < week_start + INTERVAL '7 days' AND approved_at IS NOT NULL LIMIT 1",
                (today, today)
            ).fetchone()
            result["week_approved"] = apv is not None

            cache = conn.execute("SELECT value FROM agent_state WHERE key='pacing_cache'").fetchone()
            if cache:
                result["pacing"] = json.loads(cache[0])
    except Exception as e:
        result["error"] = str(e)
    return result


@app.post("/api/boost")
async def boost_revenue():
    """Boost mode: find best R2-compliant audience and launch a campaign."""
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _run_boost)
    from fastapi.responses import RedirectResponse
    if result.get("ok"):
        return RedirectResponse(url="/dashboard", status_code=303)
    return JSONResponse(result, status_code=500)


def _run_boost() -> dict:
    """Find the highest-RPR audience not sent to in 7+ days and queue a campaign."""
    from datetime import date, timedelta
    from db.connection import get_conn
    from lib.slack import post_draft
    today = date.today()
    seven_ago = today - timedelta(days=7)
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT audience, content_type, AVG(actual_rpr) as rpr
                   FROM calendar_executions
                   WHERE is_preliminary = false AND actual_rpr > 0
                     AND slot_date > CURRENT_DATE - INTERVAL '90 days'
                   GROUP BY audience, content_type
                   HAVING COUNT(*) >= 2
                   ORDER BY rpr DESC LIMIT 10"""
            ).fetchall()
            # Filter to those not sent in last 7 days
            for r in rows:
                audience, ct = r[0], r[1]
                last = conn.execute(
                    "SELECT MAX(slot_date) FROM calendar_executions WHERE audience=%s AND status IN ('dispatched','completed')",
                    (audience,)
                ).fetchone()
                last_date = last[0] if last and last[0] else None
                if last_date is None or (today - last_date).days >= 7:
                    rpr = float(r[2])
                    slot = {
                        "date": today.isoformat(),
                        "content_type": "klaviyo_campaign",
                        "audience": audience,
                        "topic_angle": "Boost send — top performing segment",
                        "send_time_est": "18:00",
                        "priority": "high",
                        "revenue_estimate": round(rpr * 1000, 2),
                    }
                    try:
                        from workers.beezy_campaign import run as campaign_run
                        result = campaign_run(slot)
                        post_draft(
                            title=f"Boost Activated — {audience}",
                            summary_lines=[f"Audience: {audience}", f"Est. revenue: ${slot['revenue_estimate']:,.0f}", f"Campaign: {result.get('campaign_id','?')}"],
                            body=f"Boost mode launched via dashboard. RPR history: ${rpr:.3f}",
                        )
                        return {"ok": True, "audience": audience, "campaign_id": result.get("campaign_id")}
                    except Exception as e:
                        return {"ok": False, "error": str(e)}
        return {"ok": False, "error": "No eligible audience found (all in 7-day cooldown)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/slack/interactive")
async def slack_interactive(request: Request):
    """
    Handles Slack interactive callbacks (button clicks).

    Currently supports:
      action_id = "apply_flow_fix"  →  value = "<template_id>:<flow_id>"
        Assigns the pre-generated fix template to all email messages in the flow,
        then posts a confirmation back to Slack.

    Slack requires a 200 response within 3s; heavy work runs in a thread executor.
    """
    import json
    import urllib.parse

    raw_body = await request.body()
    # Slack sends as URL-encoded: payload=<json>
    parsed   = urllib.parse.parse_qs(raw_body.decode("utf-8"))
    payload_str = (parsed.get("payload") or ["{}"])[0]
    try:
        payload = json.loads(payload_str)
    except Exception:
        return JSONResponse({"error": "bad payload"}, status_code=400)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _handle_slack_action, payload)
    return JSONResponse({"ok": True})


def _handle_slack_action(payload: dict) -> None:
    """Synchronous handler for Slack interactive actions. Runs in a thread."""
    import os
    import httpx as _httpx
    from lib.slack import _post as slack_post

    actions = payload.get("actions") or []
    response_url = payload.get("response_url", "")

    for action in actions:
        action_id = action.get("action_id", "")
        value     = action.get("value", "")

        if action_id == "apply_flow_fix":
            parts = value.split(":", 1)
            if len(parts) != 2:
                _reply(response_url, "❌ Invalid action value — could not parse template/flow IDs.")
                continue
            template_id, flow_id = parts
            _apply_flow_fix_template(template_id, flow_id, response_url)


def _apply_flow_fix_template(template_id: str, flow_id: str, response_url: str) -> None:
    """Assign a pre-built template to all email messages in a flow."""
    import os
    import httpx as _httpx

    api_key  = os.environ.get("KLAVIYO_API_KEY", "")
    revision = "2025-10-15"
    headers  = {
        "Authorization": f"Klaviyo-API-Key {api_key}",
        "revision": revision,
        "Content-Type": "application/json",
    }

    # Get all flow messages
    try:
        resp = _httpx.get(
            f"https://a.klaviyo.com/api/flows/{flow_id}/flow-messages/",
            headers=headers,
            params={"fields[flow-message]": "id,channel"},
            timeout=20,
        )
        messages = resp.json().get("data", []) if resp.is_success else []
    except Exception as exc:
        _reply(response_url, f"❌ Could not fetch flow messages: {exc}")
        return

    email_messages = [m for m in messages
                      if (m.get("attributes", {}).get("channel") or "email") == "email"]

    if not email_messages:
        _reply(response_url, f"⚠️ No email messages found in flow `{flow_id}`.")
        return

    applied, errors = [], []
    for msg in email_messages:
        msg_id = msg["id"]
        try:
            r = _httpx.post(
                "https://a.klaviyo.com/api/flow-message-assign-template/",
                headers=headers,
                json={"data": {
                    "type": "flow-message",
                    "id":   msg_id,
                    "relationships": {
                        "template": {"data": {"type": "template", "id": template_id}}
                    },
                }},
                timeout=20,
            )
            if r.is_success:
                applied.append(msg_id)
            else:
                errors.append(f"{msg_id}: {r.status_code}")
        except Exception as exc:
            errors.append(f"{msg_id}: {exc}")

    if applied:
        _reply(
            response_url,
            f"✅ Fix applied to {len(applied)} flow message(s).\n"
            f"Template `{template_id}` → flow `{flow_id}`.\n"
            f"{'⚠️ Errors: ' + str(errors) if errors else ''}",
        )
    else:
        _reply(response_url, f"❌ Failed to apply template. Errors: {errors}")


def _reply(response_url: str, text: str) -> None:
    """Post a follow-up message to the Slack response_url."""
    if not response_url:
        return
    import httpx as _httpx
    try:
        _httpx.post(response_url,
                    json={"text": text, "replace_original": False},
                    timeout=10)
    except Exception:
        pass
