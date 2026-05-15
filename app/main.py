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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.dashboard import router as dashboard_router

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NY = ZoneInfo("America/New_York")
_last_cron_minute = -1


async def _slack_loop():
    """Polls Slack every 5s; exponential backoff (30s→60s→…→300s) on network errors."""
    import httpx as _httpx
    loop = asyncio.get_event_loop()
    _net_backoff = 0   # consecutive network-error count
    _net_logged  = False
    while True:
        try:
            from agents.slack_agent import run_once
            await loop.run_in_executor(None, run_once)
            if _net_backoff:
                print("[slack_loop] Slack connection restored.")
            _net_backoff = 0
            _net_logged  = False
            await asyncio.sleep(5)
        except _httpx.NetworkError as e:
            _net_backoff += 1
            sleep_secs = min(30 * _net_backoff, 300)   # 30→60→90→…→300s cap
            if not _net_logged:
                print(f"[slack_loop] Network unreachable ({e}). Backing off — will retry silently.")
                _net_logged = True
            await asyncio.sleep(sleep_secs)
        except Exception as e:
            print(f"[slack_loop] {e}")
            await asyncio.sleep(30)


def _mark_ran_today(key: str) -> None:
    """Write today's date into agent_state so catch-up logic knows this job ran."""
    try:
        from db.connection import get_conn
        from datetime import date
        today = date.today().isoformat()
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO agent_state (key, value, updated_at) VALUES (%s, %s, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
                (key, today),
            )
            conn.commit()
    except Exception:
        pass


def _ran_today(key: str) -> bool:
    """Return True if this job already ran today (checked via agent_state)."""
    try:
        from db.connection import get_conn
        from datetime import date
        today = date.today().isoformat()
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM agent_state WHERE key = %s", (key,)
            ).fetchone()
        return row is not None and row[0] == today
    except Exception:
        return False


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

    # Pacing brain: 7:30am ET target. Catch-up window: 7:30–8:29am (server-restart safe).
    if (h == 7 and m >= 30) or h == 8:
        if not _ran_today("cron_pacing_brain"):
            try:
                from pacing.cron import run_daily as pacing_daily
                print("[cron] pacing brain" + (" [catch-up]" if not (h == 7 and m == 30) else ""))
                pacing_daily()
                _mark_ran_today("cron_pacing_brain")
            except Exception as e:
                print(f"[cron] pacing error: {e}")

    # Orchestrator: 8:00am ET target. Catch-up window: 8:00–9:00am (runs once via _ran_today guard).
    if h == 8 or (h == 9 and m == 0):
        if not _ran_today("cron_orchestrator"):
            try:
                from pacing.orchestrator import run_daily
                print("[cron] orchestrator" + (" [catch-up]" if not (h == 8 and m == 0) else ""))
                run_daily()
                _mark_ran_today("cron_orchestrator")
            except Exception as e:
                print(f"[cron] orchestrator error: {e}")

    # Audience health monitor — daily at 7:40am (once per day guard)
    if h == 7 and m == 40:
        if not _ran_today("cron_audience_health"):
            try:
                from workers.audience_health import run_audience_health
                print("[cron] audience health")
                run_audience_health()
                _mark_ran_today("cron_audience_health")
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

    # Morning briefing — 8:05am daily (after orchestrator, once per day guard)
    if h == 8 and m == 5:
        if not _ran_today("cron_morning_brief"):
            try:
                from workers.morning_brief import run_morning_brief
                print("[cron] morning brief")
                run_morning_brief()
                _mark_ran_today("cron_morning_brief")
            except Exception as e:
                print(f"[cron] morning brief error: {e}")

    if h == 10 and m == 0:
        try:
            from workers.klaviyo_campaign import auto_create_pending
            print("[cron] hive mind campaign")
            auto_create_pending()
        except Exception as e:
            print(f"[cron] hive mind error: {e}")

    if h == 10 and m == 30:
        try:
            from workers.deliverability_monitor import run_deliverability_check
            print("[cron] deliverability check")
            run_deliverability_check()
        except Exception as e:
            print(f"[cron] deliverability error: {e}")

    # Weekly approval brief — Sunday 9:05pm ET (after learning_loop at 9pm finishes)
    if now.weekday() == 6 and h == 21 and m == 5:
        try:
            from pacing.weekly_brief import run_weekly_brief
            print("[cron] weekly brief")
            run_weekly_brief()
        except Exception as e:
            print(f"[cron] weekly brief error: {e}")

    # Monday 9:30am: escalation nudge if week still not approved
    if now.weekday() == 0 and h == 9 and m == 30:
        try:
            from pacing.weekly_brief import run_approval_nudge
            print("[cron] approval nudge")
            run_approval_nudge()
        except Exception as e:
            print(f"[cron] approval nudge error: {e}")

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.post("/api/refresh-pacing")
async def refresh_pacing():
    """Manually trigger pacing cache refresh — pulls live MTD revenue from Klaviyo."""
    try:
        loop = asyncio.get_event_loop()
        from workers.pacing_cache import refresh_pacing_cache
        await loop.run_in_executor(None, refresh_pacing_cache)
        from db.connection import get_conn
        import json as _json
        with get_conn() as c:
            row = c.execute("SELECT value FROM agent_state WHERE key='pacing_cache'").fetchone()
            result = _json.loads(row[0]) if row else {}
        return JSONResponse({"status": "ok", "data": result})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/refresh-audience-health")
async def refresh_audience_health():
    """Pull Klaviyo campaign history → compute RPR per audience → cache in agent_state."""
    try:
        loop = asyncio.get_running_loop()
        from app.dashboard import pull_klaviyo_audience_health
        result = await loop.run_in_executor(None, pull_klaviyo_audience_health)
        return JSONResponse({"status": "ok", "audiences": len(result)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/run-flow-check")
async def run_flow_check():
    """Run the weekly flow health check now — pulls 30-day Klaviyo flow metrics."""
    try:
        loop = asyncio.get_running_loop()
        from workers.flow_monitor import run_flow_check as _flow_check
        result = await loop.run_in_executor(None, _flow_check)
        return JSONResponse({"status": "ok", "result": str(result)[:200]})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/run-deliverability-check")
async def run_deliverability_check_endpoint():
    """Run deliverability check now — pulls 30-day Klaviyo bounce/spam/unsub rates."""
    try:
        loop = asyncio.get_running_loop()
        from workers.deliverability_monitor import run_deliverability_check
        result = await loop.run_in_executor(None, run_deliverability_check)
        return JSONResponse({"status": "ok", "result": result})
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

    from config import KLAVIYO_REVISION
    api_key  = os.environ.get("KLAVIYO_API_KEY", "")
    headers  = {
        "Authorization": f"Klaviyo-API-Key {api_key}",
        "revision": KLAVIYO_REVISION,
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


# ── Next.js Dashboard JSON API ────────────────────────────────────────────────
# All /api/data/* endpoints return pure JSON consumed by the Next.js dashboard.
# They reuse the same data functions as the FastAPI HTML dashboard.

@app.get("/api/data/overview")
def api_data_overview():
    """Combined overview: pacing + today's slots + approval status."""
    from app.dashboard import _pacing, _today_slots, _next_send_date, _approval_status
    p = _pacing()
    slots = _today_slots()
    apv = _approval_status()
    # Serialize date objects
    if apv.get("week_start"):
        apv["week_start"] = str(apv["week_start"])
    return JSONResponse({
        "pacing": p,
        "today_slots": slots,
        "next_send": _next_send_date() if not slots else "",
        "approval": apv,
    })


@app.get("/api/data/calendar")
def api_data_calendar():
    """Full calendar plan for the current month."""
    from app.dashboard import _upcoming_slots, _approval_status
    apv = _approval_status()
    if apv.get("week_start"):
        apv["week_start"] = str(apv["week_start"])
    return JSONResponse({"slots": _upcoming_slots(), "approval": apv})


@app.get("/api/data/audiences")
def api_data_audiences():
    """Audience health + burn list."""
    from app.dashboard import _audience_health
    import json as _json
    health = _audience_health()
    burn_list: list[str] = []
    try:
        from db.connection import get_conn
        with get_conn() as c:
            row = c.execute("SELECT value FROM agent_state WHERE key='burned_audiences'").fetchone()
            if row:
                d = _json.loads(row[0]) if isinstance(row[0], str) else row[0]
                burn_list = d.get("audiences", [])
    except Exception:
        pass
    return JSONResponse({"health": health, "burn_list": burn_list})


@app.get("/api/data/analytics")
def api_data_analytics():
    """Top performers + learning loop RPR table + 30-day revenue trend."""
    from app.dashboard import _top_performers, _learning_loop
    import json as _json
    # 30-day daily revenue from performance table
    trend: list = []
    try:
        from db.connection import get_conn
        with get_conn() as c:
            rows = c.execute(
                """SELECT DATE(window_start) as day, SUM(metric_value)
                   FROM performance
                   WHERE source='klaviyo' AND metric_name='conversion_value'
                     AND window_start >= CURRENT_DATE - INTERVAL '30 days'
                   GROUP BY day ORDER BY day"""
            ).fetchall()
            trend = [{"date": str(r[0]), "revenue": float(r[1] or 0)} for r in rows]
    except Exception:
        pass
    return JSONResponse({
        "top_performers": _top_performers(),
        "learning": _learning_loop(),
        "revenue_trend": trend,
    })


@app.get("/api/data/flows")
def api_data_flows():
    """Flow health from strategies table."""
    from app.dashboard import _flow_health
    data = _flow_health() or {}
    return JSONResponse(data)


@app.get("/api/data/content")
def api_data_content():
    """Hive Mind issues + SEO topics + episodes."""
    import json as _json
    issues: list = []
    seo_topics: list = []
    episodes: list = []
    try:
        from db.connection import get_conn
        with get_conn() as c:
            rows = c.execute(
                """SELECT number, subject_line, pillar, status, shopify_page_url,
                          cover_image_url, klaviyo_campaign_id, scheduled_send_at, published_at
                   FROM issues ORDER BY number DESC LIMIT 30"""
            ).fetchall()
            issues = [{"number": r[0], "subject_line": r[1], "pillar": r[2],
                       "status": r[3], "page_url": r[4], "cover_url": r[5],
                       "campaign_id": r[6],
                       "scheduled": str(r[7])[:10] if r[7] else "",
                       "published": str(r[8])[:10] if r[8] else ""} for r in rows]

            rows = c.execute(
                "SELECT keyword, status, published_url, error_detail, created_at FROM seo_topics ORDER BY created_at DESC LIMIT 30"
            ).fetchall()
            seo_topics = [{"keyword": r[0], "status": r[1], "url": r[2],
                           "error": r[3], "created": str(r[4])[:10] if r[4] else ""} for r in rows]

            rows = c.execute(
                """SELECT title, episode_type, shopify_page_url, duration_minutes,
                          deployed_at, klaviyo_campaign_id_a
                   FROM episodes ORDER BY deployed_at DESC LIMIT 30"""
            ).fetchall()
            episodes = [{"title": r[0], "type": r[1], "url": r[2], "duration": r[3],
                         "deployed": str(r[4])[:10] if r[4] else "", "campaign_a": r[5]} for r in rows]
    except Exception as e:
        pass
    return JSONResponse({"issues": issues, "seo_topics": seo_topics, "episodes": episodes})


@app.get("/api/data/system")
def api_data_system():
    """System health: cron sentinels, DB connection, env vars present."""
    import json as _json
    import os as _os
    cron_sentinels: dict = {}
    recent_runs: list = []
    try:
        from db.connection import get_conn
        with get_conn() as c:
            rows = c.execute(
                "SELECT key, value, updated_at FROM agent_state WHERE key LIKE 'cron_%' ORDER BY key"
            ).fetchall()
            cron_sentinels = {r[0]: {"value": r[1], "updated": str(r[2])[:16] if r[2] else ""} for r in rows}

            rows = c.execute(
                """SELECT id, worker, status, cost_usd, elapsed_seconds, created_at
                   FROM runs ORDER BY created_at DESC LIMIT 20"""
            ).fetchall()
            recent_runs = [{"id": str(r[0]), "worker": r[1], "status": r[2],
                            "cost": float(r[3] or 0), "elapsed": float(r[4] or 0),
                            "created": str(r[5])[:16] if r[5] else ""} for r in rows]
    except Exception as e:
        cron_sentinels = {"error": str(e)}

    env_keys = ["KLAVIYO_API_KEY", "SHOPIFY_ACCESS_TOKEN", "BEEZY_ANTHROPIC_API_KEY",
                "SLACK_BOT_TOKEN", "DATABASE_URL", "HIGGSFIELD_KEY"]
    env_status = {k: bool(_os.environ.get(k)) for k in env_keys}

    return JSONResponse({
        "cron_sentinels": cron_sentinels,
        "recent_runs": recent_runs,
        "env_status": env_status,
        "db_ok": "error" not in cron_sentinels,
    })


@app.post("/api/burn-audience")
async def api_burn_audience(audience: str):
    """Add an audience to the burn list."""
    import json as _json
    try:
        from db.connection import get_conn
        with get_conn() as c:
            row = c.execute("SELECT value FROM agent_state WHERE key='burned_audiences'").fetchone()
            if row:
                d = _json.loads(row[0]) if isinstance(row[0], str) else row[0]
            else:
                d = {"audiences": []}
            aud_list = d.get("audiences", [])
            norm = audience.lower().replace("-", "_").replace(" ", "_")
            if norm not in aud_list:
                aud_list.append(norm)
            d["audiences"] = aud_list
            d["updated_at"] = datetime.now(NY).isoformat()
            c.execute(
                "INSERT INTO agent_state (key,value,updated_at) VALUES ('burned_audiences',%s,NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                (_json.dumps(d),)
            )
            c.commit()
        return JSONResponse({"status": "burned", "audience": norm, "burn_list": aud_list})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/unburn-audience")
async def api_unburn_audience(audience: str):
    """Remove an audience from the burn list."""
    import json as _json
    try:
        from db.connection import get_conn
        with get_conn() as c:
            row = c.execute("SELECT value FROM agent_state WHERE key='burned_audiences'").fetchone()
            if not row:
                return JSONResponse({"status": "not_found"})
            d = _json.loads(row[0]) if isinstance(row[0], str) else row[0]
            norm = audience.lower().replace("-", "_").replace(" ", "_")
            aud_list = [a for a in d.get("audiences", []) if a != norm]
            d["audiences"] = aud_list
            d["updated_at"] = datetime.now(NY).isoformat()
            c.execute(
                "INSERT INTO agent_state (key,value,updated_at) VALUES ('burned_audiences',%s,NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                (_json.dumps(d),)
            )
            c.commit()
        return JSONResponse({"status": "unburned", "audience": norm, "burn_list": aud_list})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/slack-command")
async def api_slack_command(request: Request):
    """Send a message to #beezy-agents as if typed by Boris."""
    import json as _json
    import os as _os
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)
    try:
        import httpx as _httpx
        token = _os.environ.get("SLACK_BOT_TOKEN", "")
        channel = "C0B3DEUJS9G"  # #beezy-agents
        resp = _httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"channel": channel, "text": f"[dashboard] {message}"},
            timeout=10,
        )
        ok = resp.json().get("ok", False)
        if not ok:
            return JSONResponse({"error": resp.json().get("error", "slack error")}, status_code=500)
        return JSONResponse({"status": "sent", "message": message})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/generate-calendar")
async def api_generate_calendar():
    """Trigger calendar generation for next month."""
    try:
        loop = asyncio.get_event_loop()
        def _run():
            from pacing.calendar import run_monthly
            run_monthly()
            return "ok"
        await loop.run_in_executor(None, _run)
        return JSONResponse({"status": "started"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/run-orchestrator")
async def api_run_orchestrator():
    """Trigger today's orchestrator run immediately."""
    try:
        loop = asyncio.get_event_loop()
        def _run():
            from pacing.orchestrator import run_daily
            run_daily()
            return "ok"
        await loop.run_in_executor(None, _run)
        return JSONResponse({"status": "dispatched"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/run-ingestion")
async def api_run_ingestion():
    """Trigger a Klaviyo + Shopify ingestion sync now."""
    try:
        loop = asyncio.get_event_loop()
        def _run():
            from ingestion.sync import run_sync
            run_sync()
            return "ok"
        await loop.run_in_executor(None, _run)
        return JSONResponse({"status": "started"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/run-learning-loop")
async def api_run_learning_loop():
    """Trigger weekly learning loop review now."""
    try:
        loop = asyncio.get_event_loop()
        def _run():
            from workers.learning_loop import run_weekly
            run_weekly()
            return "ok"
        await loop.run_in_executor(None, _run)
        return JSONResponse({"status": "started"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
