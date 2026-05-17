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


def _create_page_for_issue(issue_number: int) -> dict:
    """
    Create a Shopify page (isPublished=False) for a Hive Mind issue that has all
    content but no Shopify page yet.  Mirrors scripts/publish_page.py main() but
    callable programmatically.

    Returns {page_id, page_handle, page_url, image_url}.
    Raises on any failure — caller logs and continues.
    """
    from workers.shopify_page_builder import build_page_html
    from workers.shopify_publisher import create_page, upload_image_to_shopify
    from datetime import datetime, timezone

    with psycopg.connect(DATABASE_URL) as conn:
        row = conn.execute(
            """
            SELECT number, page_title, page_dek, page_breadcrumb_label,
                   page_slug, long_form_body, until_next_teaser, read_time_min,
                   cover_image_url, shopify_image_id, shopify_image_url,
                   preview_text, buzzsprout_url
            FROM issues WHERE number = %s
            """,
            (issue_number,),
        ).fetchone()

    if not row:
        raise ValueError(f"Issue {issue_number} not found in DB.")

    (
        number, page_title, page_dek, page_breadcrumb_label,
        page_slug, long_form_body, until_next_teaser, read_time_min,
        cover_image_url, shopify_image_id, shopify_image_url,
        preview_text, buzzsprout_url,
    ) = row

    _local_vals = {
        "page_title": page_title, "page_dek": page_dek,
        "page_slug": page_slug, "long_form_body": long_form_body,
        "cover_image_url": cover_image_url,
    }
    missing = [f for f, v in _local_vals.items() if not v]
    if missing:
        raise ValueError(f"Issue {issue_number} missing required fields for page: {missing}")

    issue_dict = {
        "number":                number,
        "page_title":            page_title,
        "page_dek":              page_dek,
        "page_breadcrumb_label": page_breadcrumb_label or "",
        "page_slug":             page_slug,
        "long_form_body":        long_form_body,
        "until_next_teaser":     until_next_teaser or "",
        "read_time_min":         read_time_min,
        "cover_image_url":       cover_image_url,
        "shopify_image_url":     shopify_image_url,
        "buzzsprout_url":        buzzsprout_url or "",
    }

    if shopify_image_id and shopify_image_url:
        print(f"[page_create] Issue {number}: reusing existing Shopify image {shopify_image_id}")
        image_info = {"id": shopify_image_id, "url": shopify_image_url}
    else:
        alt = f"The Hive Mind Issue {number:03d} — {page_breadcrumb_label or page_title}"
        print(f"[page_create] Issue {number}: uploading cover image to Shopify CDN...")
        image_info = upload_image_to_shopify(cover_image_url, alt=alt)
        print(f"[page_create] Issue {number}: image_id={image_info['id']}")
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute(
                "UPDATE issues SET shopify_image_id = %s, shopify_image_url = %s WHERE number = %s",
                (image_info["id"], image_info["url"], number),
            )

    issue_dict["shopify_image_url"] = image_info["url"]

    print(f"[page_create] Issue {number}: building page HTML...")
    body_html = build_page_html(issue_dict)

    print(f"[page_create] Issue {number}: creating Shopify page (isPublished=False)...")
    page_info = create_page(
        title=page_title,
        body_html=body_html,
        handle=page_slug,
        seo_title=page_title,
        seo_description=preview_text,
        is_published=False,
        image_file_id=image_info["id"],
    )
    print(f"[page_create] Issue {number}: page_id={page_info['id']}  url={page_info['url']}")

    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(
            """
            UPDATE issues SET
                shopify_page_id     = %s,
                shopify_page_handle = %s,
                shopify_page_url    = %s
            WHERE number = %s
            """,
            (page_info["id"], page_info["handle"], page_info["url"], number),
        )

    return {
        "page_id":   page_info["id"],
        "page_url":  page_info["url"],
        "image_url": image_info["url"],
    }


def auto_create_pending() -> str:
    """
    Called at 10am cron.

    Phase 1 — create Shopify pages (isPublished=False) for draft issues that have
    all required content but no shopify_page_id yet.

    Phase 2 — create Klaviyo DRAFT campaigns for draft issues that now have a
    shopify_page_id but no klaviyo_campaign_id.

    Returns a short summary string for logging.
    """
    # ── Phase 1: create missing Shopify pages ──────────────────────────────
    with psycopg.connect(DATABASE_URL) as conn:
        page_rows = conn.execute(
            """
            SELECT number FROM issues
            WHERE status = 'draft'
              AND shopify_page_id IS NULL
              AND page_title IS NOT NULL
              AND long_form_body IS NOT NULL
              AND cover_image_url IS NOT NULL
            ORDER BY number ASC LIMIT 5
            """
        ).fetchall()

    page_results: list[str] = []
    for (number,) in page_rows:
        try:
            out = _create_page_for_issue(number)
            page_results.append(f"Issue {number:03d} page ✅ — {out['page_url']}")
        except Exception as exc:
            msg = str(exc)[:120]
            page_results.append(f"Issue {number:03d} page ❌ — {msg}")
            print(f"[auto_create] Issue {number} page creation failed: {exc}")
            notify_failure(source=f"auto_create/page/{number}", error=str(exc))

    if page_results:
        print("[auto_create] Page creation:\n" + "\n".join(page_results))

    # ── Phase 2: create Klaviyo campaigns for issues with pages ────────────
    with psycopg.connect(DATABASE_URL) as conn:
        campaign_rows = conn.execute(
            "SELECT number FROM issues WHERE status = 'draft' AND shopify_page_id IS NOT NULL AND klaviyo_campaign_id IS NULL ORDER BY number ASC LIMIT 5"
        ).fetchall()

    if not campaign_rows:
        if not page_results:
            print("[auto_create] No pending issues — nothing to do.")
            return "no_pending"
        return "\n".join(page_results)

    campaign_results: list[str] = []
    for (number,) in campaign_rows:
        try:
            out = create_campaign_for_issue(number)
            campaign_results.append(f"Issue {number:03d} campaign ✅ — {out['campaign_id'][:12]}…")
        except Exception as exc:
            msg = str(exc)[:100]
            campaign_results.append(f"Issue {number:03d} campaign ❌ — {msg}")
            print(f"[auto_create] Issue {number} campaign failed: {exc}")

    all_results = page_results + campaign_results
    summary = "\n".join(all_results)
    print(f"[auto_create] Done:\n{summary}")
    return summary


def create_campaign_for_issue(issue_number: int) -> dict:
    """
    Full campaign creation flow for one Hive Mind issue.

    Ensures a Shopify page exists first (creates isPublished=False if missing),
    creates the Klaviyo DRAFT campaign, then updates both hub index pages.

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
                   cover_image_url, shopify_image_url,
                   shopify_page_id, shopify_page_url,
                   klaviyo_campaign_id,
                   pillar, read_time_min,
                   page_title
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
        cover_image_url, shopify_image_url,
        shopify_page_id, shopify_page_url,
        existing_campaign_id,
        pillar, read_time_min,
        page_title,
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

    # ── Ensure Shopify page exists (create as Hidden if missing) ──────────────
    if not shopify_page_id:
        print(f"[campaign] Issue {number}: no page yet — creating now (isPublished=False)...")
        try:
            page_result   = _create_page_for_issue(issue_number)
            shopify_page_url  = page_result["page_url"]
            shopify_image_url = page_result.get("image_url") or shopify_image_url
            print(f"[campaign] Issue {number}: page created — {shopify_page_url}")
        except Exception as exc:
            print(f"[campaign] Issue {number}: page creation failed (continuing): {exc}")

    page_url = shopify_page_url or f"{SHOPIFY_DOMAIN}/pages/{page_slug}"

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
        f"Hive Mind {number:03d}", html
    )
    print(f"[campaign]   template_id: {template_id}")

    print(f"[campaign] Creating campaign + message...")
    campaign_id, message_id = _create_campaign(
        name=f"Hive Mind -- Issue {number:03d}",
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

    admin_url = f"https://www.klaviyo.com/campaign/{campaign_id}/wizard"

    post_draft(
        title=f"Hive Mind Issue {number:03d} -- Klaviyo campaign DRAFT",
        summary_lines=[
            f"Subject:    {subject_line}",
            f"Preview:    (HTML preheader div)",
            f"From:       {from_label} <{from_email}>",
            f"Audiences:  SUPER ENGAGED 48hr + ENGAGED 30d + Hive Mind list",
            f"Excluded:   ALL CUSTOMERS ONLY",
            f"Smart send: OFF",
            f"Status:     DRAFT -- awaiting your review",
            f"Klaviyo:    {admin_url}",
            f"Page:       {page_url}",
        ],
        body=f"Klaviyo: {admin_url}\n\nPage: {page_url}",
    )

    # ── Update hub index pages ────────────────────────────────────────────────
    # add_issue_to_hubs rebuilds /pages/the-hive-mind AND updates the
    # "Latest Issue" featured box on /pages/sleep-science-hub.
    try:
        from workers.hub_updater import add_issue_to_hubs
        issue_data = {
            "number":            number,
            "subject_line":      subject_line,
            "page_title":        page_title or subject_line,
            "page_dek":          page_dek,
            "cover_image_url":   cover_image_url,
            "shopify_image_url": shopify_image_url or "",
            "shopify_page_url":  page_url,
            "pillar":            pillar or "",
            "read_time_min":     read_time_min,
        }
        hub_results = add_issue_to_hubs(issue_data)
        print(f"[campaign] Hub update: {hub_results}")
    except Exception as exc:
        print(f"[campaign] Hub update failed (non-fatal): {exc}")

    return {
        "campaign_id": campaign_id,
        "template_id": template_id,
        "message_id":  message_id,
        "admin_url":   admin_url,
    }
