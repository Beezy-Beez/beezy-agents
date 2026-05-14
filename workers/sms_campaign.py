"""
Beezy SMS campaign worker — full autonomous pipeline.

Sequence:
  1. Generate SMS copy via Anthropic (short, punchy, 160-320 chars)
  2. Create Klaviyo SMS campaign with correct audience
  3. Validate (subset of email rules)
  4. Auto-schedule
  5. Notify Slack

SMS rules:
  - Max 320 chars (2 SMS segments). Aim for 160 (1 segment).
  - Direct CTA URL — always /pages/bf-collection or /discount/CODE
  - Personal, conversational. No salesy caps or exclamation overload.
  - 2-3x per WEEK max. active_seal (drops), vip (flash), lapsed_30d (urgency).
  - Comply with TCPA — Klaviyo handles opt-out automatically.
"""
from __future__ import annotations

import json
import os
from datetime import date

import anthropic
import httpx

MODEL = "claude-sonnet-4-6"

SEGMENT_IDS = {
    "lapsed_30d": "UEQD6k", "lapsed_60d": "UfARWm",
    "lapsed_90d": "XuS7rY", "lapsed_180d": "W98qh3",
    "vip": "RArtzN", "inner_circle": "RArtzN",
    "engaged_customers": "RvtHdn",
    "active_seal": "UBFUcH", "active_subscribers": "UBFUcH",
    "whales": "VAUD58", "high_aov": "Res3GH",
    "one_time_buyers": "UfARWm", "otb": "UfARWm",
}

HIGH_VALUE_SEGMENTS = {"vip", "inner_circle", "whales", "high_aov", "active_seal", "active_subscribers"}

SMS_COPY_SYSTEM = """You write SMS messages for Beezy Beez Honey (trybeezybeez.com).
Target: women 50+, sleep wellness, botanical extract honey.

RULES:
- MUST be under 300 characters total (aim for 160 — one SMS segment).
- Conversational, warm, personal. Like a text from a friend who runs a small honey company.
- ONE clear CTA with a short URL placeholder: {cta_url}
- If discount: mention the code naturally. "Use SAVE20 at checkout"
- If no discount: product recommendation or check-in.
- NEVER use ALL CAPS words. Max one exclamation mark in the entire message.
- No emojis overload. Max 1-2 emojis, placed naturally.

OFFER RULES BY AUDIENCE:
- VIP, whales, active_seal: NEVER discount. Insider updates, new product drops, restock nudges.
- lapsed_30d: urgency, "we miss you", credit offers OK.
- engaged_customers: product features, sleep tips, seasonal.

Output ONLY valid JSON:
{
  "body": "the SMS message text with {cta_url} where the link goes",
  "rationale": "one line explaining the angle"
}"""


def _generate_sms_copy(slot: dict, cta_url: str) -> dict:
    """Generate SMS copy via Anthropic."""
    key = os.environ.get("BEEZY_ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=key)

    audience = slot.get("audience", "?")
    aud_key = audience.lower().replace(" ", "_")
    aud_type = "HIGH_VALUE" if aud_key in HIGH_VALUE_SEGMENTS else "CUSTOMER"

    context = (
        f"Audience: {audience} ({aud_type})\n"
        f"Topic: {slot.get('topic_angle', '')}\n"
        f"CTA URL: {cta_url}\n"
        f"Discount code: {slot.get('discount_code', 'none')}\n"
        f"Date: {slot.get('date', date.today().isoformat())}"
    )

    msg = client.messages.create(
        model=MODEL, max_tokens=512, system=SMS_COPY_SYSTEM,
        messages=[{"role": "user", "content": context}],
    )
    raw = msg.content[0].text.strip()
    s, e = raw.find("{"), raw.rfind("}")
    data = json.loads(raw[s:e+1] if s != -1 else raw)

    # Insert actual CTA URL
    body = data.get("body", "")
    body = body.replace("{cta_url}", cta_url)

    # Enforce 320 char limit
    if len(body) > 320:
        body = body[:317] + "..."
        print(f"[sms_campaign] WARNING: body truncated to 320 chars")

    data["body"] = body
    return data


def _klaviyo_headers() -> dict:
    return {
        "Authorization": "Klaviyo-API-Key " + os.environ.get("KLAVIYO_API_KEY", ""),
        "revision": "2025-10-15",
        "Content-Type": "application/json",
    }


def _create_sms_campaign(slot: dict, copy: dict, segment_id: str) -> tuple[str, str]:
    """Create a Klaviyo SMS campaign. Returns (campaign_id, message_id)."""
    audience = slot.get("audience", "unknown")
    date_str = slot.get("date", date.today().isoformat())
    name = f"SMS | {audience} | {slot.get('topic_angle', '')[:30]} | {date_str}"

    payload = {
        "data": {
            "type": "campaign",
            "attributes": {
                "name": name,
                "audiences": {
                    "included": [segment_id],
                    "excluded": [],
                },
                "send_options": {"use_smart_sending": True},  # SMS uses smart sending
                "send_strategy": {
                    "method": "static",
                    "datetime": f"{date_str}T16:00:00+00:00",  # noon EDT default
                    "options": {"is_local": False},
                },
                "campaign-messages": {
                    "data": [{
                        "type": "campaign-message",
                        "attributes": {
                            "definition": {
                                "channel": "sms",
                                "content": {
                                    "body": copy["body"],
                                },
                                "render_options": {
                                    "shorten_links": True,
                                    "add_org_prefix": True,
                                    "add_info_link": False,
                                    "add_opt_out_language": True,
                                },
                            },
                        },
                    }],
                },
            },
        },
    }

    resp = httpx.post(
        "https://a.klaviyo.com/api/campaigns/",
        headers=_klaviyo_headers(),
        json=payload,
        timeout=30,
    )

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Klaviyo SMS campaign creation failed: {resp.status_code} {resp.text[:300]}")

    data = resp.json().get("data", {})
    campaign_id = data.get("id", "")
    messages = data.get("relationships", {}).get("campaign-messages", {}).get("data", [])
    message_id = messages[0]["id"] if messages else ""

    return campaign_id, message_id


def run_sms_campaign(slot: dict) -> str:
    """
    Full SMS pipeline. Called by orchestrator for sms_campaign slots.
    Returns status string for calendar_executions.
    """
    from lib.slack import post_draft, notify_failure
    from workers.auto_schedule import schedule_campaign

    audience = slot.get("audience", "unknown")
    aud_key = audience.lower().replace(" ", "_")
    segment_id = SEGMENT_IDS.get(aud_key)

    if not segment_id:
        notify_failure(source="sms_campaign", error=f"No segment ID for audience '{audience}'")
        return "failed:no_segment"

    # CTA URL
    discount_code = slot.get("discount_code", "")
    if discount_code:
        cta_url = f"https://trybeezybeez.com/discount/{discount_code}?redirect=/pages/bf-collection"
    else:
        cta_url = "https://trybeezybeez.com/pages/bf-collection"

    # Generate copy
    print(f"[sms_campaign] Generating SMS copy for {audience}...")
    try:
        copy = _generate_sms_copy(slot, cta_url)
    except Exception as e:
        notify_failure(source="sms_campaign/copy", error=str(e))
        return "failed:copy_gen"

    print(f"[sms_campaign]   Body ({len(copy['body'])} chars): {copy['body'][:80]}...")

    # Create Klaviyo campaign
    print(f"[sms_campaign] Creating Klaviyo SMS campaign...")
    try:
        campaign_id, message_id = _create_sms_campaign(slot, copy, segment_id)
    except Exception as e:
        notify_failure(source="sms_campaign/klaviyo", error=str(e))
        return "failed:klaviyo"

    print(f"[sms_campaign]   Campaign: {campaign_id}")

    # Auto-schedule
    print(f"[sms_campaign] Auto-scheduling...")
    sched = schedule_campaign(campaign_id, slot)
    sched_note = "scheduled" if sched["scheduled"] else "draft:" + sched.get("error", "")

    # Slack notify
    camp_url = f"https://www.klaviyo.com/campaign/{campaign_id}/wizard"
    post_draft(
        title=f"📱 SMS Campaign — {audience} | {slot.get('date', '')}",
        summary_lines=[
            f"*Audience:* {audience} ({segment_id})",
            f"*Body:* {copy['body'][:200]}",
            f"*CTA:* {cta_url}",
            f"*Status:* {sched_note}",
            f"<{camp_url}|Open in Klaviyo>",
        ],
        body=copy.get("rationale", ""),
    )

    print(f"[sms_campaign]   Done: {camp_url} | {sched_note}")
    return f"slack_notified|{sched_note}"
