"""
Beezy Agents — 6-fix installer
Paste into Replit shell: python3 beezy_agent_fixes.py

Fixes:
1. CTA: customer segments skip landing page, go to /pages/bf-collection
2. Collection URL: fallback from /products/honey-sub → /pages/bf-collection  
3. Subject line: {{ first_name }} in subject, {{ person.first_name|default:'there' }} in body
4. Image prompts: humans required, diverse 50+ women, different per campaign
5. Silent failures: add HANDLERS check + sniper_followup handler stub
6. Segment-aware offers: VIPs get educational, not discounts
"""
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
print("Beezy Agents — 6-fix installer")
print("=" * 60)

# ── Fix 2: Non-discount fallback URL ──────────────────────────────────
print("\n[Fix 2] Collection URL fallback...")
patch(
    "workers/beezy_campaign.py",
    '        cta_url = "https://trybeezybeez.com/products/honey-sub"',
    '        cta_url = "https://trybeezybeez.com/pages/bf-collection"',
    "fallback CTA → /pages/bf-collection"
)

# ── Fix 1 + 6: Customer segment detection + skip page + offer rules ──
print("\n[Fix 1+6] Customer segment CTA + offer awareness...")

# Add CUSTOMER_SEGMENTS constant after SEGMENT_IDS
patch(
    "workers/beezy_campaign.py",
    '''TRACKING_PARAMS = [''',
    '''# Segments that are CUSTOMERS — never send to landing pages, never need education
CUSTOMER_SEGMENTS = {
    "lapsed_30d", "lapsed_60d", "lapsed_60_90d", "lapsed_90d", "lapsed_90_180d",
    "lapsed_180d", "lapsed_180d_plus", "winback_180d",
    "vip", "inner_circle", "engaged_customers", "all_customers",
    "active_seal", "active_subscribers", "whales", "high_aov",
    "one_time_buyers", "otb", "cart_abandoners",
}

# Segments that should NEVER get flat discounts or BOGO — they buy without them
HIGH_VALUE_SEGMENTS = {"vip", "inner_circle", "whales", "high_aov", "active_seal", "active_subscribers"}

TRACKING_PARAMS = [''',
    "CUSTOMER_SEGMENTS + HIGH_VALUE_SEGMENTS constants"
)

# Override page creation for customer segments — inject after cta_url fallback
patch(
    "workers/beezy_campaign.py",
    '''    # Landing page (if needed)
    page_url = ""
    if _needs_page(slot):''',
    '''    # RULE: Customer segments NEVER go to a landing page — direct to collection/discount
    audience_key = slot.get("audience", "").lower().replace(" ", "_")
    is_customer = audience_key in CUSTOMER_SEGMENTS
    if is_customer and not discount_code:
        cta_url = "https://trybeezybeez.com/pages/bf-collection"

    # Landing page (if needed — but NEVER for customer segments)
    page_url = ""
    if _needs_page(slot) and not is_customer:''',
    "customer segments skip landing page"
)

# ── Fix 3 + 4 + 6: Replace COPY_SYSTEM with fixed version ────────────
print("\n[Fix 3+4+6] Copy system prompt — subject syntax, image rules, offer rules...")

OLD_COPY_SYSTEM = '''COPY_SYSTEM = """You are the creative director for Beezy Beez Honey (trybeezybeez.com).
DTC botanical extract honey. Target: women 50+. AOV ~$54.95.

Write at top-1% Health & Wellness DTC benchmarks:
- Subject: 6-9 words, curiosity-driven, personal. No clickbait.
- Preview: under 90 chars, extends the subject naturally.
- Body: 3 short paragraphs. Opens with a specific person or moment.
  Personalization: {{ person.first_name|default:'there' }}
  If page_url provided: naturally drive to reading more / listening.
  If discount_code provided: mention it naturally in the body.
- from_label: "Alan from Beezy Beez" for personal/lapsed,
              "Beezy Beez" for promotional.
- image_prompt: 12-word Higgsfield prompt. Woman 50+, warm honey tones,
  editorial lifestyle, real human face, no text.

Output ONLY valid JSON:
{
  "subject": "...",
  "preview_text": "under 90 chars",
  "from_label": "Alan from Beezy Beez",
  "body_paragraphs": ["para 1", "para 2", "para 3"],
  "cta_text": "READ MORE",
  "image_prompt": "12-word prompt"
}"""'''

NEW_COPY_SYSTEM = '''COPY_SYSTEM = """You are the creative director for Beezy Beez Honey (trybeezybeez.com).
DTC botanical extract honey. Target: women 50+. AOV ~$54.95.

Write at top-1% Health & Wellness DTC benchmarks:

SUBJECT LINE RULES:
- 6-9 words, curiosity-driven, personal. No clickbait.
- Personalization: {{ first_name }} — this is the ONLY format that works in Klaviyo subject lines.
- NEVER use {{ person.first_name|default:'there' }} in the subject — it renders as raw text.

PREVIEW TEXT:
- Under 90 chars, extends the subject naturally.

BODY RULES:
- 3 short paragraphs. Opens with a specific person or moment.
- Personalization in body: {{ person.first_name|default:'there' }} — this is the body format.
- If page_url provided: naturally drive to reading more / listening.
- If discount_code provided: mention it naturally in the body.
- CTA links directly to the collection or discount URL. NEVER to a separate landing page for customer audiences.

FROM LABEL:
- "Alan from Beezy Beez" for personal/JSH/lapsed/educational
- "Beezy Beez" for promotional/product

OFFER RULES BY AUDIENCE:
- VIP, inner_circle, whales, high_aov, active_seal: NEVER offer discounts, BOGO, or credits.
  Instead: insider knowledge, product recommendations, educational science, early access, personal check-ins.
- lapsed_30d: JSH check-ins from Alan, $25 credit occasionally. No deep discounts.
- lapsed_90d+, lapsed_180d+: deep discounts OK (35-40% off), reactivation offers.
- one_time_buyers: $25 credit, BOGO, product features.
- engaged_customers: product features, sleep stories, seasonal content.

IMAGE PROMPT RULES:
- 15-word Higgsfield prompt. MUST include a real human woman aged 50+.
- Warm amber/golden/honey tones (#8b4513 palette). Photorealistic, editorial lifestyle.
- Women depicted: diverse ethnicities, age-appropriate, never stock-photo generic.
- NEVER: "woman reading a book", sad/lonely scenes, no-people scenes, cold blue tones.
- VARY the scene each campaign: bedroom, kitchen, garden, patio, yoga, walking in nature, tea time.
- Include honey jar in roughly 40% of images.

Output ONLY valid JSON:
{
  "subject": "...",
  "preview_text": "under 90 chars",
  "from_label": "Alan from Beezy Beez",
  "body_paragraphs": ["para 1", "para 2", "para 3"],
  "cta_text": "SHOP NOW",
  "image_prompt": "15-word prompt with human woman 50+"
}"""'''

patch(
    "workers/beezy_campaign.py",
    OLD_COPY_SYSTEM,
    NEW_COPY_SYSTEM,
    "COPY_SYSTEM rewritten with subject/body/image/offer rules"
)

# ── Fix 3 (part 2): Inject audience context into copy generation ──────
print("\n[Fix 3b] Inject audience type into copy generation context...")
patch(
    "workers/beezy_campaign.py",
    '''    context = (
        "Campaign type: " + slot.get("content_type", "") + "\\n"
        "Audience: " + slot.get("audience", "?") + "\\n"
        "Topic: " + slot.get("topic_angle", "") + "\\n"
        "Send time: " + slot.get("send_time_est", "14:00") + " EST"
    )''',
    '''    aud_key = slot.get("audience", "?").lower().replace(" ", "_")
    aud_type = "HIGH_VALUE_CUSTOMER" if aud_key in HIGH_VALUE_SEGMENTS else (
               "CUSTOMER" if aud_key in CUSTOMER_SEGMENTS else "PROSPECT")
    context = (
        "Campaign type: " + slot.get("content_type", "") + "\\n"
        "Audience: " + slot.get("audience", "?") + "\\n"
        "Audience type: " + aud_type + " — follow the OFFER RULES for this type.\\n"
        "Topic: " + slot.get("topic_angle", "") + "\\n"
        "Send time: " + slot.get("send_time_est", "14:00") + " EST"
    )''',
    "audience type injected into copy context"
)

# ── Fix 5: Add sniper_followup handler + catch missing handlers ───────
print("\n[Fix 5] Check orchestrator HANDLERS...")
# Read orchestrator to find HANDLERS dict
orc_path = "pacing/orchestrator.py"
with open(orc_path, 'r') as f:
    orc = f.read()

if "sniper_followup" not in orc:
    # Find HANDLERS dict and add sniper_followup
    if "HANDLERS" in orc and "beezy_campaign" in orc:
        patch(
            orc_path,
            '    "seo_blog":',
            '    "sniper_followup":   lambda s: _run_beezy_campaign(s),\n    "seo_blog":',
            "sniper_followup handler added to orchestrator"
        )
    else:
        print("  SKIP sniper_followup — HANDLERS dict not found in expected format")
else:
    print("  SKIP sniper_followup — already present")

# ── Fix 7 (bonus): "approve today" alias in slack_agent ───────────────
print("\n[Bonus] 'approve today' alias in slack_agent...")
patch(
    "agents/slack_agent.py",
    '                "approved today": {"action": "approve_day", "params": {"day": "today"}},',
    '                "approved today": {"action": "approve_day", "params": {"day": "today"}},\n                "approve today": {"action": "approve_day", "params": {"day": "today"}},',
    "'approve today' alias added"
)

# ── Fix 8 (bonus): Remove --reload from production ────────────────────
print("\n[Bonus] Check for --reload in .replit or run command...")
for cfg_file in [".replit", "replit.nix", "pyproject.toml"]:
    if os.path.exists(cfg_file):
        with open(cfg_file, 'r') as f:
            content = f.read()
        if "--reload" in content:
            print(f"  ⚠ Found --reload in {cfg_file} — remove for production stability")
        else:
            print(f"  OK {cfg_file} — no --reload found")

print("\n" + "=" * 60)
print("All fixes applied. Verify with:")
print('  grep -n "bf-collection\\|CUSTOMER_SEGMENTS\\|HIGH_VALUE" workers/beezy_campaign.py | head -10')
print('  grep -n "first_name.*ONLY\\|OFFER RULES\\|IMAGE PROMPT RULES" workers/beezy_campaign.py | head -5')
print('  grep -n "approve today" agents/slack_agent.py')
print("=" * 60)
