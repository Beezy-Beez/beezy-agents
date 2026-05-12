"""Cron dispatcher for Replit Scheduled Deployment."""
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
now = datetime.now(NY)
print(f"[dispatch] {now.isoformat()} hour={now.hour} minute={now.minute}")

if now.hour % 4 == 0 and now.minute < 30:
    print("[dispatch] running ingestion.sync all")
    r = subprocess.run([sys.executable, "-m", "ingestion.sync", "all"], check=False)
    print(f"[dispatch] ingestion exit={r.returncode}")

if now.hour == 7 and now.minute >= 30:
    print("[dispatch] running pacing.cron daily")
    r = subprocess.run([sys.executable, "-m", "pacing.cron", "daily"], check=False)
    print(f"[dispatch] pacing exit={r.returncode}")

print("[dispatch] done")
