"""
Beezy Agents — unified web server.
Slack agent runs every 5 seconds.
All cron jobs run on time-based schedule in background.
Single deployment handles everything.

SINGLE SOURCE OF TRUTH FOR CAMPAIGN DISPATCH:
  pacing.orchestrator.run_daily at 8am ET — the ONLY system that dispatches
  calendar campaigns. No other worker should create or dispatch calendar slots.

Removed: workers.calendar_campaign_builder (was creating duplicate calendar
plans and dispatching them independently, causing double-dispatch at 8am).
"""
import sys
import os
import json
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
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
    _net_backoff = 0
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
            sleep_secs = min(30 * _net_backoff, 300)
            if not _net_logged:
                print(f"[slack_loop] Network unreachable ({e}). Backing off — will retry silently.")
                _net_logged = True
            await asyncio.sleep(sleep_secs)
        except Exception as e:
            print(f"[slack_loop] {e}")
            await asyncio.sleep(30)


def _try_claim_today(key: str) -> bool:
    """Atomically claim today's run slot for `key`.

    Uses INSERT ... ON CONFLICT DO NOTHING so only ONE process/instance can
    claim a given key per calendar day — even if two instances race.

    Returns True if this call successfully claimed the slot (run it).
    Returns False if another instance already claimed it (skip it).
    """
    try:
        from db.connection import get_conn
        from datetime import date
        today = date.today().isoformat()
        with get_conn() as conn:
            # Try to INSERT today's date. If the key already exists with today's
            # value another instance already ran — DO NOTHING and return 0 rows.
            # If the value is stale (yesterday) update it and return 1 row.
            result = conn.execute(
                """INSERT INTO agent_state (key, value, updated_at) VALUES (%s, %s, NOW())
                   ON CONFLICT (key) DO UPDATE
                     SET value = EXCLUDED.value, updated_at = NOW()
                     WHERE agent_state.value != EXCLUDED.value
                   RETURNING key""",
                (key, today),
            ).fetchone()
            conn.commit()
        return result is not None  # True = we claimed it; False = already claimed today
    except Exception as e:
        print(f"[cron] _try_claim_today({key}) failed: {e}")
        return False  # On DB error, skip rather than double-run


def _run_cron_jobs(now: datetime) -> None:
    """Synchronous cron dispatch — runs in a thread executor."""
    h, m = now.hour, now.minute
    from lib.jobs import run_job

    if h % 4 == 0 and m < 2:
        try:
            with run_job("ingestion_sync", trigger="cron") as job:  # time-gated
                from ingestion.sync import run_shopify_sync, run_klaviyo_sync
                print("[cron] ingestion sync")
                run_shopify_sync()
                run_klaviyo_sync()
        except Exception as e:
            print(f"[cron] ingestion error: {e}")

    # Pacing brain: 7:30am ET. Catch-up window 7:30–8:59am. Atomic claim prevents double-run.
    if (h == 7 and m >= 30) or h == 8:
        if _try_claim_today("cron_pacing_brain"):
            try:
                with run_job("cron_pacing_brain", trigger="cron") as job:  # claim-gated
                    from pacing.cron import run_daily as pacing_daily
                    print("[cron] pacing brain")
                    pacing_daily()
            except Exception as e:
                print(f"[cron] pacing error: {e}")

    # Orchestrator: 8:00am ET. Catch-up window 8:00–9:00am. Atomic claim prevents double-run.
    # THIS IS THE ONLY SYSTEM THAT DISPATCHES CALENDAR CAMPAIGNS.
    if h == 8 or (h == 9 and m == 0):
        if _try_claim_today("cron_orchestrator"):
            try:
                with run_job("cron_orchestrator", trigger="cron") as job:  # claim-gated
                    from pacing.orchestrator import run_daily
                    print("[cron] orchestrator")
                    run_daily()
            except Exception as e:
                print(f"[cron] orchestrator error: {e}")

    # Audience health monitor — daily at 7:40am
    if h == 7 and m == 40:
        if _try_claim_today("cron_audience_health"):
            try:
                with run_job("cron_audience_health", trigger="cron") as job:  # claim-gated
                    from workers.audience_health import run_audience_health
                    print("[cron] audience health")
                    run_audience_health()
            except Exception as e:
                print(f"[cron] audience health error: {e}")

    # Pacing cache refresh — daily at 7:35am
    if h == 7 and m == 35:
        try:
            with run_job("pacing_cache_refresh", trigger="cron") as job:  # time-gated
                from workers.pacing_cache import refresh_pacing_cache
                print('[cron] pacing cache refresh')
                refresh_pacing_cache()
        except Exception as e:
            print(f'[cron] pacing cache error: {e}')

    # Revenue backfill — daily at 9am
    if h == 9 and m == 0:
        try:
            with run_job("revenue_backfill", trigger="cron") as job:  # time-gated
                from workers.revenue_backfill import run_backfill
                print("[cron] revenue backfill")
                run_backfill()
        except Exception as e:
            print(f"[cron] backfill error: {e}")

    # Weekly learning review — Sunday at 9pm ET
    if now.weekday() == 6 and h == 21 and m == 0:
        try:
            with run_job("learning_loop_weekly", trigger="cron") as job:  # time-gated
                from workers.learning_loop import run_weekly
                print("[cron] weekly learning review")
                run_weekly()
        except Exception as e:
            print(f"[cron] weekly review error: {e}")

    # Weekly flow health check — Sunday at 9:15pm ET
    if now.weekday() == 6 and h == 21 and m == 15:
        try:
            with run_job("flow_monitor", trigger="cron") as job:  # time-gated
                from workers.flow_monitor import run_flow_check
                print("[cron] flow health check")
                run_flow_check()
        except Exception as e:
            print(f"[cron] flow monitor error: {e}")

    # Bi-weekly pacing check — 15th at 9:30am
    if now.day == 15 and h == 9 and m == 30:
        try:
            with run_job("learning_loop_biweekly", trigger="cron") as job:  # time-gated
                from workers.learning_loop import run_biweekly
                print("[cron] bi-weekly pacing check")
                run_biweekly()
        except Exception as e:
            print(f"[cron] biweekly error: {e}")

    # Monthly retrospective — 1st at 9:30am
    if now.day == 1 and h == 9 and m == 30:
        try:
            with run_job("learning_loop_monthly", trigger="cron") as job:  # time-gated
                from workers.learning_loop import run_monthly
                print("[cron] monthly retrospective")
                run_monthly()
        except Exception as e:
            print(f"[cron] monthly retro error: {e}")

    # Pending campaign auto-schedule — every 5 minutes
    if m % 5 == 0:
        try:
            from workers.beezy_campaign import (
                check_pending_schedules, _pending_schedules_due)
            if _pending_schedules_due():                              # 5-min watchdog
                with run_job("pending_schedules", trigger="cron") as job:
                    job.detail = {"acted": check_pending_schedules()}
        except Exception as e:
            print(f"[cron] pending_schedules error: {e}")

    # TTS 30-min watchdog — every 5 minutes
    if m % 5 == 0:
        try:
            from workers.sleep_audio_producer import (
                check_tts_timeouts, _tts_candidates_due)
            if _tts_candidates_due():                                 # 5-min watchdog
                with run_job("tts_timeout_watchdog", trigger="cron") as job:
                    job.detail = {"acted": check_tts_timeouts()}
        except Exception as e:
            print(f"[cron] tts_timeout error: {e}")

    # Control-plane watchdog — hourly. Heartbeat gated to the 11:00 ET tick
    # (after deliverability_check at 10:30) so it reports the day's critical
    # jobs; the _try_claim_today claim is a restart-safety belt so a
    # top-of-hour restart cannot double-post the heartbeat.
    if m == 0:
        try:
            with run_job("watchdog", trigger="cron") as job:
                from workers.watchdog import run_watchdog
                hb = (h == 11) and _try_claim_today("watchdog_heartbeat")
                job.detail = run_watchdog(emit_heartbeat=hb)
        except Exception as e:
            print(f"[cron] watchdog error: {e}")

    # Morning briefing — 8:05am daily
    if h == 8 and m == 5:
        if _try_claim_today("cron_morning_brief"):
            try:
                with run_job("cron_morning_brief", trigger="cron") as job:  # claim-gated
                    from workers.morning_brief import run_morning_brief
                    print("[cron] morning brief")
                    run_morning_brief()
            except Exception as e:
                print(f"[cron] morning brief error: {e}")

    # Publish pages + update index pages — 8:05am ET (right after orchestrator)
    # Adds new Hive Mind issues to the archive + SSH_FEATURED, adds episodes to library.
    # Idempotent — skips anything already up-to-date.
    if h == 8 and m == 5:
        if _try_claim_today("cron_publish_and_index"):
            try:
                with run_job("cron_publish_and_index", trigger="cron") as job:  # claim-gated
                    from workers.publish_and_index import run as run_publish
                    print("[cron] publish and index")
                    run_publish()
            except Exception as e:
                print(f"[cron] publish_and_index error: {e}")

    # Hive Mind newsletter campaign auto-create — 10am daily
    # NOTE: This ONLY creates Hive Mind newsletter campaigns from the issues table.
    # It does NOT touch the main calendar campaign pipeline.
    if h == 10 and m == 0:
        try:
            with run_job("hive_mind_campaign", trigger="cron") as job:  # time-gated
                from workers.klaviyo_campaign import auto_create_pending
                print("[cron] hive mind campaign")
                auto_create_pending()
        except Exception as e:
            print(f"[cron] hive mind error: {e}")

    # REMOVED: calendar_campaign_builder at 9:45am — was creating a parallel
    # calendar plan and dispatching it independently, causing duplicate campaigns.
    # The orchestrator at 8am is the single source of truth for campaign dispatch.

    if h == 10 and m == 30:
        try:
            with run_job("deliverability_check", trigger="cron") as job:  # time-gated
                from workers.deliverability_monitor import run_deliverability_check
                print("[cron] deliverability check")
                run_deliverability_check()
        except Exception as e:
            print(f"[cron] deliverability error: {e}")

    # Hive Mind status sync — 9:10pm daily
    if h == 21 and m == 10:
        try:
            with run_job("hive_mind_status_sync", trigger="cron") as job:  # time-gated
                from workers.hive_mind_status_sync import sync_sent_campaigns
                print("[cron] hive_mind_status_sync")
                sync_sent_campaigns()
        except Exception as e:
            print(f"[cron] hive_mind_status_sync error: {e}")

    # Weekly approval brief — Sunday 9:05pm ET
    if now.weekday() == 6 and h == 21 and m == 5:
        try:
            with run_job("weekly_brief", trigger="cron") as job:  # time-gated
                from pacing.weekly_brief import run_weekly_brief
                print("[cron] weekly brief")
                run_weekly_brief()
        except Exception as e:
            print(f"[cron] weekly brief error: {e}")

    # Monday 9:30am: escalation nudge if week still not approved
    if now.weekday() == 0 and h == 9 and m == 30:
        try:
            with run_job("approval_nudge", trigger="cron") as job:  # time-gated
                from pacing.weekly_brief import run_approval_nudge
                print("[cron] approval nudge")
                run_approval_nudge()
        except Exception as e:
            print(f"[cron] approval nudge error: {e}")

    # Welcome video worker — every tick, fast no-op when queue is empty
    try:
        from workers.welcome_video import run_once as _welcome_video_run_once
        _welcome_video_run_once()
    except Exception as e:
        print(f"[cron] welcome_video error: {e}")

    import calendar as _cal
    last_day = _cal.monthrange(now.year, now.month)[1]
    if now.day == last_day - 7 and h == 9 and m == 0:
        try:
            with run_job("calendar_generation", trigger="cron") as job:  # time-gated
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

@app.get("/api/deploy/health")
async def deploy_health():
    return {"status": "ok"}


@app.post("/webhook/first-order")
async def first_order_webhook(request: Request):
    """
    Klaviyo calls this when a customer places their first order.
    Queues a welcome-video job and returns immediately.

    Expected payload: {"email": "...", "first_name": "...", "order_id": "..."}
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    email      = (payload.get("email") or "").strip().lower()
    first_name = (payload.get("first_name") or "").strip()
    order_id   = str(payload.get("order_id") or "").strip()

    if not email or not first_name or not order_id:
        raise HTTPException(
            status_code=400,
            detail="missing required field: email, first_name, order_id",
        )

    from db.connection import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO welcome_video_jobs (order_id, email, first_name, status)
                VALUES (%s, %s, %s, 'pending')
                ON CONFLICT (order_id) DO NOTHING
                """,
                (order_id, email, first_name),
            )
        conn.commit()

    return {"status": "queued", "order_id": order_id}


@app.get("/debug/db")
async def debug_db():
    result: dict = {}
    try:
        result["database_url_prefix"] = os.environ.get("POSTGRES_URL", "")[:80]

        from db.connection import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), current_user, inet_server_addr()::text")
                row = cur.fetchone()
                result["current_database"] = row[0]
                result["current_user"] = row[1]
                result["inet_server_addr"] = row[2]

                cur.execute("SELECT count(*) FROM welcome_video_jobs")
                result["welcome_video_jobs_count"] = cur.fetchone()[0]

                cur.execute(
                    "SELECT id, order_id, status, created_at, attempts, last_error "
                    "FROM welcome_video_jobs ORDER BY created_at DESC LIMIT 5"
                )
                result["recent_jobs"] = [
                    {
                        "id": r[0],
                        "order_id": r[1],
                        "status": r[2],
                        "created_at": r[3].isoformat() if r[3] else None,
                        "attempts": r[4],
                        "last_error": r[5],
                    }
                    for r in cur.fetchall()
                ]
    except Exception as e:
        result["error"] = str(e)
    return result


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


@app.get("/api/hive-mind/issues")
def hive_mind_issues():
    """Sent Hive Mind issues, newest first.
    Consumed by lib.hm_gate JS to refresh the subscriber library dynamically.

    Only returns issues that are published OR whose scheduled_send_at has passed.
    Never returns future/unsent issues.
    """
    from db.connection import get_conn
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT number, subject_line, page_dek, shopify_page_url,
                          cover_image_url, pillar
                   FROM issues
                   WHERE (status = 'published'
                       OR (status = 'scheduled' AND scheduled_send_at IS NOT NULL AND scheduled_send_at <= NOW()))
                     AND shopify_page_url IS NOT NULL
                   ORDER BY number DESC"""
            ).fetchall()
        return [
            {
                "number": r[0],
                "title":  r[1] or "",
                "dek":    (r[2] or "")[:160],
                "url":    r[3] or "#",
                "img":    r[4] or "",
                "pillar": r[5] or "",
            }
            for r in rows
        ]
    except Exception as exc:
        return []


@app.get("/debug/pacing")
def debug_pacing():
    """Diagnose pacing data in the deployed container."""
    import json, os
    result = {"database_url_set": bool(os.environ.get("POSTGRES_URL")),
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
    return RedirectResponse(url="/dashboard/", status_code=302)


_DASH_OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "dashboard", "out")
if os.path.isdir(_DASH_OUT):
    from fastapi.staticfiles import StaticFiles
    app.mount("/dashboard", StaticFiles(directory=_DASH_OUT, html=True),
              name="dashboard")
else:
    print(f"[main] dashboard static export not found at {_DASH_OUT} "
          f"— /dashboard will 404 until `npm run build` is run in dashboard/")


@app.post("/api/approve-week")
async def approve_week():
    """Write approval for the current week."""
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
    """Mark this month's calendar as approved."""
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
    """Manually trigger pacing cache refresh."""
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
    """Pull Klaviyo campaign history → compute RPR per audience → cache."""
    try:
        loop = asyncio.get_running_loop()
        from app.dashboard import pull_klaviyo_audience_health
        result = await loop.run_in_executor(None, pull_klaviyo_audience_health)
        return JSONResponse({"status": "ok", "audiences": len(result)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/run-flow-check")
async def run_flow_check():
    """Run the weekly flow health check now."""
    try:
        loop = asyncio.get_running_loop()
        from workers.flow_monitor import run_flow_check as _flow_check
        result = await loop.run_in_executor(None, _flow_check)
        return JSONResponse({"status": "ok", "result": str(result)[:200]})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/run-deliverability-check")
async def run_deliverability_check_endpoint():
    """Run deliverability check now."""
    try:
        loop = asyncio.get_running_loop()
        from workers.deliverability_monitor import run_deliverability_check
        result = await loop.run_in_executor(None, run_deliverability_check)
        return JSONResponse({"status": "ok", "result": result})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/retry-slot")
async def retry_slot(id: str):
    """Re-queue a failed calendar_executions row."""
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
        return RedirectResponse(url="/dashboard/", status_code=303)
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
    """Handles Slack interactive callbacks (button clicks)."""
    import json
    import urllib.parse

    raw_body = await request.body()
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
    """Synchronous handler for Slack interactive actions."""
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

@app.get("/api/data/overview")
def api_data_overview():
    from app.dashboard import _pacing, _today_slots, _next_send_date, _approval_status
    p = _pacing()
    slots = _today_slots()
    apv = _approval_status()
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
    from app.dashboard import _upcoming_slots, _approval_status
    apv = _approval_status()
    if apv.get("week_start"):
        apv["week_start"] = str(apv["week_start"])
    return JSONResponse({"slots": _upcoming_slots(), "approval": apv})


@app.get("/api/data/audiences")
def api_data_audiences():
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
    from app.dashboard import _top_performers, _learning_loop
    import json as _json
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
    from app.dashboard import _flow_health
    data = _flow_health() or {}
    return JSONResponse(data)


@app.get("/api/data/content")
def api_data_content():
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
                "SLACK_BOT_TOKEN", "POSTGRES_URL", "HIGGSFIELD_KEY"]
    env_status = {k: bool(_os.environ.get(k)) for k in env_keys}

    return JSONResponse({
        "cron_sentinels": cron_sentinels,
        "recent_runs": recent_runs,
        "env_status": env_status,
        "db_ok": "error" not in cron_sentinels,
    })


@app.post("/api/burn-audience")
async def api_burn_audience(audience: str):
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
    """Send a message to #beezy-agents as if typed by Alan."""
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


# ── Extended read endpoints ───────────────────────────────────────────────────

@app.get("/api/data/business")
def api_data_business():
    from app.dashboard import _store_revenue, _pacing
    return JSONResponse({"store": _store_revenue(), "pacing": _pacing()})


@app.get("/api/data/pacing-history")
def api_data_pacing_history():
    from app.dashboard import _pacing_history
    return JSONResponse({"history": _pacing_history()})


@app.get("/api/data/deliverability")
def api_data_deliverability():
    from app.dashboard import _deliverability
    return JSONResponse(_deliverability())


# ── Inline editing — calendar slots ───────────────────────────────────────────

_SLOT_EDITABLE = {
    "date", "content_type", "audience", "topic_angle", "send_time_est",
    "priority", "revenue_estimate", "needs_page", "discount_code",
    "discount_pct", "rationale",
}


def _load_plan(conn, month: str):
    row = conn.execute(
        "SELECT id, output FROM decisions WHERE decision_type='calendar_plan' "
        "AND output->>'month'=%s ORDER BY created_at DESC LIMIT 1",
        (month,),
    ).fetchone()
    if not row:
        return None, None
    payload = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    return str(row[0]), payload


def _save_plan(conn, decision_id: str, payload: dict) -> None:
    conn.execute(
        "UPDATE decisions SET output=%s WHERE id=%s",
        (json.dumps(payload), decision_id),
    )
    conn.commit()


@app.post("/api/calendar/slot")
async def api_calendar_slot_add(request: Request):
    body = await request.json()
    slot = {k: v for k, v in body.items() if k in _SLOT_EDITABLE}
    if not slot.get("date") or not slot.get("content_type") or not slot.get("audience"):
        return JSONResponse({"error": "date, content_type and audience are required"}, status_code=400)
    month = slot["date"][:7]
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            decision_id, payload = _load_plan(conn, month)
            if not payload:
                return JSONResponse({"error": f"no calendar plan exists for {month}"}, status_code=404)
            slot.setdefault("revenue_estimate", 0)
            payload.setdefault("slots", []).append(slot)
            _save_plan(conn, decision_id, payload)
        return JSONResponse({"status": "added", "slot": slot})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.patch("/api/calendar/slot")
async def api_calendar_slot_edit(request: Request):
    body = await request.json()
    loc = body.get("locator") or {}
    fields = {k: v for k, v in (body.get("fields") or {}).items() if k in _SLOT_EDITABLE}
    if not loc.get("date") or not loc.get("content_type") or not loc.get("audience"):
        return JSONResponse({"error": "locator requires date, content_type, audience"}, status_code=400)
    if not fields:
        return JSONResponse({"error": "no editable fields supplied"}, status_code=400)
    month = loc["date"][:7]
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            decision_id, payload = _load_plan(conn, month)
            if not payload:
                return JSONResponse({"error": f"no calendar plan for {month}"}, status_code=404)
            matched = False
            for s in payload.get("slots", []):
                if (s.get("date") == loc["date"]
                        and s.get("content_type") == loc["content_type"]
                        and s.get("audience") == loc["audience"]):
                    s.update(fields)
                    matched = True
                    break
            if not matched:
                return JSONResponse({"error": "slot not found"}, status_code=404)
            _save_plan(conn, decision_id, payload)
        return JSONResponse({"status": "updated", "fields": fields})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/calendar/slot")
async def api_calendar_slot_delete(request: Request):
    body = await request.json()
    d, ct, aud = body.get("date"), body.get("content_type"), body.get("audience")
    if not d or not ct or not aud:
        return JSONResponse({"error": "date, content_type, audience required"}, status_code=400)
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            decision_id, payload = _load_plan(conn, d[:7])
            if not payload:
                return JSONResponse({"error": "no calendar plan"}, status_code=404)
            before = len(payload.get("slots", []))
            payload["slots"] = [
                s for s in payload.get("slots", [])
                if not (s.get("date") == d and s.get("content_type") == ct
                        and s.get("audience") == aud)
            ]
            if len(payload["slots"]) == before:
                return JSONResponse({"error": "slot not found"}, status_code=404)
            _save_plan(conn, decision_id, payload)
        return JSONResponse({"status": "deleted"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Inline editing — Hive Mind issues ─────────────────────────────────────────

_ISSUE_EDITABLE = {
    "subject_line", "subject_line_48h", "preview_text", "pillar",
    "topic_summary", "scheduled_send_at", "status", "notes",
}


@app.patch("/api/content/issue")
async def api_issue_edit(request: Request):
    body = await request.json()
    number = body.get("number")
    fields = {k: v for k, v in (body.get("fields") or {}).items() if k in _ISSUE_EDITABLE}
    if number is None or not fields:
        return JSONResponse({"error": "number and at least one editable field required"}, status_code=400)
    sets = ", ".join(f"{k}=%s" for k in fields)
    params = list(fields.values()) + [number]
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            conn.execute(f"UPDATE issues SET {sets} WHERE number=%s", params)
            conn.commit()
        return JSONResponse({"status": "updated", "number": number, "fields": fields})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Inline editing — SEO topic queue ──────────────────────────────────────────

@app.post("/api/content/seo-topic")
async def api_seo_topic_add(request: Request):
    body = await request.json()
    keyword = (body.get("keyword") or "").strip()
    if not keyword:
        return JSONResponse({"error": "keyword required"}, status_code=400)
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO seo_topics (keyword, status, created_at) VALUES (%s, 'pending', NOW())",
                (keyword,),
            )
            conn.commit()
        return JSONResponse({"status": "queued", "keyword": keyword})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.patch("/api/content/seo-topic")
async def api_seo_topic_edit(request: Request):
    body = await request.json()
    keyword = (body.get("keyword") or "").strip()
    status = body.get("status")
    if not keyword or status not in ("pending", "published", "error"):
        return JSONResponse({"error": "keyword and valid status required"}, status_code=400)
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            conn.execute("UPDATE seo_topics SET status=%s WHERE keyword=%s", (status, keyword))
            conn.commit()
        return JSONResponse({"status": "updated", "keyword": keyword, "new_status": status})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/content/seo-topic")
async def api_seo_topic_delete(request: Request):
    body = await request.json()
    keyword = (body.get("keyword") or "").strip()
    if not keyword:
        return JSONResponse({"error": "keyword required"}, status_code=400)
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            conn.execute("DELETE FROM seo_topics WHERE keyword=%s", (keyword,))
            conn.commit()
        return JSONResponse({"status": "deleted", "keyword": keyword})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/railway-logs")
async def api_railway_logs(since: int = 3600, filter_after: str = "", limit: int = 500):
    """Pull Railway deployment logs for sleep-audio-platform."""
    import httpx as _httpx
    from datetime import datetime, timezone, timedelta

    token      = os.environ.get("RAILWAY_TOKEN", "")
    project_id = os.environ.get("RAILWAY_PROJECT_ID", "")
    service_id = os.environ.get("RAILWAY_SERVICE_ID", "")
    gql_url    = "https://backboard.railway.app/graphql/v2"

    if not token:
        return JSONResponse({"error": "RAILWAY_TOKEN not set"}, status_code=500)
    if not project_id or not service_id:
        return JSONResponse({"error": "RAILWAY_PROJECT_ID or RAILWAY_SERVICE_ID not set"}, status_code=500)

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _gql(query: str, variables: dict | None = None) -> dict:
        r = _httpx.post(gql_url, headers=headers,
                        json={"query": query, "variables": variables or {}}, timeout=15)
        r.raise_for_status()
        data = r.json()
        if "errors" in data:
            raise RuntimeError(str([e.get("message") for e in data["errors"]]))
        return data["data"]

    try:
        dep_data = _gql("""
            query($projectId: String!, $serviceId: String!) {
              deployments(input: {projectId: $projectId, serviceId: $serviceId} first: 1) {
                edges { node { id status createdAt } }
              }
            }
        """, {"projectId": project_id, "serviceId": service_id})
        edges = dep_data["deployments"]["edges"]
        if not edges:
            return JSONResponse({"error": "no deployments found"}, status_code=404)
        dep = edges[0]["node"]
        deployment_id = dep["id"]

        log_data = _gql("""
            query($deploymentId: String!) {
              deploymentLogs(deploymentId: $deploymentId, limit: """ + str(limit) + """) {
                timestamp severity message
              }
            }
        """, {"deploymentId": deployment_id})

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=since)
        raw    = log_data.get("deploymentLogs", [])

        lines = []
        for entry in raw:
            ts_raw = entry.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                ts = datetime.now(timezone.utc)
            if ts < cutoff:
                continue
            lines.append({
                "ts":  ts.strftime("%H:%M:%S"),
                "sev": (entry.get("severity") or "")[:4].upper(),
                "msg": entry.get("message", ""),
            })

        if filter_after:
            trigger_idx = next(
                (i for i, l in enumerate(lines) if filter_after.lower() in l["msg"].lower()),
                None,
            )
            if trigger_idx is not None:
                lines = lines[trigger_idx:]
            else:
                lines = []

        return JSONResponse({
            "deployment_id": deployment_id,
            "deployment_status": dep["status"],
            "since_seconds": since,
            "filter_after": filter_after or None,
            "line_count": len(lines),
            "lines": lines,
        })

    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Approval endpoints ────────────────────────────────────────────────────────

@app.post("/api/refresh-pacing")
async def refresh_pacing_2():
    """Alias — handled above."""
    pass


@app.post("/api/retry-slot")
async def retry_slot_2(id: str):
    """Alias — handled above."""
    pass