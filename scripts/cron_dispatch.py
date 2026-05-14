import sys
from datetime import datetime
from zoneinfo import ZoneInfo

NY  = ZoneInfo("America/New_York")
now = datetime.now(NY)
h, m = now.hour, now.minute
print("[dispatch] " + now.isoformat() + "  h=" + str(h) + " m=" + str(m))

# Every 4 hours: ingest
if h % 4 == 0 and m < 30:
    print("[dispatch] ingestion.sync_all")
    try:
        from ingestion.sync import sync_all
        sync_all()
    except Exception as e:
        from lib.slack import notify_failure
        notify_failure(source="cron/ingestion", error=str(e))

# 7:30 AM ET: pacing brain
if h == 7 and 30 <= m < 60:
    print("[dispatch] pacing.brain daily")
    try:
        from pacing.cron import run_daily as pacing_daily
        pacing_daily()
    except Exception as e:
        from lib.slack import notify_failure
        notify_failure(source="cron/pacing", error=str(e))

# 8:00 AM ET: orchestrator (runs only if week is approved)
if h == 8 and m < 30:
    print("[dispatch] orchestrator daily")
    try:
        from pacing.orchestrator import run_daily as orch_daily
        orch_daily()
    except Exception as e:
        from lib.slack import notify_failure
        notify_failure(source="cron/orchestrator", error=str(e))

# 10:00 AM ET: Hive Mind campaign auto-create
if h == 10 and m < 30:
    print("[dispatch] hive_mind campaign auto-create")
    try:
        from workers.klaviyo_campaign import auto_create_pending
        auto_create_pending()
    except Exception as e:
        from lib.slack import notify_failure
        notify_failure(source="cron/klaviyo_campaign", error=str(e))

# Sunday 9:00 PM ET: weekly 7-day lookahead brief
if now.weekday() == 6 and h == 21 and m < 30:
    print("[dispatch] weekly brief")
    try:
        from pacing.weekly_brief import run_weekly_brief
        run_weekly_brief()
    except Exception as e:
        from lib.slack import notify_failure
        notify_failure(source="cron/weekly_brief", error=str(e))

# 1st of month 9:00 AM ET: calendar generation
if now.day == 1 and h == 9 and m < 30:
    print("[dispatch] calendar.run_monthly")
    try:
        from pacing.calendar import run_monthly
        run_monthly()
    except Exception as e:
        from lib.slack import notify_failure
        notify_failure(source="cron/calendar", error=str(e))

print("[dispatch] done")
