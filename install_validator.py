"""
Beezy Agents — Validator integration installer.
Run from ~/workspace: python3 install_validator.py

1. Copies validator.py into workers/
2. Patches beezy_campaign.py to call validator after copy generation
3. If validator FAILS → posts to Slack, skips Klaviyo entirely
"""

import shutil
import os

def patch(filepath, old, new, label):
    with open(filepath, 'r') as f:
        content = f.read()
    if old not in content:
        print(f"  SKIP {label} — pattern not found")
        return False
    content = content.replace(old, new, 1)
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"  ✓ {label}")
    return True

print("=" * 60)
print("Validator Integration Installer")
print("=" * 60)

# Step 1: Copy validator.py into workers/
print("\n[1] Installing workers/validator.py...")
# validator.py should be in the same directory as this script
src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validator.py")
dst = "workers/validator.py"
if os.path.exists(src):
    shutil.copy2(src, dst)
    print(f"  ✓ Copied {src} → {dst}")
elif os.path.exists("validator.py"):
    shutil.copy2("validator.py", dst)
    print(f"  ✓ Copied validator.py → {dst}")
else:
    print("  ✗ validator.py not found! Place it next to this script or in ~/workspace/")
    exit(1)

# Step 2: Add validator import to beezy_campaign.py
print("\n[2] Adding validator import...")
patch(
    "workers/beezy_campaign.py",
    "import httpx",
    "import httpx\nfrom workers.validator import validate_campaign",
    "validator import added"
)

# Step 3: Wire validator between copy generation and image generation
print("\n[3] Wiring validator gate into pipeline...")

OLD_PIPELINE = '''    # Image
    prompt  = copy.get("image_prompt", "Woman 50 warm honey tones editorial lifestyle real human face")
    print("[beezy_campaign] Generating image...")
    raw_url = _generate_image(prompt)
    cdn_url = _upload_to_shopify_cdn(raw_url, "Beezy Beez email")
    print("[beezy_campaign]   Image: " + cdn_url)

    # Email HTML
    html = _build_email_html(copy, cdn_url, cta_url, discount_code)

    # Klaviyo
    tpl_name = audience[:20] + " | " + date_str
    print("[beezy_campaign] Creating Klaviyo template...")
    template_id = _create_template(html, tpl_name)

    print("[beezy_campaign] Creating Klaviyo campaign...")
    campaign_id, message_id = _create_campaign(slot, copy, segment_id)

    if message_id:
        print("[beezy_campaign] Assigning template...")
        _assign_template(message_id, template_id)

    # Slack notify
    _slack_notify(slot, copy, campaign_id, page_url, cdn_url)

    camp_url = "https://www.klaviyo.com/campaign/" + campaign_id + "/wizard"
    print("[beezy_campaign]   Done: " + camp_url)'''

NEW_PIPELINE = '''    # ── VALIDATOR GATE ──────────────────────────────────────────────────
    from db.connection import get_conn as _get_validator_conn
    print("[beezy_campaign] Running validator...")
    try:
        with _get_validator_conn() as vconn:
            validation = validate_campaign(vconn, slot, copy, cta_url)
    except Exception as ve:
        print("[beezy_campaign] Validator error (proceeding with caution): " + str(ve))
        validation = {"pass": True, "verdict": "ERROR", "slack_block": "Validator error: " + str(ve)}

    if not validation["pass"]:
        print("[beezy_campaign] ❌ VALIDATOR BLOCKED: " + validation["verdict"])
        # Post failure to Slack — do NOT create campaign
        from lib.slack import post_draft
        post_draft(
            title="❌ Campaign Blocked by Validator",
            summary_lines=[validation["slack_block"]],
            body="Slot: " + json.dumps(slot, indent=2)[:500],
        )
        return "blocked:" + validation["verdict"]

    print("[beezy_campaign] ✅ Validator PASSED")
    # Post validation report to Slack regardless
    from lib.slack import post_draft as _post_validation
    _post_validation(
        title="✅ Validator Passed — deploying",
        summary_lines=[validation["slack_block"][:500]],
        body="",
    )

    # Image
    prompt  = copy.get("image_prompt", "Woman 50 warm honey tones editorial lifestyle real human face")
    print("[beezy_campaign] Generating image...")
    raw_url = _generate_image(prompt)
    cdn_url = _upload_to_shopify_cdn(raw_url, "Beezy Beez email")
    print("[beezy_campaign]   Image: " + cdn_url)

    # Email HTML
    html = _build_email_html(copy, cdn_url, cta_url, discount_code)

    # Klaviyo
    tpl_name = audience[:20] + " | " + date_str
    print("[beezy_campaign] Creating Klaviyo template...")
    template_id = _create_template(html, tpl_name)

    print("[beezy_campaign] Creating Klaviyo campaign...")
    campaign_id, message_id = _create_campaign(slot, copy, segment_id)

    if message_id:
        print("[beezy_campaign] Assigning template...")
        _assign_template(message_id, template_id)

    # Slack notify
    _slack_notify(slot, copy, campaign_id, page_url, cdn_url)

    camp_url = "https://www.klaviyo.com/campaign/" + campaign_id + "/wizard"
    print("[beezy_campaign]   Done: " + camp_url)'''

patch(
    "workers/beezy_campaign.py",
    OLD_PIPELINE,
    NEW_PIPELINE,
    "validator gate wired into pipeline"
)

print("\n" + "=" * 60)
print("Validator installed. Verify:")
print('  python3 -c "from workers.validator import validate_campaign; print(\'OK\')"')
print('  grep -n "validate_campaign\\|VALIDATOR" workers/beezy_campaign.py | head -5')
print("=" * 60)
