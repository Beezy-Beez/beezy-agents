"""
Updated beezy_campaign.py — discount codes + Slack handoff for Klaviyo deployment.

Changes:
1. Calendar slots with discount_pct get a Shopify discount created automatically
2. Discount code woven into email copy + CTA button URL
3. Klaviyo deployment handed off to Slack (deployer chat uses MCP tools)
   — no more fighting the Klaviyo REST API from Python
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

import anthropic
import httpx

MODEL = "claude-sonnet-4-6"

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

TRACKING_PARAMS = [
    {"type": "static",  "value": "Klaviyo",       "name": "utm_source"},
    {"type": "static",  "value": "campaign",      "name": "utm_medium"},
    {"type": "dynamic", "value": "campaign_name", "name": "utm_campaign"},
    {"type": "dynamic", "value": "campaign_id",   "name": "utm_id"},
    {"type": "static",  "value": "Klaviyo",       "name": "tw_source"},
    {"type": "dynamic", "value": "profile_id",    "name": "tw_profile_id"},
    {"type": "static",  "value": "campaign",      "name": "tw_medium"},
]

COPY_SYSTEM = """You are the creative director for Beezy Beez Honey (trybeezybeez.com).
DTC botanical extract honey. Target: women 50+ seeking better sleep. AOV ~$54.95.

Write at top-1% Health & Wellness DTC benchmarks:
- Subject: curiosity-driven, personal, specific. 6-9 words. No clickbait.
- Preview: extends subject naturally. Under 90 chars.
- Body: warm, empathetic. Opens with a person or moment. 3 short paragraphs max.
  Personalization: {{ person.first_name|default:'there' }}
- If discount_code is provided: mention it naturally in the body AND in/near the CTA.
  e.g. "Use code {discount_code} at checkout" or "Your code: {discount_code}"
- from_label: "Alan from Beezy Beez" for personal/lapsed/editorial,
              "Beezy Beez" for promotional/product-led.
- image_prompt: 12-word Higgsfield prompt. Woman 50+, warm honey tones,
  no text, editorial lifestyle, real human face required.

Output ONLY valid JSON. No markdown. Schema:
{
  "subject": "...",
  "preview_text": "under 90 chars",
  "from_label": "Alan from Beezy Beez",
  "body_paragraphs": ["para 1", "para 2", "para 3"],
  "cta_text": "SHOP NOW",
  "image_prompt": "12-word prompt"
}"""


# ── Step 1: Generate copy ─────────────────────────────────────────────────────

def _generate_copy(slot: dict, discount_code: str = "") -> dict:
    key = os.environ.get("BEEZY_ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("BEEZY_ANTHROPIC_API_KEY not set.")
    client = anthropic.Anthropic(api_key=key)
    kind   = slot.get("content_type", "")
    parent = ""
    if kind == "sniper_followup" and slot.get("parent_send"):
        parent = "\nNon-opener follow-up to: " + slot["parent_send"] + "\nDifferent subject + angle required."
    discount_note = ""
    if discount_code:
        pct = slot.get("discount_pct", "")
        discount_note = f"\ndiscount_code: {discount_code}" + (f"\ndiscount_pct: {pct}%" if pct else "")

    msg = client.messages.create(
        model=MODEL, max_tokens=1024, system=COPY_SYSTEM,
        messages=[{"role": "user", "content": (
            "Campaign type: " + kind + "\n"
            "Audience: " + slot.get("audience", "?") + "\n"
            "Topic/angle: " + slot.get("topic_angle", "") + "\n"
            "Send time: " + slot.get("send_time_est", "14:00") + " EST\n"
            "Rationale: " + slot.get("rationale", "")
            + discount_note + parent
        )}],
    )
    raw = msg.content[0].text.strip()
    s, e = raw.find("{"), raw.rfind("}")
    return json.loads(raw[s:e+1] if s != -1 else raw)


# ── Step 2: Create Shopify discount ──────────────────────────────────────────

def _create_shopify_discount(slot: dict) -> str:
    """
    Create a percentage discount in Shopify. Returns the discount code string.
    Only called when slot has discount_pct and discount_code fields.
    """
    shop  = os.environ.get("SHOPIFY_SHOP_DOMAIN")
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    url   = "https://" + shop + "/admin/api/2025-10/graphql.json"
    hdrs  = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}

    code     = slot.get("discount_code", "").upper().replace(" ", "")
    pct      = float(slot.get("discount_pct", 20)) / 100.0
    audience = slot.get("audience", "campaign")
    date_str = slot.get("date", "")
    title    = f"Beezy {int(pct*100)}% off | {audience} | {date_str}"

    now        = datetime.now(timezone.utc)
    starts_at  = now.isoformat()
    ends_at    = (now + timedelta(hours=72)).isoformat()

    mutation = """
    mutation discountCodeBasicCreate($basicCodeDiscount: DiscountCodeBasicInput!) {
      discountCodeBasicCreate(basicCodeDiscount: $basicCodeDiscount) {
        codeDiscountNode {
          id
          codeDiscount {
            ... on DiscountCodeBasic {
              codes(first: 1) { nodes { code } }
            }
          }
        }
        userErrors { field code message }
      }
    }"""

    variables = {"basicCodeDiscount": {
        "title":       title,
        "code":        code,
        "startsAt":    starts_at,
        "endsAt":      ends_at,
        "usageLimit":  None,
        "customerGets": {
            "value":    {"percentage": pct},
            "items":    {"all": True},
        },
        "customerSelection": {"all": True},
        "appliesOncePerCustomer": True,
    }}

    resp = httpx.post(url, headers=hdrs, timeout=30,
                      json={"query": mutation, "variables": variables})
    resp.raise_for_status()
    data   = resp.json().get("data", {}).get("discountCodeBasicCreate", {})
    errors = data.get("userErrors", [])
    if errors:
        print(f"[beezy_campaign] Discount warning: {errors}")
        return code  # Return code anyway — may already exist

    nodes = (data.get("codeDiscountNode", {})
             .get("codeDiscount", {})
             .get("codes", {})
             .get("nodes", []))
    return nodes[0]["code"] if nodes else code


# ── Step 3: Generate image (Higgsfield REST) ──────────────────────────────────

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
    headers = {"Authorization": "Key " + api_key + ":" + secret,
               "Content-Type": "application/json"}
    resp = httpx.post(base + "/" + model, headers=headers, timeout=30,
                      json={"prompt": prompt, "aspect_ratio": "16:9", "resolution": "720p"})
    resp.raise_for_status()
    req_id = resp.json()["request_id"]
    for _ in range(60):
        time.sleep(5)
        s = httpx.get(base + "/requests/" + req_id + "/status", headers=headers, timeout=20).json()
        if s.get("status") == "completed":
            return s["images"][0]["url"]
        if s.get("status") == "failed":
            raise RuntimeError("Higgsfield failed: " + str(s))
    raise RuntimeError("Higgsfield timed out.")


# ── Step 4: Upload image to Shopify CDN ───────────────────────────────────────

def _upload_to_shopify_cdn(image_url: str, alt_text: str = "") -> str:
    try:
        from workers.shopify_publisher import upload_image_to_cdn
        return upload_image_to_cdn(image_url, alt_text)
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
                                  "mediaContentType": "IMAGE", "alt": alt_text}]},
    })
    resp.raise_for_status()
    files = resp.json().get("data", {}).get("fileCreate", {}).get("files", [])
    if files and files[0].get("image", {}).get("url"):
        return files[0]["image"]["url"]
    return image_url


# ── Step 5: Build email HTML ──────────────────────────────────────────────────

def _build_html(copy: dict, image_url: str, discount_code: str = "") -> str:
    preview = copy.get("preview_text", "")
    paras   = "\n".join("<p>" + p + "</p>" for p in copy.get("body_paragraphs", []))
    cta_txt = copy.get("cta_text", "SHOP NOW")

    if discount_code:
        cta_url = ("https://trybeezybeez.com/discount/" + discount_code
                   + "?redirect=/pages/bf-collection")
    else:
        cta_url = copy.get("cta_url", "https://trybeezybeez.com/products/honey-sub")

    img_tag = ('<img src="' + image_url + '" alt="Beezy Beez" '
               'width="600" style="width:100%;display:block;border:0;">') if image_url else ""

    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body{margin:0;padding:0;background:#faf6ee;font-family:Georgia,serif;color:#2c2417;}
  .pre{display:none;max-height:0;overflow:hidden;font-size:1px;color:#faf6ee;}
  .wrap{max-width:600px;margin:0 auto;}
  .content{padding:32px 28px 0;}
  p{font-size:17px;line-height:1.75;margin:0 0 18px;color:#2c2417;}
  .code-box{background:#fdf5e6;border:1px dashed #d4a847;border-radius:4px;
    padding:12px 20px;text-align:center;margin:0 0 18px;font-size:15px;}
  .code-box strong{font-size:20px;letter-spacing:2px;color:#8b4513;}
  .cta-wrap{text-align:center;padding:8px 0 32px;}
  .cta{display:inline-block;background:#8b4513;color:#fff!important;
    text-decoration:none;padding:14px 36px;border-radius:4px;
    font-size:15px;letter-spacing:1.5px;text-transform:uppercase;}
  .footer{border-top:1px solid #e8dcc8;padding:20px 28px;
    font-size:13px;color:#8b7355;text-align:center;}
</style>
</head>
<body>
<div class="pre">""" + preview + """</div>
<table class="wrap" width="600" cellpadding="0" cellspacing="0" border="0" align="center">
  <tr><td>""" + img_tag + """</td></tr>
  <tr><td class="content">
    <p>Hi {{ person.first_name|default:'there' }},</p>
    """ + paras + """
    """ + ("""<div class="code-box">Your discount code: <strong>""" + discount_code + """</strong></div>""" if discount_code else "") + """
    <div class="cta-wrap">
      <a href=\"""" + cta_url + """\" class="cta">""" + cta_txt + """</a>
    </div>
  </td></tr>
  <tr><td class="footer">
    <p style="margin:0 0 8px;">Beezy Beez Honey &middot; <a href="https://trybeezybeez.com" style="color:#8b4513;">trybeezybeez.com</a></p>
    {% unsubscribe 'Unsubscribe' %}
  </td></tr>
</table>
</body>
</html>"""


# ── Step 6: Post to Slack for deployer to pick up ────────────────────────────

def _post_to_deployer_channel(slot: dict, copy: dict, html: str,
                               image_url: str, discount_code: str,
                               segment_id: str) -> None:
    """
    Post structured campaign payload to Slack.
    The beezy-episode-deployer Claude chat reads this and deploys to Klaviyo via MCP.
    Format mirrors the #beezy-new-episodes handoff contract.
    """
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook:
        return

    audience  = slot.get("audience", "?")
    date_str  = slot.get("date", "")
    time_str  = slot.get("send_time_est", "14:00")
    from_email = os.environ.get("KLAVIYO_FROM_EMAIL", "help@trybeezybeez.com")

    campaign_payload = {
        "type":         "beezy_campaign",
        "campaign_name": audience + " | " + slot.get("topic_angle","")[:35] + " | " + date_str + " " + time_str,
        "subject":      copy.get("subject", ""),
        "preview_text": copy.get("preview_text", ""),
        "from_label":   copy.get("from_label", "Beezy Beez"),
        "from_email":   from_email,
        "segment_id":   segment_id,
        "audience":     audience,
        "send_time":    date_str + " " + time_str + " EST",
        "use_smart_sending": False,
        "tracking_params":   TRACKING_PARAMS,
        "discount_code": discount_code,
        "image_url":    image_url,
        "html":         html[:200] + "... [full HTML in thread]",
    }

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": "📧 Campaign Ready for Klaviyo Deploy — " + date_str}},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            "*Subject:* `" + copy.get("subject","") + "`\n"
            "*Preview:* _" + copy.get("preview_text","") + "_\n"
            "*Audience:* " + audience + " (`" + segment_id + "`)\n"
            "*Send:* " + date_str + " @ " + time_str + " EST\n"
            "*From:* " + copy.get("from_label","Beezy Beez") + "\n"
            "*Rev. Est.:* $" + str(int(slot.get("revenue_estimate", 0))) +
            ("   *Discount:* `" + discount_code + "`" if discount_code else "")
        )}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": "*Settings:* Smart Sending OFF · UTMs locked · Image CDN URL below"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": "*Image CDN:* " + image_url}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": "```" + json.dumps(campaign_payload, indent=2)[:2000] + "```"}},
        {"type": "context", "elements": [{"type": "mrkdwn",
            "text": "⬆️ Deploy: use klaviyo_create_email_template + klaviyo_create_campaign + klaviyo_assign_template_to_campaign_message with the HTML and config above."}]},
    ]

    # Post full HTML as a follow-up message in the same channel
    httpx.post(webhook, json={"text": "📧 Campaign Ready — " + date_str, "blocks": blocks}, timeout=10)
    # Post HTML separately (Slack message size limit)
    httpx.post(webhook, json={"text": "HTML for " + audience + " " + date_str + ":\n```" + html[:8000] + "```"}, timeout=10)


# ── Main entry ────────────────────────────────────────────────────────────────

def _resolve_segment(audience: str) -> str:
    slug = audience.lower().replace("-","_").replace(" ","_")
    if slug in SEGMENT_IDS:
        return SEGMENT_IDS[slug]
    for k, v in SEGMENT_IDS.items():
        if k in slug or slug in k:
            return v
    raise ValueError("No segment ID for '" + audience + "'. Add to SEGMENT_IDS.")


def run(slot: dict) -> dict:
    audience = slot.get("audience","?")
    segment_id = _resolve_segment(audience)

    # Discount code — create in Shopify if slot specifies one
    discount_code = ""
    if slot.get("discount_pct") and slot.get("discount_code"):
        print("[beezy_campaign] Creating Shopify discount: " + slot.get("discount_code",""))
        discount_code = _create_shopify_discount(slot)
        print("[beezy_campaign]   Code: " + discount_code)

    # Copy
    print("[beezy_campaign] Generating copy...")
    copy = _generate_copy(slot, discount_code)
    print("[beezy_campaign]   Subject: " + copy.get("subject",""))

    # Image
    image_prompt = copy.get("image_prompt",
        "Woman 50 warm honey tones editorial lifestyle real human face botanical")
    print("[beezy_campaign] Generating image...")
    raw_url = _generate_image(image_prompt)
    cdn_url = _upload_to_shopify_cdn(raw_url, "Beezy Beez email hero")
    print("[beezy_campaign]   CDN: " + cdn_url)

    # HTML
    html = _build_html(copy, cdn_url, discount_code)

    # Post to Slack for deployer to pick up and deploy to Klaviyo via MCP
    print("[beezy_campaign] Posting to Slack deployer channel...")
    _post_to_deployer_channel(slot, copy, html, cdn_url, discount_code, segment_id)
    print("[beezy_campaign]   Posted. Deployer chat will create Klaviyo campaign via MCP.")

    return {"status": "posted_to_slack", "audience": audience, "discount_code": discount_code}
