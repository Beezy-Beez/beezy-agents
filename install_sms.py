"""
Beezy Agents — SMS installer
Run from ~/workspace: python3 install_sms.py

Replaces the SMS stub handler in orchestrator.py with the full autonomous pipeline.
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
print("SMS Campaign Worker Installer")
print("=" * 60)

# Step 1: Copy module
print("\n[1] Installing workers/sms_campaign.py...")
if os.path.exists("sms_campaign.py"):
    shutil.copy2("sms_campaign.py", "workers/sms_campaign.py")
    print("  ✓ sms_campaign.py → workers/sms_campaign.py")
else:
    print("  ✗ sms_campaign.py not found!")
    exit(1)

# Step 2: Replace stub handler in orchestrator
print("\n[2] Replacing SMS stub handler in orchestrator...")

OLD_HANDLER = '''def _handle_sms(slot):
        title="SMS Brief -- " + slot["date"],
        body="*Angle:* " + slot.get("topic_angle","") + "\\n\\n*Rationale:* " + slot.get("rationale","") + "\\n\\n_Build and deploy via Klaviyo SMS. Max 2x/month._",'''

# The stub might have different formatting, let's try a more targeted approach
with open("pacing/orchestrator.py", 'r') as f:
    orc = f.read()

if "def _handle_sms(slot):" in orc:
    # Find the function and replace it
    start = orc.index("def _handle_sms(slot):")
    # Find the next function definition or the HANDLERS dict
    remaining = orc[start:]
    # Find end of function — next def or next line at same indentation
    lines = remaining.split("\n")
    end_offset = 0
    for i, line in enumerate(lines[1:], 1):
        # Function ends when we hit a non-indented line (not blank)
        if line and not line.startswith(" ") and not line.startswith("\t"):
            end_offset = sum(len(l) + 1 for l in lines[:i])
            break
    if end_offset == 0:
        end_offset = len(remaining)

    new_handler = '''def _handle_sms(slot):
    """Full autonomous SMS pipeline."""
    from workers.sms_campaign import run_sms_campaign
    return run_sms_campaign(slot)


'''
    orc = orc[:start] + new_handler + orc[start + end_offset:]
    with open("pacing/orchestrator.py", 'w') as f:
        f.write(orc)
    print("  ✓ SMS stub handler replaced with full pipeline")
else:
    print("  SKIP — _handle_sms not found in orchestrator.py")

print("\n" + "=" * 60)
print("SMS installed. Verify:")
print('  python3 -c "from workers.sms_campaign import run_sms_campaign; print(\'OK\')"')
print('  grep -n "_handle_sms\\|sms_campaign" pacing/orchestrator.py | head -5')
print("=" * 60)
print()
print("SMS campaigns will now auto-generate and schedule when")
print("the calendar has sms_campaign slots. Same light-switch flow:")
print("  Calendar → Orchestrator → SMS Worker → Klaviyo → Auto-schedule → Slack")
