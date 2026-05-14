"""
Beezy Agents — Phase 2 installer (auto-schedule + revenue backfill + learning loop)
Run from ~/workspace: python3 install_phase2.py

Installs:
1. workers/auto_schedule.py — schedules campaigns in Klaviyo automatically
2. workers/revenue_backfill.py — pulls actual revenue after 72h
3. workers/learning_loop.py — weekly/biweekly/monthly performance reviews
4. Wires auto_schedule into beezy_campaign.py pipeline
5. Adds cron jobs to app/main.py
"""
import shutil
import os

def patch(filepath, old, new, label):
    with open(filepath, 'r') as f:
        content = f.read()
    if old not in content:
        print(f"  SKIP {label} — pattern not found in {filepath}")
        return False
    content = content.replace(old, new, 1)
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"  ✓ {label}")
    return True

print("=" * 60)
print("Phase 2: Auto-schedule + Revenue Backfill + Learning Loop")
print("=" * 60)

# ── Step 1: Copy modules ──────────────────────────────────────────────────────
print("\n[1] Installing modules...")
for src_name, dst_path in [
    ("auto_schedule.py", "workers/auto_schedule.py"),
    ("revenue_backfill.py", "workers/revenue_backfill.py"),
    ("learning_loop.py", "workers/learning_loop.py"),
]:
    if os.path.exists(src_name):
        shutil.copy2(src_name, dst_path)
        print(f"  ✓ {src_name} → {dst_path}")
    else:
        print(f"  ✗ {src_name} not found!")

# ── Step 2: Wire auto_schedule into beezy_campaign.py ─────────────────────────
print("\n[2] Wiring auto-schedule into campaign pipeline...")

# Add import
patch(
    "workers/beezy_campaign.py",
    "from workers.validator import validate_campaign",
    "from workers.validator import validate_campaign\nfrom workers.auto_schedule import schedule_campaign",
    "auto_schedule import"
)

# Add scheduling step after Klaviyo campaign creation + template assignment
patch(
    "workers/beezy_campaign.py",
    '''    # Slack notify
    _slack_notify(slot, copy, campaign_id, page_url, cdn_url)

    camp_url = "https://www.klaviyo.com/campaign/" + campaign_id + "/wizard"
    print("[beezy_campaign]   Done: " + camp_url)''',
    '''    # Auto-schedule — campaign goes from Draft → Scheduled
    print("[beezy_campaign] Auto-scheduling...")
    sched_result = schedule_campaign(campaign_id, slot)
    if sched_result["scheduled"]:
        sched_note = "✅ Scheduled for " + sched_result["send_time"]
    else:
        sched_note = "⚠️ Draft only — " + sched_result.get("error", "unknown")
    print("[beezy_campaign]   " + sched_note)

    # Slack notify (include schedule status)
    _slack_notify(slot, copy, campaign_id, page_url, cdn_url)

    camp_url = "https://www.klaviyo.com/campaign/" + campaign_id + "/wizard"
    print("[beezy_campaign]   Done: " + camp_url + " | " + sched_note)''',
    "auto-schedule step added after Klaviyo creation"
)

# ── Step 3: Add cron jobs to app/main.py ───────────────────────────────────────
print("\n[3] Adding cron jobs to app/main.py...")

# Find the cron loop and add new jobs
CRON_PATCH_OLD = '''    if h == 8 and m == 0:
        try:
            from pacing.orchestrator import run_daily
            print("[cron] orchestrator")
            run_daily()
        except Exception as e:
            print(f"[cron] orchestrator error: {e}")'''

CRON_PATCH_NEW = '''    if h == 8 and m == 0:
        try:
            from pacing.orchestrator import run_daily
            print("[cron] orchestrator")
            run_daily()
        except Exception as e:
            print(f"[cron] orchestrator error: {e}")

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
            print(f"[cron] monthly retro error: {e}")'''

patch("app/main.py", CRON_PATCH_OLD, CRON_PATCH_NEW, "cron jobs: backfill + weekly + biweekly + monthly")

# ── Step 4: Add Slack agent commands for manual triggers ───────────────────────
print("\n[4] Adding learning loop commands to Slack agent...")

patch(
    "agents/slack_agent.py",
    '                "restore calendar": {"action": "restore_calendar"},',
    '''                "restore calendar": {"action": "restore_calendar"},
                "run backfill": {"action": "run_backfill"},
                "weekly review": {"action": "weekly_review"},
                "pacing check": {"action": "pacing_check"},
                "monthly review": {"action": "monthly_review"},''',
    "learning loop commands added to fast_match"
)

# Add handlers for the new commands
patch(
    "agents/slack_agent.py",
    '    "view_calendar":     _handle_view_calendar,',
    '''    "run_backfill":      lambda conn, _: _handle_backfill(),
    "weekly_review":     lambda conn, _: _handle_weekly_review(),
    "pacing_check":      lambda conn, _: _handle_pacing_check(),
    "monthly_review":    lambda conn, _: _handle_monthly_review(),
    "view_calendar":     _handle_view_calendar,''',
    "learning loop handlers added to HANDLERS"
)

# Add the handler functions — inject before run_once
HANDLER_FUNCS = '''

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


'''

patch(
    "agents/slack_agent.py",
    "def run_once() -> None:",
    HANDLER_FUNCS + "def run_once() -> None:",
    "learning loop handler functions"
)

# Update HELP_TEXT
patch(
    "agents/slack_agent.py",
    '`help` — show this list"""',
    '''`help` — show this list

*Performance:*
`run backfill` — pull revenue for campaigns sent 3+ days ago
`weekly review` — run the weekly performance review now
`pacing check` — mid-month pacing check
`monthly review` — run the monthly retrospective"""''',
    "help text updated with learning commands"
)

print("\n" + "=" * 60)
print("Phase 2 installed. Verify:")
print('  python3 -c "from workers.auto_schedule import schedule_campaign; print(\'OK\')"')
print('  python3 -c "from workers.revenue_backfill import run_backfill; print(\'OK\')"')
print('  python3 -c "from workers.learning_loop import run_weekly, run_biweekly, run_monthly; print(\'OK\')"')
print('  grep -n "schedule_campaign\\|run_backfill\\|learning_loop" app/main.py | head -8')
print('  grep -n "backfill\\|weekly_review\\|pacing_check" agents/slack_agent.py | head -8')
print("=" * 60)
print()
print("NEW CRON SCHEDULE:")
print("  Daily  9:00am  — Revenue backfill (72h-old campaigns)")
print("  Sunday 9:00pm  — Weekly learning review")
print("  15th   9:30am  — Bi-weekly pacing check")
print("  1st    9:30am  — Monthly retrospective")
print()
print("NEW SLACK COMMANDS:")
print("  'run backfill'    — manual trigger")
print("  'weekly review'   — manual trigger")
print("  'pacing check'    — manual trigger")
print("  'monthly review'  — manual trigger")
