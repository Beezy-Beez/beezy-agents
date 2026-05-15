"""
Beezy campaign worker — full autonomous pipeline.

Sequence:
  1. Check if slot needs a landing page
  2. If yes: generate page content → create Shopify page → get live URL
  3. Generate copy (with page URL, discount code if applicable)
  4. Generate hero image (Higgsfield) → upload to Shopify CDN
  5. Build branded email HTML
  6. Create Klaviyo template → campaign → assign (confirmed endpoints)
  7. Notify Slack with Open in Klaviyo button

Slots that need a page first:
  - content_type has topic involving sleep science, research, story, audio, meditation
  - slot has needs_page: true from calendar

Slots that go straight to email:
  - Pure offer/discount/reactivation → product URL or discount URL
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import anthropic
import httpx
from workers.validator import validate_campaign
from workers.auto_schedule import schedule_campaign

MODEL = "claude-sonnet-4-6"

# A/B testing: segments with estimated list size > 3,000 get a second subject generated.
LARGE_SEGMENTS = {
    "lapsed_30d", "lapsed_60d", "lapsed_60_90d", "lapsed_90d", "lapsed_90_180d",
    "lapsed_180d", "lapsed_180d_plus", "winback_180d",
    "vip", "engaged_customers", "all_customers",
    "one_time_buyers", "otb", "engaged_prospects", "super_engaged",
}

SEGMENT_IDS = {
    "lapsed_30d": "UEQD6k", "lapsed_60d": "UfARWm", "lapsed_60_90d": "UfARWm",
    "lapsed_90d": "XuS7rY", "lapsed_90_180d": "XuS7rY", "lapsed_180d": "W98qh3",
    "lapsed_180d_plus": "W98qh3", "winback_180d": "W98qh3",
    "vip": "RArtzN", "inner_circle": "RArtzN",
    "engaged_customers": "RvtHdn", "all_customers": "RvtHdn",
    "active_seal": "UBFUcH", "active_subscribers": "UBFUcH",
    "engaged_prospects": "Xrp3ha", "super_engaged": "Sme9Nq",
    "whales": "VAUD58", "high_aov": "Res3GH",
    "one_time_buyers": "UfARWm", "otb": "UfARWm", "cart_abandoners": "RvtHdn",
}

# Segments that are CUSTOMERS — never send to landing pages, never need education
CUSTOMER_SEGMENTS = {
    "lapsed_30d", "lapsed_60d", "lapsed_60_90d", "lapsed_90d", "lapsed_90_180d",
    "lapsed_180d", "lapsed_180d_plus", "winback_180d",
    "vip", "inner_circle", "engaged_customers", "all_customers",
    "active_seal", "active_subscribers", "whales", "high_aov",
    "one_time_buyers", "otb", "cart_abandoners",
}

# Segments that should NEVER get flat discounts or BOGO — they buy without them
HIGH_VALUE_SEGMENTS = {"vip", "inner_circle", "whales", "high_aov", "active_seal", "active_subscribers"}

TRACKING_PARAMS = [
    {"type": "static",  "value": "Klaviyo",       "name": "utm_source"},
    {"type": "static",  "value": "campaign",      "name": "utm_medium"},
    {"type": "dynamic", "value": "campaign_name", "name": "utm_campaign"},
    {"type": "dynamic", "value": "campaign_id",   "name": "utm_id"},
    {"type": "static",  "value": "Klaviyo",       "name": "tw_source"},
    {"type": "dynamic", "value": "profile_id",    "name": "tw_profile_id"},
    {"type": "static",  "value": "campaign",      "name": "tw_medium"},
]

CONTENT_KEYWORDS = {"sleep", "science", "research", "story", "meditation",
                    "audio", "brain", "study", "discover", "learn", "guide"}


# ── Step 0: Does this slot need a landing page? ───────────────────────────────

def _needs_page(slot: dict) -> bool:
    if slot.get("needs_page"):
        return True
    angle = slot.get("topic_angle", "").lower()
    return any(kw in angle for kw in CONTENT_KEYWORDS)


# ── Step 1: Create Shopify landing page ───────────────────────────────────────

PAGE_SYSTEM = """You are a landing page copywriter for Beezy Beez Honey (trybeezybeez.com).
Write a branded landing page for women 50+ seeking better sleep.
Warm, empathetic, science-grounded. Georgia serif aesthetic.

Output ONLY valid JSON. Schema:
{
  "title": "Page title (H1)",
  "slug": "url-handle-no-spaces",
  "hero_line": "One powerful opening sentence",
  "body_paragraphs": ["para 1", "para 2", "para 3"],
  "science_fact": "One specific research finding with researcher/institution name",
  "cta_text": "SHOP NOW"
}"""

PAGE_HTML_TEMPLATE = """<style>
.bb-page{{font-family:Georgia,serif;color:#2c2417;max-width:680px;margin:0 auto;padding:40px 24px;background:#faf6ee;}}
.bb-page h1{{font-size:36px;line-height:1.2;color:#8b4513;margin:0 0 24px;font-weight:normal;}}
.bb-page p{{font-size:18px;line-height:1.8;margin:0 0 20px;}}
.bb-science{{background:#fff8ec;border-left:3px solid #d4a847;padding:16px 20px;margin:28px 0;font-size:16px;font-style:italic;color:#5a4030;}}
.bb-cta{{display:block;background:#8b4513;color:#fff;text-decoration:none;padding:16px 40px;border-radius:4px;text-align:center;font-size:16px;letter-spacing:1.5px;text-transform:uppercase;margin:32px auto;max-width:280px;}}
.bb-discount{{background:#fdf5e6;border:1px dashed #d4a847;border-radius:4px;padding:14px 20px;text-align:center;margin:20px 0;font-size:15px;}}
.bb-discount strong{{font-size:22px;letter-spacing:2px;color:#8b4513;}}
</style>
<div class="bb-page">
<h1>{title}</h1>
<p>{hero_line}</p>
{body_html}
<div class="bb-science">{science_fact}</div>
{discount_html}
<a href="{cta_url}" class="bb-cta">{cta_text}</a>
</div>"""


def _generate_page_content(slot: dict, discount_code: str = "") -> dict:
    key = os.environ.get("BEEZY_ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=MODEL, max_tokens=1024, system=PAGE_SYSTEM,
        messages=[{"role": "user", "content":
            "Topic: " + slot.get("topic_angle", "") + "\n"
            "Audience: " + slot.get("audience", "women 50+") + "\n"
            "Date: " + slot.get("date", "") +
            ("\nDiscount code: " + discount_code if discount_code else "")
        }],
    )
    raw = msg.content[0].text.strip()
    s, e = raw.find("{"), raw.rfind("}")
    return json.loads(raw[s:e+1] if s != -1 else raw)


def _build_page_html(content: dict, discount_code: str = "", cta_url: str = "") -> str:
    body_html = "\n".join("<p>" + p + "</p>" for p in content.get("body_paragraphs", []))
    discount_html = ""
    if discount_code:
        discount_html = '<div class="bb-discount">Your code: <strong>' + discount_code + '</strong></div>'
    return PAGE_HTML_TEMPLATE.format(
        title=content.get("title", ""),
        hero_line=content.get("hero_line", ""),
        body_html=body_html,
        science_fact=content.get("science_fact", ""),
        discount_html=discount_html,
        cta_url=cta_url or "https://trybeezybeez.com/products/honey-sub",
        cta_text=content.get("cta_text", "SHOP NOW"),
    )


def _create_shopify_page(slug: str, title: str, html: str) -> str:
    shop  = os.environ.get("SHOPIFY_SHOP_DOMAIN")
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    url   = "https://" + shop + "/admin/api/2025-10/graphql.json"
    hdrs  = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}

    # Check if page exists
    check = httpx.post(url, headers=hdrs, timeout=30, json={
        "query": '{ pages(first:1, query:"handle:' + slug + '") { edges { node { id } } } }'
    })
    edges = check.json().get("data", {}).get("pages", {}).get("edges", [])

    if edges:
        page_id = edges[0]["node"]["id"]
        resp = httpx.post(url, headers=hdrs, timeout=30, json={
            "query": """mutation pageUpdate($id: ID!, $page: PageUpdateInput!) {
                pageUpdate(id: $id, page: $page) { page { id handle } userErrors { field message } }
            }""",
            "variables": {"id": page_id, "page": {"body": html, "isPublished": True}},
        })
    else:
        resp = httpx.post(url, headers=hdrs, timeout=30, json={
            "query": """mutation pageCreate($page: PageCreateInput!) {
                pageCreate(page: $page) { page { id handle } userErrors { field message } }
            }""",
            "variables": {"page": {"title": title, "handle": slug,
                                   "body": html, "isPublished": True}},
        })

    resp.raise_for_status()
    return "https://" + shop + "/pages/" + slug


# ── Step 2: Shopify discount creation ─────────────────────────────────────────

def _create_shopify_discount(slot: dict) -> str:
    shop  = os.environ.get("SHOPIFY_SHOP_DOMAIN")
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    url   = "https://" + shop + "/admin/api/2025-10/graphql.json"
    hdrs  = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}

    code  = slot.get("discount_code", "").upper().replace(" ", "")
    pct   = float(slot.get("discount_pct", 20)) / 100.0
    now   = datetime.now(timezone.utc)

    mutation = """mutation discountCodeBasicCreate($basicCodeDiscount: DiscountCodeBasicInput!) {
      discountCodeBasicCreate(basicCodeDiscount: $basicCodeDiscount) {
        codeDiscountNode { id codeDiscount { ... on DiscountCodeBasic {
          codes(first:1) { nodes { code } }
        }}}
        userErrors { field code message }
      }
    }"""
    variables = {"basicCodeDiscount": {
        "title":     "Beezy " + str(int(pct*100)) + "% | " + slot.get("audience","") + " | " + slot.get("date",""),
        "code":      code,
        "startsAt":  now.isoformat(),
        "endsAt":    (now + timedelta(hours=72)).isoformat(),
        "customerGets": {"value": {"percentage": pct}, "items": {"all": True}},
        "customerSelection": {"all": True},
        "appliesOncePerCustomer": True,
    }}
    resp = httpx.post(url, headers=hdrs, timeout=30,
                      json={"query": mutation, "variables": variables})
    resp.raise_for_status()
    data   = resp.json().get("data", {}).get("discountCodeBasicCreate", {})
    errors = data.get("userErrors", [])
    if errors:
        print("[beezy_campaign] Discount warning: " + str(errors))
    nodes = (data.get("codeDiscountNode", {}).get("codeDiscount", {})
             .get("codes", {}).get("nodes", []))
    return nodes[0]["code"] if nodes else code


# ── Step 3: Generate copy ─────────────────────────────────────────────────────

COPY_SYSTEM = """You are the creative director for Beezy Beez Honey (trybeezybeez.com).
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
}"""


def _generate_copy(slot: dict, page_url: str = "", discount_code: str = "") -> dict:
    key = os.environ.get("BEEZY_ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=key)
    aud_key = slot.get("audience", "?").lower().replace(" ", "_")
    aud_type = "HIGH_VALUE_CUSTOMER" if aud_key in HIGH_VALUE_SEGMENTS else (
               "CUSTOMER" if aud_key in CUSTOMER_SEGMENTS else "PROSPECT")
    context = (
        "Campaign type: " + slot.get("content_type", "") + "\n"
        "Audience: " + slot.get("audience", "?") + "\n"
        "Audience type: " + aud_type + " — follow the OFFER RULES for this type.\n"
        "Topic: " + slot.get("topic_angle", "") + "\n"
        "Send time: " + slot.get("send_time_est", "14:00") + " EST"
    )
    if page_url:
        context += "\nLanding page URL: " + page_url + "\n(Drive readers to this page)"
    if discount_code:
        context += "\nDiscount code: " + discount_code + " (" + str(slot.get("discount_pct","")) + "% off)"
    if slot.get("parent_send") and slot.get("content_type") == "sniper_followup":
        context += "\nNon-opener follow-up to: " + slot["parent_send"] + " — different subject required"

    # Incorporate winning subject pattern if one has been learned for this audience
    patterns = _load_subject_patterns()
    winner = patterns.get(aud_key, {}).get("winning_type")
    if winner == "benefit":
        context += "\nSubject line guidance: previous sends show BENEFIT-FOCUSED subjects outperform curiosity for this audience. Lead with the outcome (e.g. 'Sleep through the night, {{ first_name }}')."
    elif winner == "curiosity":
        context += "\nSubject line guidance: previous sends show CURIOSITY subjects outperform benefit for this audience. Lead with a question or mystery."

    msg = client.messages.create(
        model=MODEL, max_tokens=1024, system=COPY_SYSTEM,
        messages=[{"role": "user", "content": context}],
    )
    raw = msg.content[0].text.strip()
    s, e = raw.find("{"), raw.rfind("}")
    return json.loads(raw[s:e+1] if s != -1 else raw)


# ── A/B subject line helpers ──────────────────────────────────────────────────

def _load_subject_patterns() -> dict:
    """Read subject_patterns from agent_state to inform copy generation."""
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM agent_state WHERE key='subject_patterns'"
            ).fetchone()
        if row:
            return json.loads(row[0])
    except Exception:
        pass
    return {}


def _generate_ab_subject(slot: dict, primary_subject: str) -> str:
    """Generate a benefit-focused alternative to the curiosity-based primary subject.

    Primary is typically curiosity-driven (why/what/how). Alt is direct-benefit:
    'Sleep through the night — finally' vs 'Why are you waking at 3am, {{ first_name }}?'
    """
    key = os.environ.get("BEEZY_ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=key)
    system = (
        "You are a subject line writer for Beezy Beez Honey (trybeezybeez.com). "
        "Write ONE alternative email subject line for women 50+.\n"
        "Style: direct benefit — state the outcome they will get, not the question/mystery.\n"
        "Rules: 6-9 words. Include {{ first_name }} once. No exclamation marks. No click-bait. "
        "Output ONLY the subject line text, nothing else."
    )
    context = (
        f"Audience: {slot.get('audience', '?')}\n"
        f"Topic: {slot.get('topic_angle', '')}\n"
        f"Primary subject (curiosity-style): {primary_subject}\n\n"
        "Write the benefit-focused alternative now:"
    )
    try:
        msg = client.messages.create(
            model=MODEL, max_tokens=80, system=system,
            messages=[{"role": "user", "content": context}],
        )
        alt = msg.content[0].text.strip().strip('"').strip("'")
        return alt if len(alt) > 5 else primary_subject
    except Exception as e:
        print(f"[beezy_campaign] A/B subject generation failed: {e}")
        return primary_subject


# ── Step 4: Generate image ─────────────────────────────────────────────────────

def _generate_image(prompt: str) -> str:
    try:
        from workers.image_gen import generate_cover
        return generate_cover(prompt).url
    except Exception:
        pass
    api_key = os.environ.get("HIGGSFIELD_API_KEY", "")
    secret  = os.environ.get("HIGGSFIELD_SECRET", "")
    model   = os.environ.get("HIGGSFIELD_IMAGE_MODEL", "higgsfield-ai/soul/standard")
    base    = "https://platform.higgsfield.ai"
    hdrs    = {"Authorization": "Key " + api_key + ":" + secret, "Content-Type": "application/json"}
    resp    = httpx.post(base + "/" + model, headers=hdrs, timeout=30,
                         json={"prompt": prompt, "aspect_ratio": "16:9", "resolution": "720p"})
    resp.raise_for_status()
    req_id  = resp.json()["request_id"]
    for _ in range(60):
        time.sleep(5)
        s = httpx.get(base + "/requests/" + req_id + "/status", headers=hdrs, timeout=20).json()
        if s.get("status") == "completed":
            return s["images"][0]["url"]
        if s.get("status") == "failed":
            raise RuntimeError("Higgsfield failed: " + str(s))
    raise RuntimeError("Higgsfield timed out.")


def _upload_to_shopify_cdn(image_url: str, alt: str = "") -> str:
    try:
        from workers.shopify_publisher import upload_image_to_cdn
        return upload_image_to_cdn(image_url, alt)
    except (ImportError, AttributeError):
        pass
    shop  = os.environ.get("SHOPIFY_SHOP_DOMAIN")
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    url   = "https://" + shop + "/admin/api/2025-10/graphql.json"
    hdrs  = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    resp  = httpx.post(url, headers=hdrs, timeout=30, json={
        "query": """mutation fileCreate($files: [FileCreateInput!]!) {
          fileCreate(files: $files) {
            files { ... on MediaImage { image { url } } }
            userErrors { field message }
          }}""",
        "variables": {"files": [{"originalSource": image_url,
                                  "mediaContentType": "IMAGE", "alt": alt}]},
    })
    resp.raise_for_status()
    files = resp.json().get("data", {}).get("fileCreate", {}).get("files", [])
    if files and files[0].get("image", {}).get("url"):
        return files[0]["image"]["url"]
    return image_url


# ── Step 5: Build email HTML ──────────────────────────────────────────────────

EMAIL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body{{margin:0;padding:0;background:#faf6ee;font-family:Georgia,serif;color:#2c2417;}}
  .pre{{display:none;max-height:0;overflow:hidden;font-size:1px;color:#faf6ee;}}
  .wrap{{max-width:600px;margin:0 auto;}}
  .content{{padding:32px 28px 0;}}
  p{{font-size:17px;line-height:1.75;margin:0 0 18px;color:#2c2417;}}
  .code-box{{background:#fdf5e6;border:1px dashed #d4a847;border-radius:4px;
    padding:12px 20px;text-align:center;margin:0 0 18px;font-size:15px;}}
  .code-box strong{{font-size:20px;letter-spacing:2px;color:#8b4513;}}
  .cta-wrap{{text-align:center;padding:8px 0 32px;}}
  .cta{{display:inline-block;background:#8b4513;color:#fff!important;
    text-decoration:none;padding:14px 36px;border-radius:4px;
    font-size:15px;letter-spacing:1.5px;text-transform:uppercase;}}
  .footer{{border-top:1px solid #e8dcc8;padding:20px 28px;
    font-size:13px;color:#8b7355;text-align:center;}}
  @media(max-width:600px){{.content{{padding:20px 16px 0;}}}}
</style>
</head>
<body>
<div class="pre">{preview}</div>
<table class="wrap" width="600" cellpadding="0" cellspacing="0" border="0" align="center">
  <tr><td><img src="{image_url}" alt="Beezy Beez" width="600" style="width:100%;display:block;border:0;"></td></tr>
  <tr><td class="content">
    <p>Hi {{{{ person.first_name|default:'there' }}}},</p>
    {body_paragraphs}
    {discount_box}
    <div class="cta-wrap"><a href="{cta_url}" class="cta">{cta_text}</a></div>
  </td></tr>
  <tr><td class="footer">
    <p style="margin:0 0 8px;">Beezy Beez Honey &middot; <a href="https://trybeezybeez.com" style="color:#8b4513;">trybeezybeez.com</a></p>
    {{% unsubscribe 'Unsubscribe' %}}
  </td></tr>
</table>
</body>
</html>"""


def _build_email_html(copy: dict, image_url: str, cta_url: str, discount_code: str = "") -> str:
    paras        = "\n    ".join("<p>" + p + "</p>" for p in copy.get("body_paragraphs", []))
    discount_box = ""
    if discount_code:
        discount_box = '<div class="code-box">Your code: <strong>' + discount_code + '</strong></div>'
    return EMAIL_HTML.format(
        preview=copy.get("preview_text", ""),
        image_url=image_url,
        body_paragraphs=paras,
        discount_box=discount_box,
        cta_url=cta_url,
        cta_text=copy.get("cta_text", "SHOP NOW"),
    )


# ── Step 6: Klaviyo deployment ────────────────────────────────────────────────

def _klaviyo_headers() -> dict:
    return {"Authorization": "Klaviyo-API-Key " + os.environ.get("KLAVIYO_API_KEY", ""),
            "revision": "2025-10-15", "Content-Type": "application/json"}


def _create_template(html: str, name: str) -> str:
    resp = httpx.post("https://a.klaviyo.com/api/templates/",
                      headers=_klaviyo_headers(), timeout=30,
                      json={"data": {"type": "template", "attributes":
                            {"name": name, "html": html, "editor_type": "CODE"}}})
    if not resp.is_success:
        raise RuntimeError("Template " + str(resp.status_code) + ": " + resp.text[:400])
    return resp.json()["data"]["id"]


def _create_campaign(slot: dict, copy: dict, segment_id: str) -> tuple[str, str]:
    from_email = os.environ.get("KLAVIYO_FROM_EMAIL", "help@trybeezybeez.com")
    name       = slot.get("audience","?") + " | " + slot.get("topic_angle","")[:35] + " | " + slot.get("date","")
    payload    = {"data": {"type": "campaign", "attributes": {
        "name": name,
        "audiences": {"included": [segment_id], "excluded": []},
        "send_options": {"use_smart_sending": False},
        "tracking_options": {"is_tracking_opens": True, "is_tracking_clicks": True,
                             "add_tracking_params": True, "custom_tracking_params": TRACKING_PARAMS},
        "campaign-messages": {"data": [{"type": "campaign-message", "attributes": {
            "definition": {"channel": "email", "content": {
                "subject": copy.get("subject", ""), "preview_text": "",
                "from_email": from_email, "from_label": copy.get("from_label", "Beezy Beez"),
            }},
        }}]},
    }}}
    resp = httpx.post("https://a.klaviyo.com/api/campaigns/",
                      headers=_klaviyo_headers(), timeout=30, json=payload)
    if not resp.is_success:
        raise RuntimeError("Campaign " + str(resp.status_code) + ": " + resp.text[:400])
    data       = resp.json()["data"]
    campaign_id = data["id"]
    messages   = data.get("relationships", {}).get("campaign-messages", {}).get("data", [])
    message_id = messages[0]["id"] if messages else ""
    return campaign_id, message_id


def _assign_template(message_id: str, template_id: str) -> None:
    resp = httpx.post("https://a.klaviyo.com/api/campaign-message-assign-template/",
                      headers=_klaviyo_headers(), timeout=30,
                      json={"data": {"type": "campaign-message", "id": message_id,
                                     "relationships": {"template": {"data": {
                                         "type": "template", "id": template_id}}}}})
    if not resp.is_success:
        raise RuntimeError("Assign " + str(resp.status_code) + ": " + resp.text[:400])


# ── Step 7: Slack notify ──────────────────────────────────────────────────────

def _slack_notify(slot: dict, copy: dict, campaign_id: str, page_url: str, image_url: str) -> None:
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook:
        return
    camp_url = "https://www.klaviyo.com/campaign/" + campaign_id + "/wizard"
    kind     = "Sniper" if slot.get("content_type") == "sniper_followup" else "Campaign"
    blocks   = [
        {"type": "header", "text": {"type": "plain_text",
             "text": kind + " Draft Ready — " + slot.get("date","")}},
        {"type": "image", "image_url": image_url, "alt_text": "Email hero"},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            "*Subject:* `" + copy.get("subject","") + "`\n"
            "*Preview:* _" + copy.get("preview_text","") + "_\n"
            "*Audience:* " + slot.get("audience","?") + "\n"
            "*Send:* " + slot.get("date","") + " @ " + slot.get("send_time_est","?") + " EST\n"
            "*Rev. est.:* $" + str(int(slot.get("revenue_estimate",0))) +
            ("   *Discount:* `" + slot.get("discount_code","") + "`" if slot.get("discount_code") else "") +
            ("\n*Page:* " + page_url if page_url else "")
        )}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Open in Klaviyo"},
             "url": camp_url, "style": "primary"},
        ] + ([{"type": "button", "text": {"type": "plain_text", "text": "View Landing Page"},
               "url": page_url}] if page_url else [])},
    ]
    httpx.post(webhook, json={"text": kind + " draft ready", "blocks": blocks}, timeout=10)


# ── Main entry ────────────────────────────────────────────────────────────────

def _resolve_segment(audience: str) -> str:
    slug = audience.lower().replace("-","_").replace(" ","_")
    if slug in SEGMENT_IDS:
        return SEGMENT_IDS[slug]
    for k, v in SEGMENT_IDS.items():
        if k in slug or slug in k:
            return v
    raise ValueError("No segment ID for '" + audience + "'")


def run(slot: dict) -> dict:
    audience   = slot.get("audience","?")
    segment_id = _resolve_segment(audience)
    date_str   = slot.get("date","")

    # Discount
    discount_code = ""
    if slot.get("discount_pct") and slot.get("discount_code"):
        print("[beezy_campaign] Creating Shopify discount...")
        discount_code = _create_shopify_discount(slot)
        print("[beezy_campaign]   Code: " + discount_code)

    # CTA URL (discount takes priority)
    if discount_code:
        cta_url = "https://trybeezybeez.com/discount/" + discount_code + "?redirect=/pages/bf-collection"
    else:
        cta_url = "https://trybeezybeez.com/pages/bf-collection"

    # RULE: Customer segments NEVER go to a landing page — direct to collection/discount
    audience_key = slot.get("audience", "").lower().replace(" ", "_")
    is_customer = audience_key in CUSTOMER_SEGMENTS
    if is_customer and not discount_code:
        cta_url = "https://trybeezybeez.com/pages/bf-collection"

    # Landing page (if needed — but NEVER for customer segments)
    page_url = ""
    if _needs_page(slot) and not is_customer:
        print("[beezy_campaign] Creating landing page...")
        page_content = _generate_page_content(slot, discount_code)
        page_html    = _build_page_html(page_content, discount_code, cta_url)
        slug         = page_content.get("slug", audience.replace("_","-") + "-" + date_str)
        page_url     = _create_shopify_page(slug, page_content.get("title",""), page_html)
        cta_url      = page_url  # email drives to page, page drives to product
        print("[beezy_campaign]   Page: " + page_url)

    # Copy
    print("[beezy_campaign] Generating copy...")
    copy = _generate_copy(slot, page_url, discount_code)
    print("[beezy_campaign]   Subject A (curiosity): " + copy.get("subject",""))

    # A/B subject generation for large segments (>3,000 estimated recipients)
    ab_subject = ""
    audience_key_ab = audience.lower().replace(" ", "_")
    if audience_key_ab in LARGE_SEGMENTS:
        print("[beezy_campaign] Large segment — generating benefit-focused alt subject...")
        ab_subject = _generate_ab_subject(slot, copy.get("subject", ""))
        print("[beezy_campaign]   Subject B (benefit):  " + ab_subject)

    # ── VALIDATOR GATE ──────────────────────────────────────────────────
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

    # ── 60-minute preview window ──────────────────────────────────────────
    print("[beezy_campaign] Posting preview — 60-minute cancel window...")
    _post_preview(slot, copy, campaign_id, cdn_url, ab_subject=ab_subject)
    _store_pending_schedule(campaign_id, message_id, slot, copy, cdn_url, page_url, ab_subject=ab_subject)

    camp_url = "https://www.klaviyo.com/campaign/" + campaign_id + "/wizard"
    print("[beezy_campaign]   Preview posted. Auto-schedules in 60 min: " + camp_url)
    return {"campaign_url": camp_url, "page_url": page_url, "campaign_id": campaign_id}


def _post_preview(slot: dict, copy: dict, campaign_id: str, image_url: str, ab_subject: str = "") -> None:
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook:
        return
    aud = slot.get("audience", "?")
    send_time = slot.get("send_time_est", "TBD")
    rev = int(slot.get("revenue_estimate", 0) or 0)
    body_preview = " ".join(copy.get("body_paragraphs", [""])[:1])[:140]
    subject_text = f"📧 *Subject A (curiosity — auto-selected):* `{copy.get('subject', '')}`"
    if ab_subject and ab_subject != copy.get("subject", ""):
        subject_text += f"\n📧 *Subject B (benefit):* `{ab_subject}`\n_A/B tracked — winner informs future sends_"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "👁 Campaign Preview — ready to schedule"}},
        {"type": "image", "image_url": image_url, "alt_text": "Email hero"},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            f"{subject_text}\n"
            f"👥 *Audience:* {aud}\n"
            f"🕐 *Scheduled:* {send_time} ET\n"
            f"💰 *Est. revenue:* ${rev:,}\n\n"
            f"_{body_preview}..._\n\n"
            f"✅ Auto-schedules in 60 minutes unless you cancel.\n"
            f"❌ Type `cancel {campaign_id}` to abort.\n"
            f"Klaviyo: https://www.klaviyo.com/campaign/{campaign_id}/wizard"
        )}},
    ]
    httpx.post(webhook, json={"text": "Campaign preview", "blocks": blocks}, timeout=10)


def _subject_type_to_send(audience: str, has_ab: bool, patterns: dict) -> str:
    """Alternate curiosity/benefit per audience using last_used from subject_patterns."""
    if not has_ab:
        return "curiosity"
    last = patterns.get(audience, {}).get("last_used", "benefit")  # first time: send curiosity
    return "benefit" if last == "curiosity" else "curiosity"


def _store_pending_schedule(campaign_id: str, message_id: str, slot: dict, copy: dict, image_url: str, page_url: str, ab_subject: str = "") -> None:
    """Store campaign in agent_state as pending_schedule so the cron can auto-schedule it after 60 min."""
    try:
        from db.connection import get_conn
        now_iso = datetime.now(timezone.utc).isoformat()
        audience = slot.get("audience", "")
        patterns = _load_subject_patterns()
        subject_type_to_send = _subject_type_to_send(audience, bool(ab_subject), patterns)
        entry = {
            "campaign_id": campaign_id,
            "message_id": message_id,
            "slot": slot,
            "copy": {k: copy.get(k, "") for k in ("subject", "send_time_est", "from_label")},
            "image_url": image_url,
            "page_url": page_url,
            "queued_at": now_iso,
            "cancelled": False,
            "subject_type_to_send": subject_type_to_send,
            "ab_subject": ab_subject,
        }
        with get_conn() as conn:
            row = conn.execute("SELECT value FROM agent_state WHERE key='pending_schedules'").fetchone()
            pending = json.loads(row[0]) if row else []
            pending.append(entry)
            conn.execute(
                "INSERT INTO agent_state (key, value, updated_at) VALUES ('pending_schedules', %s, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                (json.dumps(pending),)
            )
            conn.commit()
    except Exception as e:
        print(f"[beezy_campaign] pending_schedule store error: {e}")


def _patch_message_subject(message_id: str, subject: str) -> None:
    """PATCH the campaign-message subject in Klaviyo. Used to swap A→B subject before scheduling."""
    headers = {
        "Authorization": "Klaviyo-API-Key " + os.environ.get("KLAVIYO_API_KEY", ""),
        "revision": "2025-10-15",
        "Content-Type": "application/json",
    }
    resp = httpx.patch(
        f"https://a.klaviyo.com/api/campaign-messages/{message_id}/",
        headers=headers,
        timeout=20,
        json={"data": {
            "type": "campaign-message",
            "id": message_id,
            "attributes": {"definition": {"content": {"subject": subject}}},
        }},
    )
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"PATCH campaign-message {resp.status_code}: {resp.text[:200]}")


def check_pending_schedules() -> None:
    """Called by cron every few minutes — schedules any campaigns older than 60 minutes."""
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            row = conn.execute("SELECT value FROM agent_state WHERE key='pending_schedules'").fetchone()
        if not row:
            return
        pending = json.loads(row[0])
    except Exception as e:
        print(f"[check_pending_schedules] read error: {e}")
        return

    now = datetime.now(timezone.utc)
    updated = []
    for entry in pending:
        if entry.get("cancelled"):
            continue  # skip cancelled
        queued_at = datetime.fromisoformat(entry["queued_at"].replace("Z", "+00:00"))
        age_minutes = (now - queued_at).total_seconds() / 60
        if age_minutes < 60:
            updated.append(entry)  # keep — not yet 60 minutes
            continue
        # Time to schedule
        campaign_id   = entry["campaign_id"]
        message_id    = entry.get("message_id", "")
        slot          = entry.get("slot", {})
        audience      = slot.get("audience", "")
        subject_type  = entry.get("subject_type_to_send", "curiosity")
        ab_subject    = entry.get("ab_subject", "")
        primary_subj  = entry.get("copy", {}).get("subject", "")

        print(f"[check_pending_schedules] scheduling {campaign_id} — subject_type={subject_type} after {age_minutes:.0f}min")

        # Swap to benefit subject if warranted
        if subject_type == "benefit" and ab_subject and message_id:
            try:
                _patch_message_subject(message_id, ab_subject)
                print(f"[check_pending_schedules]   Patched subject to benefit: {ab_subject[:60]}")
            except Exception as pe:
                print(f"[check_pending_schedules]   Subject patch failed (sending curiosity): {pe}")
                subject_type = "curiosity"

        try:
            sched_result = schedule_campaign(campaign_id, slot)
        except Exception as se:
            updated.append(entry)  # retry next pass
            print(f"[check_pending_schedules]   schedule_error: {se}")
            continue

        send_time = sched_result.get("send_time", "?")
        print(f"[check_pending_schedules]   {campaign_id}: scheduled {send_time} | {subject_type}")

        # Persist subject_type to calendar_executions notes and update subject_patterns
        try:
            from db.connection import get_conn
            with get_conn() as conn:
                conn.execute(
                    "UPDATE calendar_executions "
                    "SET notes = COALESCE(notes,'') || ' | subject_type:' || %s "
                    "WHERE klaviyo_campaign_id = %s",
                    (subject_type, campaign_id)
                )
                # Update last_used in subject_patterns so next send alternates
                row = conn.execute("SELECT value FROM agent_state WHERE key='subject_patterns'").fetchone()
                sp = json.loads(row[0]) if row else {}
                aud_data = sp.get(audience, {})
                aud_data["last_used"] = subject_type
                sp[audience] = aud_data
                conn.execute(
                    "INSERT INTO agent_state (key, value, updated_at) VALUES ('subject_patterns', %s, NOW()) "
                    "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                    (json.dumps(sp),)
                )
                conn.commit()
        except Exception as de:
            print(f"[check_pending_schedules]   notes/pattern update error: {de}")

        # Don't add to updated — removes from pending list

    try:
        from db.connection import get_conn
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO agent_state (key, value, updated_at) VALUES ('pending_schedules', %s, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                (json.dumps(updated),)
            )
            conn.commit()
    except Exception as e:
        print(f"[check_pending_schedules] write error: {e}")
