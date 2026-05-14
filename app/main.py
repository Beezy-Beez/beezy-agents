"""
Beezy Agents — unified web server.
Slack agent runs every 5 seconds.
All cron jobs run on time-based schedule in background.
Single deployment handles everything.
"""
import sys
import os
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
from fastapi import FastAPI
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
    result = {"database_url_set": bool(os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_URL", "")),
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


async def healthz():
    return {"status": "ok", "agents": "running"}
