"""
Beezy Agents — Flow Monitor installer
Run from ~/workspace: python3 install_flow_monitor.py
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
print("Flow Performance Monitor Installer")
print("=" * 60)

# Step 1: Copy module
print("\n[1] Installing workers/flow_monitor.py...")
if os.path.exists("flow_monitor.py"):
    shutil.copy2("flow_monitor.py", "workers/flow_monitor.py")
    print("  ✓ flow_monitor.py → workers/flow_monitor.py")
else:
    print("  ✗ flow_monitor.py not found!")
    exit(1)

# Step 2: Add cron job — Sunday 9:15pm (right after weekly learning review at 9:00pm)
print("\n[2] Adding flow monitor cron job...")
patch(
    "app/main.py",
    '''    # Weekly learning review — Sunday at 9pm ET
    if now.weekday() == 6 and h == 21 and m == 0:
        try:
            from workers.learning_loop import run_weekly
            print("[cron] weekly learning review")
            run_weekly()
        except Exception as e:
            print(f"[cron] weekly review error: {e}")''',
    '''    # Weekly learning review — Sunday at 9pm ET
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
            print(f"[cron] flow monitor error: {e}")''',
    "flow monitor cron job (Sunday 9:15pm)"
)

# Step 3: Add Slack command
print("\n[3] Adding Slack command...")
patch(
    "agents/slack_agent.py",
    '                "monthly review": {"action": "monthly_review"},',
    '                "monthly review": {"action": "monthly_review"},\n                "flow check": {"action": "flow_check"},\n                "flow health": {"action": "flow_check"},',
    "'flow check' command added"
)

patch(
    "agents/slack_agent.py",
    '    "run_backfill":      lambda conn, _: _handle_backfill(),',
    '    "flow_check":        lambda conn, _: _handle_flow_check(),\n    "run_backfill":      lambda conn, _: _handle_backfill(),',
    "flow_check handler added"
)

patch(
    "agents/slack_agent.py",
    'def _handle_backfill():',
    '''def _handle_flow_check():
    from workers.flow_monitor import run_flow_check
    result = run_flow_check()
    return "Flow health check posted above."

def _handle_backfill():''',
    "flow_check handler function"
)

# Step 4: Update help text
print("\n[4] Updating help text...")
patch(
    "agents/slack_agent.py",
    '`monthly review` — run the monthly retrospective"""',
    '`monthly review` — run the monthly retrospective\n`flow check` — run flow health check"""',
    "help text updated with flow check"
)

print("\n" + "=" * 60)
print("Flow monitor installed. Verify:")
print('  python3 -c "from workers.flow_monitor import run_flow_check; print(\'OK\')"')
print('  grep -n "flow_check\\|flow_monitor" app/main.py agents/slack_agent.py | head -8')
print("=" * 60)
print()
print("CRON: Sunday 9:15pm ET — flow health check")
print("SLACK: 'flow check' or 'flow health' — manual trigger")
print()
print("Sunday night Slack sequence:")
print("  9:00pm — Weekly campaign performance review")
print("  9:15pm — Flow health check")
print("  Operator reads both Monday morning, takes action if needed.")
