#!/usr/bin/env bash
# install_phase3a5.sh
# Phase 3A.5 — Klaviyo email campaign worker
#
# Installs:
#   db/migrations/005_klaviyo_columns.sql  — adds klaviyo_ columns to issues table
#   lib/email_builder.py                   — email_teaser_body markdown → email HTML
#   workers/klaviyo_campaign.py            — creates Klaviyo campaign + notifies Slack
#   scripts/draft_campaign.py              — CLI: python -m scripts.draft_campaign --issue N
#
# Required Replit Secrets (add before running live):
#   KLAVIYO_API_KEY    — private API key (already used by ingestion)
#   KLAVIYO_FROM_EMAIL — verified sending address, e.g. hello@trybeezybeez.com
#   KLAVIYO_FROM_NAME  — defaults to "The Hive Mind" if not set
#
# Usage after install:
#   python -m scripts.draft_campaign --issue 15 --dry-run   # preview email HTML
#   python -m scripts.draft_campaign --issue 15             # create Klaviyo draft + Slack

set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d workers ]] || [[ ! -d lib ]] || [[ ! -f config.py ]]; then
    echo "FATAL: run from the beezy-agents workspace root" >&2
    exit 1
fi

mkdir -p db/migrations scripts

# ---------- 1. DB migration ----------

cat > db/migrations/005_klaviyo_columns.sql <<'SQLEOF'
-- Phase 3A.5: Klaviyo campaign tracking columns
ALTER TABLE issues
  ADD COLUMN IF NOT EXISTS klaviyo_campaign_id   TEXT,
  ADD COLUMN IF NOT EXISTS klaviyo_template_id   TEXT,
  ADD COLUMN IF NOT EXISTS klaviyo_message_id    TEXT,
  ADD COLUMN IF NOT EXISTS campaign_drafted_at   TIMESTAMPTZ;
SQLEOF
echo "[install] db/migrations/005_klaviyo_columns.sql written"

# Apply migration
python <<'PYEOF'
import psycopg
from config import DATABASE_URL
from pathlib import Path

sql = Path("db/migrations/005_klaviyo_columns.sql").read_text()
with psycopg.connect(DATABASE_URL) as conn:
    conn.execute(sql)
    conn.commit()
print("[install] migration 005 applied")
PYEOF

# ---------- 2. email_builder.py ----------

cat > lib/email_builder.py <<'PYEOF'
"""
Email HTML builder for Hive Mind issues.

Converts email_teaser_body markdown to email-client-safe HTML and wraps it
in the Hive Mind email template. All styles are inline for maximum
email client compatibility.

CANONICAL SPEC: /mnt/skills/user/hive-mind-page-template/SKILL.md
"""
from __future__ import annotations

import re

SHOPIFY_DOMAIN = "https://trybeezybeez.com"


def _inline_format(text: str) -> str:
    """Convert **bold** and *italic* markdown inline elements to HTML."""
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*]+?)\*", r"<em>\1</em>", text)
    return text


def _build_body_html(body_md: str) -> str:
    """Convert email_teaser_body markdown paragraphs to email-safe HTML."""
    paras = body_md.strip().split("\n\n")
    parts = []
    for para in paras:
        para = para.strip()
        if not para:
            continue
        # Skip the CTA marker line (we replace it with a button)
        if re.match(r"^\*\*Continue reading", para) or "Continue reading on the page" in para:
            continue
        # H2 section header
        if para.startswith("## "):
            text = _inline_format(para[3:].strip())
            parts.append(
                f'<h2 style="font-size:22px;font-family:Georgia,serif;'
                f'color:#2c2417;margin:28px 0 12px;font-weight:bold;'
                f'line-height:1.3;">{text}</h2>'
            )
        # Pullquote (> prefix)
        elif para.startswith("> "):
            text = _inline_format(para[2:].strip())
            parts.append(
                f'<p style="font-size:20px;font-family:Georgia,serif;'
                f'color:#5a4a3a;font-style:italic;text-align:center;'
                f'margin:28px 0;padding:20px 0;border-top:1px solid #e8dcc8;'
                f'border-bottom:1px solid #e8dcc8;">{text}</p>'
            )
        # Regular paragraph
        else:
            text = _inline_format(para.replace("\n", " "))
            parts.append(
                f'<p style="font-size:18px;font-family:Georgia,serif;'
                f'color:#2c2417;line-height:1.75;margin:0 0 18px;">{text}</p>'
            )
    return "\n".join(parts)


def build_email_html(issue: dict, shopify_domain: str = SHOPIFY_DOMAIN) -> str:
    """
    Build full email-client-safe HTML for a Hive Mind issue.

    Required keys in issue dict:
        number, subject_line, page_slug, email_teaser_body
    """
    issue_num = int(issue.get("number") or 0)
    subject = issue.get("subject_line") or ""
    page_slug = (issue.get("page_slug") or "").strip()
    body_md = (issue.get("email_teaser_body") or "").strip()

    cta_url = (
        f"{shopify_domain}/pages/{page_slug}"
        f"?s=1"
        f"&utm_source=klaviyo"
        f"&utm_medium=email"
        f"&utm_campaign=hive-mind-{issue_num:03d}"
        f"&utm_content=teaser"
    )

    body_html = _build_body_html(body_md)
    issue_label = f"The Hive Mind &middot; Issue {issue_num:03d}"

    # Klaviyo unsubscribe template variable — inserted as a literal string value,
    # not via f-string braces, so the {{ }} are preserved verbatim in the output.
    unsub_url = "{{ organization.unsubscribe_link }}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{subject}</title>
</head>
<body style="margin:0;padding:0;background-color:#faf6ee;">
<table role="presentation" cellpadding="0" cellspacing="0" width="100%"
  style="background-color:#faf6ee;">
<tr>
<td align="center" style="padding:40px 20px;">
<table role="presentation" cellpadding="0" cellspacing="0"
  style="max-width:600px;width:100%;background-color:#fffdf7;border-radius:8px;">

<tr>
<td style="padding:32px 40px 0;text-align:center;">
  <p style="font-size:12px;font-family:Georgia,serif;color:#8b7355;
    letter-spacing:2px;text-transform:uppercase;margin:0;">{issue_label}</p>
</td>
</tr>

<tr>
<td style="padding:12px 40px 0;">
  <table cellpadding="0" cellspacing="0" width="100%">
  <tr><td style="border-top:2px solid #d4a847;font-size:0;">&nbsp;</td></tr>
  </table>
</td>
</tr>

<tr>
<td style="padding:28px 40px 8px;">
{body_html}
</td>
</tr>

<tr>
<td style="padding:8px 40px 40px;text-align:center;">
  <a href="{cta_url}"
    style="display:inline-block;padding:14px 36px;background-color:#8b4513;
    color:#fffdf7;text-decoration:none;font-family:Georgia,serif;font-size:16px;
    font-weight:bold;border-radius:4px;letter-spacing:0.5px;">
    Continue reading &rarr;
  </a>
</td>
</tr>

<tr>
<td style="padding:24px 40px;border-top:1px solid #e8dcc8;text-align:center;
  background-color:#f5f0e8;border-radius:0 0 8px 8px;">
  <p style="font-size:13px;font-family:Georgia,serif;color:#8b7355;margin:0 0 6px;">
    Beezy Beez &mdash; Botanical Extract Honey
  </p>
  <p style="font-size:12px;font-family:Georgia,serif;color:#b0a090;margin:0;">
    <a href="{unsub_url}" style="color:#b0a090;text-decoration:underline;">Unsubscribe</a>
    &middot;
    <a href="{shopify_domain}" style="color:#b0a090;">trybeezybeez.com</a>
  </p>
</td>
</tr>

</table>
</td>
</tr>
</table>
</body>
</html>"""
PYEOF
echo "[install] lib/email_builder.py written ($(wc -l < lib/email_builder.py) lines)"

# ---------- 3. klaviyo_campaign.py ----------

cat > workers/klaviyo_campaign.py <<'PYEOF'
"""
Hive Mind Klaviyo campaign creator — Phase 3A.5.

Creates a DRAFT Klaviyo email campaign for a given issue:
  1. Reads issue from DB
  2. Builds email HTML via lib.email_builder
  3. Creates Klaviyo email template
  4. Creates Klaviyo campaign (Smart Sending OFF, UTM tagged)
  5. Updates campaign message with subject / preview / from
  6. Assigns template to message
  7. Posts Slack notification with admin URL
  8. Updates DB: klaviyo columns + status -> 'scheduled'

Audience config (hardcoded — edit here if segments change):
  Included:  SUPER ENGAGED Prospects 48hr (Sme9Nq)
             ENGAGED Prospects 30d       (Xrp3ha)
  Excluded:  ALL CUSTOMERS ONLY          (XFSxZt)

Required env vars:
  KLAVIYO_API_KEY     — private API key
  KLAVIYO_FROM_EMAIL  — verified sender address
  KLAVIYO_FROM_NAME   — display name (default: "The Hive Mind")

CANONICAL SPEC: /mnt/skills/user/hive-mind-page-template/SKILL.md
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

import httpx
import psycopg

from config import DATABASE_URL
from lib.email_builder import build_email_html
from lib.slack import post_draft, notify_failure

# ── Klaviyo REST settings ────────────────────────────────────────────────────
KLAVIYO_BASE = "https://a.klaviyo.com/api"
KLAVIYO_REVISION = "2024-10-15"

# ── Audience segments ────────────────────────────────────────────────────────
INCLUDED_SEGMENTS = ["Sme9Nq", "Xrp3ha"]   # SUPER ENGAGED 48hr + ENGAGED 30d
EXCLUDED_SEGMENTS = ["XFSxZt"]             # ALL CUSTOMERS ONLY

SHOPIFY_DOMAIN = "https://trybeezybeez.com"


# ── Klaviyo API helpers ──────────────────────────────────────────────────────

def _kv_headers() -> dict:
    api_key = os.environ.get("KLAVIYO_API_KEY")
    if not api_key:
        raise RuntimeError("KLAVIYO_API_KEY env var is not set.")
    return {
        "Authorization": f"Klaviyo-API-Key {api_key}",
        "revision": KLAVIYO_REVISION,
        "Content-Type": "application/json",
        "Accept": "application/json",
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
            f"Klaviyo {path} returned {resp.status_code}: {resp.text[:500]}"
        )
    return resp.json()


def _kv_patch(path: str, payload: dict) -> dict:
    resp = httpx.patch(
        f"{KLAVIYO_BASE}{path}",
        headers=_kv_headers(),
        json=payload,
        timeout=30,
    )
    if not resp.is_success:
        raise RuntimeError(
            f"Klaviyo PATCH {path} returned {resp.status_code}: {resp.text[:500]}"
        )
    return resp.json()


def _create_template(name: str, html: str) -> str:
    """Create a Klaviyo email template. Returns template_id."""
    data = _kv_post("/templates/", {
        "data": {
            "type": "template",
            "attributes": {"name": name, "html": html},
        }
    })
    return data["data"]["id"]


def _create_campaign(name: str, utm_campaign: str) -> tuple[str, str]:
    """Create a draft email campaign. Returns (campaign_id, message_id)."""
    data = _kv_post("/campaigns/", {
        "data": {
            "type": "campaign",
            "attributes": {
                "name": name,
                "channel": "email",
                "audiences": {
                    "included": INCLUDED_SEGMENTS,
                    "excluded": EXCLUDED_SEGMENTS,
                },
                "send_options": {"use_smart_sending": False},
                "tracking_options": {
                    "add_tracking_params": True,
                    "custom_tracking_params": [
                        {"name": "utm_source",   "value": "klaviyo"},
                        {"name": "utm_medium",   "value": "email"},
                        {"name": "utm_campaign", "value": utm_campaign},
                        {"name": "utm_content",  "value": "teaser"},
                    ],
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
            f"Campaign {campaign_id} created but no message ID in response. "
            f"Full response: {data}"
        )
    message_id = messages[0]["id"]
    return campaign_id, message_id


def _update_message(
    message_id: str,
    subject: str,
    preview_text: str,
    from_email: str,
    from_label: str,
) -> None:
    """Update campaign message with subject, preview text, and sender."""
    _kv_patch(f"/campaign-messages/{message_id}/", {
        "data": {
            "type": "campaign-message",
            "id": message_id,
            "attributes": {
                "definition": {
                    "channel": "email",
                    "content": {
                        "subject":      subject,
                        "preview_text": preview_text,
                        "from_email":   from_email,
                        "from_label":   from_label,
                    },
                },
            },
        }
    })


def _assign_template(message_id: str, template_id: str) -> None:
    """Assign a template to a campaign message."""
    _kv_post("/campaign-message-assign-template/", {
        "data": {
            "type": "campaign-message-assign-template",
            "attributes": {"method": "html"},
            "relationships": {
                "campaign-message": {
                    "data": {"id": message_id, "type": "campaign-message"}
                },
                "template": {
                    "data": {"id": template_id, "type": "template"}
                },
            },
        }
    })


# ── Main entry point ─────────────────────────────────────────────────────────

def create_campaign_for_issue(issue_number: int) -> dict:
    """
    Full campaign creation flow for one Hive Mind issue.
    Returns summary dict: {campaign_id, template_id, message_id, admin_url}.
    Raises on any error.
    """
    # Validate required env vars before doing any work
    from_email = os.environ.get("KLAVIYO_FROM_EMAIL")
    if not from_email:
        raise RuntimeError(
            "KLAVIYO_FROM_EMAIL is not set in Replit Secrets. "
            "Add it (e.g. hello@trybeezybeez.com) before running."
        )
    from_label = os.environ.get("KLAVIYO_FROM_NAME") or "The Hive Mind"

    # Pull issue from DB
    with psycopg.connect(DATABASE_URL) as conn:
        row = conn.execute(
            """
            SELECT number, status,
                   subject_line, preview_text, page_slug,
                   email_teaser_body, shopify_page_url,
                   klaviyo_campaign_id
            FROM issues WHERE number = %s
            """,
            (issue_number,),
        ).fetchone()

    if not row:
        raise ValueError(f"Issue {issue_number} not found in DB.")

    (
        number, status,
        subject_line, preview_text, page_slug,
        email_teaser_body, shopify_page_url,
        existing_campaign_id,
    ) = row

    issue = {
        "number": number,
        "subject_line": subject_line,
        "preview_text": preview_text,
        "page_slug": page_slug,
        "email_teaser_body": email_teaser_body,
    }

    if status == "published":
        raise ValueError(
            f"Issue {issue_number} is already published. Refusing to create campaign."
        )
    if existing_campaign_id:
        raise ValueError(
            f"Issue {issue_number} already has campaign {existing_campaign_id}. "
            "Delete it in Klaviyo or clear klaviyo_campaign_id in the DB to re-create."
        )
    if not email_teaser_body:
        raise ValueError(
            f"Issue {issue_number} has no email_teaser_body. Draft it first."
        )

    print(f"[campaign] Building email HTML for Issue {number}...")
    html = build_email_html(issue, shopify_domain=SHOPIFY_DOMAIN)
    print(f"[campaign]   HTML: {len(html):,} chars")

    print(f"[campaign] Creating Klaviyo template...")
    template_name = f"Hive Mind {number:03d} — {subject_line[:50]}"
    template_id = _create_template(template_name, html)
    print(f"[campaign]   template_id: {template_id}")

    print(f"[campaign] Creating Klaviyo campaign...")
    campaign_name = f"Hive Mind — Issue {number:03d} — {subject_line[:60]}"
    utm_campaign  = f"hive-mind-{number:03d}"
    campaign_id, message_id = _create_campaign(campaign_name, utm_campaign)
    print(f"[campaign]   campaign_id: {campaign_id}")
    print(f"[campaign]   message_id:  {message_id}")

    print(f"[campaign] Updating message (subject / preview / from)...")
    _update_message(
        message_id=message_id,
        subject=subject_line,
        preview_text=preview_text or "",
        from_email=from_email,
        from_label=from_label,
    )

    print(f"[campaign] Assigning template to message...")
    _assign_template(message_id=message_id, template_id=template_id)

    # Persist to DB
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
    print(f"[campaign] DB updated: status=scheduled, campaign_id saved")

    # Slack notification
    page_url   = shopify_page_url or f"{SHOPIFY_DOMAIN}/pages/{page_slug}"
    admin_url  = f"https://www.klaviyo.com/campaign/{campaign_id}/wizard"

    post_draft(
        title=f"📧 Hive Mind Issue {number:03d} — Klaviyo campaign DRAFT",
        summary_lines=[
            f"Subject:    {subject_line}",
            f"Preview:    {preview_text or '(none)'}",
            f"From:       {from_label} <{from_email}>",
            f"Audiences:  SUPER ENGAGED 48hr ({INCLUDED_SEGMENTS[0]}) + ENGAGED 30d ({INCLUDED_SEGMENTS[1]})",
            f"Excluded:   ALL CUSTOMERS ONLY ({EXCLUDED_SEGMENTS[0]})",
            f"Smart send: OFF",
            f"Status:     DRAFT — awaiting your review",
        ],
        body=(
            f"Review in Klaviyo:\n{admin_url}\n\n"
            f"Page (flip Visible before sending):\n{page_url}"
        ),
    )
    print(f"[campaign] Posted to Slack")

    return {
        "campaign_id": campaign_id,
        "template_id": template_id,
        "message_id":  message_id,
        "admin_url":   admin_url,
    }
PYEOF
echo "[install] workers/klaviyo_campaign.py written ($(wc -l < workers/klaviyo_campaign.py) lines)"

# ---------- 4. draft_campaign.py ----------

cat > scripts/draft_campaign.py <<'PYEOF'
"""
CLI for Phase 3A.5 — Klaviyo campaign drafter.

Usage:
    python -m scripts.draft_campaign --issue N [--dry-run]

--dry-run: builds and prints the email HTML without calling Klaviyo.
           Useful to eyeball the email before creating the draft.

After a successful run:
  - Klaviyo campaign is in DRAFT status
  - DB issue row: status='scheduled', klaviyo_campaign_id set
  - Slack notification posted with admin link
  - Flip the Shopify page to Visible, then schedule/send from Klaviyo
"""
from __future__ import annotations

import argparse
import sys

import psycopg

from config import DATABASE_URL
from lib.email_builder import build_email_html
from workers.klaviyo_campaign import create_campaign_for_issue


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draft Klaviyo campaign for a Hive Mind issue.")
    parser.add_argument("--issue", type=int, required=True, help="Issue number")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build email HTML and print stats only. No Klaviyo API calls.")
    args = parser.parse_args(argv)

    if args.dry_run:
        with psycopg.connect(DATABASE_URL) as conn:
            row = conn.execute(
                "SELECT number, subject_line, preview_text, page_slug, email_teaser_body "
                "FROM issues WHERE number = %s",
                (args.issue,),
            ).fetchone()
        if not row:
            print(f"Issue {args.issue} not found in DB.", file=sys.stderr)
            return 1

        issue = dict(zip(
            ["number", "subject_line", "preview_text", "page_slug", "email_teaser_body"],
            row,
        ))
        html = build_email_html(issue)

        print(f"Issue:         {issue['number']:03d}")
        print(f"Subject:       {issue['subject_line']}")
        print(f"Preview:       {issue['preview_text']}")
        print(f"Email HTML:    {len(html):,} chars")
        print(f"CTA URL slug:  {issue['page_slug']}?s=1&utm_...")
        print()
        print("--- FIRST 800 CHARS OF EMAIL HTML ---")
        print(html[:800])
        print()
        print("DRY-RUN — no Klaviyo calls made.")
        return 0

    print(f"Creating Klaviyo campaign for Issue {args.issue}...")
    try:
        result = create_campaign_for_issue(args.issue)
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print()
    print("=" * 60)
    print("DONE")
    print(f"Campaign ID:  {result['campaign_id']}")
    print(f"Template ID:  {result['template_id']}")
    print(f"Message ID:   {result['message_id']}")
    print(f"Admin URL:    {result['admin_url']}")
    print()
    print("Next steps:")
    print("  1. Flip Issue page to Visible in Shopify")
    print("  2. Update /pages/sleep-science-hub and /pages/the-hive-mind indexes")
    print("  3. Schedule or send the campaign from Klaviyo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
PYEOF
echo "[install] scripts/draft_campaign.py written ($(wc -l < scripts/draft_campaign.py) lines)"

# ---------- 5. Syntax checks ----------

echo "[install] syntax checks..."
python -c "import ast; ast.parse(open('lib/email_builder.py').read()); print('  lib/email_builder.py OK')"
python -c "import ast; ast.parse(open('workers/klaviyo_campaign.py').read()); print('  workers/klaviyo_campaign.py OK')"
python -c "import ast; ast.parse(open('scripts/draft_campaign.py').read()); print('  scripts/draft_campaign.py OK')"

echo ""
echo "[install] DONE. Phase 3A.5 installed."
echo ""
echo "Before going live, set these in Replit Secrets:"
echo "  KLAVIYO_FROM_EMAIL  — e.g. hello@trybeezybeez.com (must be verified in Klaviyo)"
echo "  KLAVIYO_FROM_NAME   — e.g. The Hive Mind  (optional, defaults to 'The Hive Mind')"
echo ""
echo "Test with a dry-run first (no Klaviyo API calls, just shows the email HTML):"
echo "  python -m scripts.draft_campaign --issue 15 --dry-run"
echo ""
echo "When ready to create the actual draft in Klaviyo:"
echo "  python -m scripts.draft_campaign --issue 15"
