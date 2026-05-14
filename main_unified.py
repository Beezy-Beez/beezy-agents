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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NY = ZoneInfo("America/New_York")
_last_cron_minute = -1


async def _slack_loop():
    """Polls Slack every 5 seconds."""
    while True:
        try:
            from agents.slack_agent import run_once
            run_once()
        except Exception as e:
            print(f"[slack_loop] {e}")
        await asyncio.sleep(5)


async def _cron_loop():
    """Runs time-gated jobs every 60 seconds (same logic as cron_dispatch.py)."""
    global _last_cron_minute
    while True:
        try:
            now = datetime.now(NY)
            h, m = now.hour, now.minute

            # Only run once per minute
            if m == _last_cron_minute:
                await asyncio.sleep(10)
                continue
            _last_cron_minute = m

            # Every 4h: ingestion
            if h % 4 == 0 and m < 2:
                try:
                    from ingestion.sync import run_shopify_sync, run_klaviyo_sync
                    print("[cron] ingestion sync")
                    run_shopify_sync()
                    run_klaviyo_sync()
                except Exception as e:
                    print(f"[cron] ingestion error: {e}")

            # 7:30am ET: pacing brain
            if h == 7 and m == 30:
                try:
                    from pacing.cron import run_daily as pacing_daily
                    print("[cron] pacing brain")
                    pacing_daily()
                except Exception as e:
                    print(f"[cron] pacing error: {e}")

            # 8:00am ET: orchestrator
            if h == 8 and m == 0:
                try:
                    from pacing.orchestrator import run_daily
                    print("[cron] orchestrator")
                    run_daily()
                except Exception as e:
                    print(f"[cron] orchestrator error: {e}")

            # 10:00am ET: Hive Mind auto-campaign
            if h == 10 and m == 0:
                try:
                    from workers.klaviyo_campaign import auto_create_pending
                    print("[cron] hive mind campaign")
                    auto_create_pending()
                except Exception as e:
                    print(f"[cron] hive mind error: {e}")

            # Sunday 9pm ET: weekly brief
            if now.weekday() == 6 and h == 21 and m == 0:
                try:
                    from pacing.weekly_brief import run_weekly_brief
                    print("[cron] weekly brief")
                    run_weekly_brief()
                except Exception as e:
                    print(f"[cron] weekly brief error: {e}")

            # 7 days before EOM at 9am: generate next month calendar
            import calendar as _cal
            last_day = _cal.monthrange(now.year, now.month)[1]
            trigger_day = last_day - 7
            if now.day == trigger_day and h == 9 and m == 0:
                try:
                    from pacing.calendar import run_monthly
                    from datetime import timedelta
                    next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
                    print("[cron] calendar generation")
                    run_monthly(month_start=next_month.date())
                except Exception as e:
                    print(f"[cron] calendar error: {e}")

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
    return {"status": "ok", "agents": "running"}
