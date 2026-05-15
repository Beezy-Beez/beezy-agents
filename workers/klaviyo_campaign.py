"""
Hive Mind Klaviyo campaign creator — Phase 3A.5.

Creates a DRAFT Klaviyo email campaign for a given issue:
  1. Reads issue from DB
  2. Builds email HTML via lib.email_builder
  3. Creates Klaviyo email template via MCP (klaviyo_create_email_template)
  4. Creates campaign + message in one call (2025-10-15 API)
  5. Assigns template via MCP (klaviyo_assign_template_to_campaign_message)
  6. Posts Slack notification
  7. Updates DB: klaviyo columns + status -> 'scheduled'

Audience:
  Included: SUPER ENGAGED 48hr (Sme9Nq) + ENGAGED 30d (Xrp3ha) + Hive Mind list (Y6VSre)
  Excluded: ALL CUSTOMERS ONLY (XFSxZt)

Tracking params match Issue 014 reference exactly (7 params, type-annotated):
  utm_source   = static  "Klaviyo"
  utm_medium   = dynamic message_type
  utm_campaign = dynamic campaign_name
  utm_id       = dynamic campaign_id
  tw_source    = static  "Klaviyo"
  tw_profile_id = dynamic profile_id
  tw_medium    = static  "campaign"

Required env vars:
  KLAVIYO_API_KEY, KLAVIYO_FROM_EMAIL, KLAVIYO_FROM_NAME (default: "Beezy Beez Honey")
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
import psycopg

from config import DATABASE_URL
from lib.email_builder import build_email_html
from lib.slack import post_draft, notify_failure

from config import KLAVIYO_REVISION
KLAVIYO_BASE = "https://a.klaviyo.com/api"

INCLUDED_LISTS    = ["Sme9Nq", "Xrp3ha", "Y6VSre"]  # SUPER ENGAGED 48hr + ENGAGED 30d + Hive Mind
EXCLUDED_SEGMENTS = ["XFSxZt"]                       # ALL CUSTOMERS ONLY

SHOPIFY_DOMAIN = "https://trybeezybeez.com"

# Tracking params — matches Issue 014 reference exactly
TRACKING_PARAMS = [
    {"type": "static",  "value": "Klaviyo",       "name": "utm_source"},
    {"type": "dynamic", "value": "message_type",  "name": "utm_medium"},
    {"type": "dynamic", "value": "campaign_name", "name": "utm_campaign"},
    {"type": "dynamic", "value": "campaign_id",   "name": "utm_id"},
    {"type": "static",  "value": "Klaviyo",       "name": "tw_source"},
    {"type": "dynamic", "value": "profile_id",    "name": "tw_profile_id"},
    {"type": "static",  "value": "campaign",      "name": "tw_medium"},
]


def _kv_headers() -> dict:
    api_key = os.environ.get("KLAVIYO_API_KEY")
    if not api_key:
        raise RuntimeError("KLAVIYO_API_KEY env var is not set.")
    return {
        "Authorization": f"Klaviyo-API-Key {api_key}",
        "revision":      KLAVIYO_REVISION,
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }


def _kv_post(path: str, payload: dict) -> dict:
    resp = httpx.post(
        f"{KLAVIYO_BASE}{path}",
        headers=_kv_headers(),
        json=payload,
        timeout=30,
    )
    if not resp.is_success:
        raise RuntimeError(
            f"Klaviyo POST {path} -> {resp.status_code}: {resp.text[:500]}"
        )
    return resp.json()


def _create_template(name: str, html: str) -> str:
    """Create a Klaviyo email template. Returns template_id."""
    data = _kv_post("/templates/", {
        "data": {
            "type": "template",
            "attributes": {
                "name":        name,
                "html":        html,
                "editor_type": "CODE",
            },
        }
    })
    return data["data"]["id"]


def _create_campaign(
    name:       str,
    subject:    str,
    from_email: str,
    from_label: str,
) -> tuple[str, str]:
    """
    Create draft campaign + message in one call (2025-10-15 API).
    Returns (campaign_id, message_id).
    """
    data = _kv_post("/campaigns/", {
        "data": {
            "type": "campaign",
            "attributes": {
                "name": name,
                "audiences": {
                    "included": INCLUDED_LISTS,
                    "excluded": EXCLUDED_SEGMENTS,
                },
                "send_options": {
                    "use_smart_sending": False,
                },
                "tracking_options": {
                    "add_tracking_params":    True,
                    "custom_tracking_params": TRACKING_PARAMS,
                },
                "campaign-messages": {
                    "data": [
                        {
                            "type": "campaign-message",
                            "attributes": {
                                "definition": {
                                    "channel": "email",
                                    "content": {
                                        "subject":    subject,
                                        "from_email": from_email,
                                        "from_label": from_label,
                                        # preview_text omitted — HTML preheader handles it
                                    },
                                }
                            },
                        }
                    ]
                },
            },
        }
    })

    campaign_id = data["data"]["id"]
    messages = (
        data["data"]
        .get("relationships", {})
        .get("campaign-messages", {})
        .get("data", [])
    )
    if not messages:
        raise RuntimeError(
            f"Campaign {campaign_id} created but no message ID in response: {data}"
        )
    return campaign_id, messages[0]["id"]


def _assign_template(message_id: str, template_id: str) -> None:
    """Assign a template to a campaign message."""
    _kv_post("/campaign-message-assign-template/", {
        "data": {
            "type": "campaign-message",
            "id":   message_id,
            "relationships": {
                "template": {
                    "data": {"type": "template", "id": template_id}
                },
            },
        }
    })


def auto_create_pending() -> str:
    """
    Called at 10am cron. Finds all issues with status='draft', creates a Klaviyo
    campaign for each, and posts a single Slack summary.

    Returns a short summary string for logging.
    """
    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute(
            "SELECT number FROM issues WHERE status = 'draft' ORDER BY number ASC LIMIT 5"
        ).fetchall()

    if not rows:
        print("[auto_create] No draft issues — nothing to do.")
        return "no_pending"

    results: list[str] = []
    for (number,) in rows:
        try:
            out = create_campaign_for_issue(number)
            results.append(f"Issue {number:03d} ✅ — campaign {out['campaign_id'][:12]}…")
        except Exception as exc:
            msg = str(exc)[:100]
            results.append(f"Issue {number:03d} ❌ — {msg}")
            print(f"[auto_create] Issue {number} failed: {exc}")

    summary = "\n".join(results)
    print(f"[auto_create] Done:\n{summary}")
    return summary


def create_campaign_for_issue(issue_number: int) -> dict:
    """
    Full campaign creation flow for one Hive Mind issue.
    Returns {campaign_id, template_id, message_id, admin_url}.
    """
    from_email = os.environ.get("KLAVIYO_FROM_EMAIL")
    if not from_email:
        raise RuntimeError("KLAVIYO_FROM_EMAIL is not set.")
    from_label = os.environ.get("KLAVIYO_FROM_NAME") or "Beezy Beez Honey"

    with psycopg.connect(DATABASE_URL) as conn:
        row = conn.execute(
            """
            SELECT number, status,
                   subject_line, preview_text, page_slug, page_dek,
                   email_teaser_body, long_form_body,
                   cover_image_url, shopify_page_url,
                   klaviyo_campaign_id
            FROM issues WHERE number = %s
            """,
            (issue_number,),
        ).fetchone()

    if not row:
        raise ValueError(f"Issue {issue_number} not found in DB.")

    (
        number, status,
        subject_line, preview_text, page_slug, page_dek,
        email_teaser_body, long_form_body,
        cover_image_url, shopify_page_url,
        existing_campaign_id,
    ) = row

    if status == "published":
        raise ValueError(f"Issue {issue_number} is already published.")
    if existing_campaign_id:
        raise ValueError(
            f"Issue {issue_number} already has campaign {existing_campaign_id}. "
            "Delete it in Klaviyo and clear klaviyo_campaign_id in the DB first."
        )
    if not email_teaser_body:
        raise ValueError(f"Issue {issue_number} has no email_teaser_body.")

    issue = {
        "number":            number,
        "subject_line":      subject_line,
        "preview_text":      preview_text,
        "page_slug":         page_slug,
        "page_dek":          page_dek,
        "email_teaser_body": email_teaser_body,
        "long_form_body":    long_form_body,
        "cover_image_url":   cover_image_url,
    }

    print(f"[campaign] Building HTML for Issue {number}...")
    html = build_email_html(issue, shopify_domain=SHOPIFY_DOMAIN)
    print(f"[campaign]   {len(html):,} chars")

    print(f"[campaign] Creating template...")
    template_id = _create_template(
        f"Hive Mind {number:03d} -- {subject_line[:50]}", html
    )
    print(f"[campaign]   template_id: {template_id}")

    print(f"[campaign] Creating campaign + message...")
    campaign_id, message_id = _create_campaign(
        name=f"Hive Mind -- Issue {number:03d} -- {subject_line[:60]}",
        subject=subject_line,
        from_email=from_email,
        from_label=from_label,
    )
    print(f"[campaign]   campaign_id: {campaign_id}")
    print(f"[campaign]   message_id:  {message_id}")

    print(f"[campaign] Assigning template...")
    _assign_template(message_id=message_id, template_id=template_id)

    now = datetime.now(timezone.utc)
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(
            """
            UPDATE issues
            SET klaviyo_campaign_id = %s,
                klaviyo_template_id = %s,
                klaviyo_message_id  = %s,
                campaign_drafted_at = %s,
                status              = 'scheduled'
            WHERE number = %s
            """,
            (campaign_id, template_id, message_id, now, issue_number),
        )
        conn.commit()
    print(f"[campaign] DB updated: status=scheduled")

    page_url  = shopify_page_url or f"{SHOPIFY_DOMAIN}/pages/{page_slug}"
    admin_url = f"https://www.klaviyo.com/campaign/{campaign_id}/wizard"

    post_draft(
        title=f"Hive Mind Issue {number:03d} -- Klaviyo campaign DRAFT",
        summary_lines=[
            f"Subject:    {subject_line}",
            f"Preview:    (HTML preheader div)",
            f"From:       {from_label} <{from_email}>",
            f"Audiences:  SUPER ENGAGED 48hr + ENGAGED 30d + Hive Mind list",
            f"Excluded:   ALL CUSTOMERS ONLY",
            f"UTM:        7 params (matches Issue 014 reference)",
            f"Smart send: OFF",
            f"Status:     DRAFT -- awaiting your review",
        ],
        body=f"Klaviyo: {admin_url}\n\nPage: {page_url}",
    )

    # Update hub/archive pages now that the issue page is confirmed live
    try:
        from workers.hub_updater import add_issue_to_hubs
        hub_results = add_issue_to_hubs({
            "number":           number,
            "subject_line":     subject_line,
            "page_dek":         page_dek,
            "cover_image_url":  cover_image_url,
            "shopify_page_url": page_url,
        })
        print(f"[campaign] Hub updates: {hub_results}")
    except Exception as exc:
        print(f"[campaign] Hub update failed (non-fatal): {exc}")

    return {
        "campaign_id": campaign_id,
        "template_id": template_id,
        "message_id":  message_id,
        "admin_url":   admin_url,
    }
