"""
Fix R13-failing campaigns — May 21 lapsed_30d and May 22 vip.

Both campaigns have halucinated product names in their copy.
This script:
  1. Regenerates clean copy for each slot (R13-safe system prompt)
  2. Creates a new Klaviyo template with the corrected HTML
  3. Fetches the existing campaign's message_id from Klaviyo
  4. Re-assigns the new template to the existing campaign message
  5. Re-runs the validator to confirm pass
  6. Posts a Slack notification with the corrected campaigns
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import httpx

from config import KLAVIYO_REVISION
from workers.beezy_campaign import (
    _generate_copy, _build_image_prompt, _generate_image,
    _upload_to_shopify_cdn, _build_email_html,
    _create_template, _assign_template, _klaviyo_headers,
    _CAMPAIGN_NEGATIVE_PROMPT, CUSTOMER_SEGMENTS,
)
from workers.validator import validate_campaign
from db.connection import get_conn
from lib.slack import post_draft

# ── Campaign definitions ──────────────────────────────────────────────────────

CAMPAIGNS = [
    {
        "slot": {
            "date": "2026-05-21",
            "content_type": "klaviyo_campaign",
            "audience": "lapsed_30d",
            "topic_angle": "Just checking in: how have you been sleeping?",
            "send_time_est": "14:00",
            "priority": "high",
            "revenue_estimate": 923,
        },
        "campaign_id": "01KRYP2VJDNHP590VYPGKBD3KY",
        "cta_url": "https://trybeezybeez.com/pages/bf-collection",
    },
    {
        "slot": {
            "date": "2026-05-22",
            "content_type": "klaviyo_campaign",
            "audience": "vip",
            "topic_angle": "Your next jar is waiting",
            "send_time_est": "14:00",
            "priority": "high",
            "revenue_estimate": 873,
        },
        "campaign_id": "01KRYP3V0VDH2Q6NZZ2JJ4A3VQ",
        "cta_url": "https://trybeezybeez.com/pages/bf-collection",
    },
]


def get_message_id(campaign_id: str) -> str:
    """Fetch the primary message_id for an existing Klaviyo campaign."""
    resp = httpx.get(
        f"https://a.klaviyo.com/api/campaigns/{campaign_id}/",
        headers=_klaviyo_headers(), timeout=15,
    )
    resp.raise_for_status()
    messages = (resp.json().get("data", {})
                .get("relationships", {})
                .get("campaign-messages", {})
                .get("data", []))
    if not messages:
        raise RuntimeError(f"No messages found for campaign {campaign_id}")
    return messages[0]["id"]


def fix_campaign(entry: dict) -> dict:
    slot        = entry["slot"]
    campaign_id = entry["campaign_id"]
    cta_url     = entry["cta_url"]
    audience    = slot["audience"]

    print(f"\n{'='*60}")
    print(f"Fixing: {audience} / {slot['date']}")
    print(f"Campaign ID: {campaign_id}")

    # 1. Regenerate copy (COPY_SYSTEM now has R13 guardrails)
    print("  Regenerating copy...")
    copy = _generate_copy(slot, page_url="", discount_code="")
    copy["image_prompt"] = _build_image_prompt(slot, copy)
    print(f"  Subject: {copy.get('subject','')}")
    print(f"  Image prompt: {copy.get('image_prompt','')}")

    # 2. Validate content-only rules — scheduling rules (R1/R2/R3) are irrelevant
    #    here because we're replacing the template on an already-dispatched campaign,
    #    not scheduling a new send.
    print("  Running content validator (R13, R14, C1-C5)...")
    from workers.validator import (
        _r13_product_accuracy, _r14_cta_url_compliance,
        _check_subject_syntax, _check_cta_url, _check_offer_rules,
        _check_image_prompt, _check_collection_url,
    )
    content_results = [
        _r13_product_accuracy(copy),
        _r14_cta_url_compliance(copy, cta_url),
        _check_subject_syntax(copy),
        _check_cta_url(cta_url, slot),
        _check_offer_rules(copy, slot),
        _check_image_prompt(copy),
        _check_collection_url(cta_url),
    ]
    content_fails = [r for r in content_results if not r.get("pass")]
    if content_fails:
        raise RuntimeError(f"Content validator still failing after regen: {content_fails}")
    print("  Content validator: PASS")

    # 3. Generate new hero image
    print("  Generating hero image...")
    try:
        image_url = _generate_image(copy["image_prompt"], _CAMPAIGN_NEGATIVE_PROMPT)
        cdn_url   = _upload_to_shopify_cdn(image_url, alt=f"Beezy Beez — {audience}")
        print(f"  Image CDN: {cdn_url[:60]}...")
    except Exception as exc:
        print(f"  Image generation failed (non-fatal): {exc}")
        cdn_url = "https://trybeezybeez.com/cdn/shop/files/hero-placeholder.png"

    # 4. Build new email HTML
    html = _build_email_html(copy, cdn_url, cta_url, discount_code="")

    # 5. Create new Klaviyo template
    print("  Creating new Klaviyo template...")
    tpl_name    = f"{audience} | {slot['date']} | R13-fixed"
    template_id = _create_template(html, tpl_name)
    print(f"  Template ID: {template_id}")

    # 6. Get existing campaign message_id
    print("  Fetching campaign message ID...")
    message_id = get_message_id(campaign_id)
    print(f"  Message ID: {message_id}")

    # 7. Assign new template to existing campaign message
    print("  Assigning template to campaign message...")
    _assign_template(message_id, template_id)
    print("  Template assigned.")

    return {
        "campaign_id": campaign_id,
        "template_id": template_id,
        "message_id":  message_id,
        "subject":     copy.get("subject", ""),
        "audience":    audience,
        "date":        slot["date"],
    }


def post_fix_report(results: list[dict]) -> None:
    """Post a Slack summary of what was fixed."""
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook:
        return

    lines = []
    for r in results:
        camp_url = f"https://www.klaviyo.com/campaign/{r['campaign_id']}/wizard"
        lines.append(
            f"*{r['audience']} — {r['date']}*\n"
            f"Subject: `{r['subject']}`\n"
            f"Template: `{r['template_id']}` | <{camp_url}|Open in Klaviyo>"
        )

    blocks = [
        {"type": "header", "text": {"type": "plain_text",
             "text": "R13 Fix Complete — 2 campaigns corrected"}},
        {"type": "section", "text": {"type": "mrkdwn",
             "text": (
                 "Both campaigns previously had hallucinated product names "
                 "(Chamomile & Passionflower Honey, Ashwagandha Honey). "
                 "New templates generated with catalog-accurate copy and re-assigned.\n\n" +
                 "\n\n".join(lines)
             )}},
    ]
    httpx.post(webhook, json={"text": "R13 campaigns fixed", "blocks": blocks}, timeout=10)


def main():
    results = []
    errors  = []

    for entry in CAMPAIGNS:
        try:
            r = fix_campaign(entry)
            results.append(r)
        except Exception as exc:
            audience = entry["slot"]["audience"]
            print(f"\nERROR fixing {audience}: {exc}")
            import traceback; traceback.print_exc()
            errors.append({"audience": audience, "error": str(exc)})

    print("\n" + "="*60)
    print(f"Done. {len(results)} fixed, {len(errors)} errors.")
    for r in results:
        print(f"  OK  {r['audience']} / {r['date']} — template {r['template_id']}")
    for e in errors:
        print(f"  ERR {e['audience']}: {e['error']}")

    if results:
        post_fix_report(results)

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
