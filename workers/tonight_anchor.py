"""Tonight's Anchor — operator-triggered deployment.

Test-phase wiring only. Not registered in pacing.orchestrator.HANDLERS and
not produced by the calendar generator — operator (or a Slack command)
calls `run(...)` directly. After the 4-send test phase passes the format
will be lifted into the calendar; for now this module is the entire code
path.

Flow:
    1. kill_check()                  — abort if 4 sends complete and RPR < $0.40
    2. lib.shopify_discounts          — create / fetch the ANCHOR## code
    3. build_template_html()         — render the personal-note body
    4. _create_template()            — POST /api/templates
    5. _create_campaign()            — POST /api/campaigns with send_strategy
                                       + utm_content + 14-segment exclusions
    6. _get_campaign()                — GET it back so the validator sees the
                                        canonical Klaviyo shape
    7. lib.tonight_anchor_validator   — 11 checks; on failure DO NOT assign
                                        the template (campaign is left as a
                                        blank draft, no send occurs)
    8. _assign_template()             — POST /api/campaign-message-assign-template
    9. record_send()                  — INSERT into tonight_anchor_performance
   10. lib.slack.post_draft           — Slack summary
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from db.connection import get_conn
from lib import shopify_discounts, tonight_anchor_validator
from lib.slack import post_draft
from config import KLAVIYO_REVISION


# ─── Constants ────────────────────────────────────────────────────────────
SLOT_TYPE              = "tonight_anchor"

# "Engaged One-time Buyers" — verified live 2026-05-22 against Klaviyo (W8SW8k)
TEST_AUDIENCE_ID       = "TSpNFi"

# Target send slot. The worker DOES NOT enforce these — operator passes
# send_at_et to run() and the validator (check 4) gates the 20:00-21:00 ET
# window. These constants exist for documentation + default-time logic.
SEND_DAYS              = frozenset({"Tuesday", "Friday"})
SEND_HOUR_ET           = 20
SEND_MINUTE_ET         = 30
MIN_GAP_HOURS          = 72

TEST_PHASE_SEND_COUNT  = 4
RPR_THRESHOLD          = 0.40

DEFAULT_DISCOUNT_AMOUNT      = "20.00"
DISCOUNT_DURATION_HOURS      = 12.5
DISCOUNT_CODE_PREFIX         = "ANCHOR"
# "All TAG" collection — fixed-amount discount applies only to these items.
DISCOUNT_COLLECTION_GID      = "gid://shopify/Collection/458109157625"

TEMPLATE_NAME_PATTERN  = "Tonight's Anchor — Issue {issue_n} ({protocol})"
CAMPAIGN_NAME_PATTERN  = "{audience_display} | Tonight's Anchor — Issue {issue_n} ({protocol})"
UTM_CONTENT_PATTERN    = "tonights_anchor_{issue_n:03d}_{audience_slug}"

# 14-segment universal exclusion list. Intentionally divergent from
# workers/klaviyo_campaign.py:EXCLUDED_SEGMENTS — a follow-up PR will
# globalize this across all workers.
EXCLUDED_SEGMENTS: list[str] = [
    "TfWQTx", "RbRMPR", "UmhPWG", "WSkan5", "SQ3MuX", "TMwJHE", "YennCj",
    "RUtnZg", "T2TXFk", "ULWR2p", "UpuHSM", "VkbHQJ", "WEgpmt", "UBFUcH",
]

_ET = ZoneInfo("America/New_York")

# Klaviyo "from" defaults — match what the existing deployer uses.
_DEFAULT_FROM_EMAIL = os.environ.get("KLAVIYO_FROM_EMAIL", "alan@trybeezybeez.com")
_DEFAULT_FROM_LABEL = "Alan @ Beezy Beez"


# ─── Kill rule & counters ─────────────────────────────────────────────────
def completed_send_count(conn) -> int:
    """Number of Tonight's Anchor sends that have recorded recipients."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM tonight_anchor_performance "
            "WHERE recipients IS NOT NULL AND recipients > 0"
        )
        row = cur.fetchone()
    return int(row[0]) if row else 0


def aggregate_rpr(conn) -> float:
    """Aggregate revenue-per-recipient across all completed sends.

    Computed as SUM(revenue) / SUM(recipients) — the canonical RPR formula,
    not the average of per-send RPRs. Sends with NULL revenue / recipients
    are excluded.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(SUM(revenue), 0)::float, "
            "       COALESCE(SUM(recipients), 0)::float "
            "FROM tonight_anchor_performance "
            "WHERE recipients IS NOT NULL AND recipients > 0 "
            "  AND revenue IS NOT NULL"
        )
        revenue, recipients = cur.fetchone()
    if not recipients:
        return 0.0
    return float(revenue) / float(recipients)


def kill_check(conn) -> tuple[bool, str | None]:
    """Return (kill, reason). Kill fires only when the 4-send test phase
    is complete AND the aggregate RPR has cleared finalization (revenue
    non-null on all four rows) AND is below RPR_THRESHOLD."""
    completed = completed_send_count(conn)
    if completed < TEST_PHASE_SEND_COUNT:
        return False, None
    rpr = aggregate_rpr(conn)
    if rpr < RPR_THRESHOLD:
        return True, (
            f"Tonight's Anchor 4-send aggregate RPR is ${rpr:.2f}, below the "
            f"${RPR_THRESHOLD:.2f} continuation threshold. Format killed. "
            f"Operator override required to continue."
        )
    return False, None


def next_issue_number(conn) -> int:
    """Highest issue_number in tonight_anchor_performance + 1, or 1 if empty."""
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(issue_number), 0) FROM tonight_anchor_performance")
        return int(cur.fetchone()[0]) + 1


# ─── Template body ────────────────────────────────────────────────────────
def build_template_html(
    *, protocol_name: str, body_copy: str, discount_code: str, discount_amount: str
) -> str:
    """Render the Tonight's Anchor email body.

    Personal-note format: no images, no testimonial block, single CTA button.
    `body_copy` should already be 200–300 words (180–320 with grace) and use
    {{ person.first_name|default:'there' }} for body personalization per
    CLAUDE.md hard rule 5.
    """
    cta_url = (
        f"https://trybeezybeez.com/discount/{discount_code}"
        f"?redirect=/collections/tag"
    )
    # NOTE: do NOT introduce any <img …> tag — validator check 7 forbids it
    # during the test phase. CTA button is text-only.
    return (
        "<!doctype html><html><body style=\"margin:0;padding:0;"
        "font-family:Georgia,serif;color:#222;background:#fafaf6;\">"
        "<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" "
        "cellspacing=\"0\" style=\"max-width:560px;margin:0 auto;padding:32px 24px;\">"
        "<tr><td>"
        f"<div style=\"font-size:17px;line-height:1.6;\">{body_copy}</div>"
        "<div style=\"margin:28px 0 4px;\">"
        f"<a href=\"{cta_url}\" "
        "style=\"display:inline-block;padding:14px 24px;background:#222;"
        "color:#fff;text-decoration:none;border-radius:4px;"
        f"font-family:Georgia,serif;\">Use ${int(float(discount_amount))} off — "
        f"code {discount_code}</a>"
        "</div>"
        "<p style=\"font-size:13px;color:#666;margin-top:24px;\">"
        f"Code <b>{discount_code}</b> expires in 12.5 hours. One per customer. "
        f"Applies to the {protocol_name} collection.</p>"
        "</td></tr></table></body></html>"
    )


# ─── Klaviyo REST primitives ──────────────────────────────────────────────
def _headers() -> dict[str, str]:
    return {
        "Authorization": "Klaviyo-API-Key " + os.environ.get("KLAVIYO_API_KEY", ""),
        "revision": KLAVIYO_REVISION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _create_template(html: str, name: str) -> str:
    resp = httpx.post(
        "https://a.klaviyo.com/api/templates/",
        headers=_headers(), timeout=30,
        json={"data": {"type": "template", "attributes": {
            "name": name, "html": html, "editor_type": "CODE",
        }}},
    )
    if not resp.is_success:
        raise RuntimeError(f"Template create {resp.status_code}: {resp.text[:400]}")
    return resp.json()["data"]["id"]


def _create_campaign(
    *,
    name: str,
    subject: str,
    preview_text: str,
    from_email: str,
    from_label: str,
    audience_included: list[str],
    audience_excluded: list[str],
    send_at_utc: datetime,
    utm_content: str,
) -> tuple[str, str]:
    """Returns (campaign_id, message_id). POST shape matches the canonical
    Klaviyo response shape used by `lib.tonight_anchor_validator.validate`."""
    iso = send_at_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    tracking_params = [
        {"type": "static",  "value": "Klaviyo",       "name": "utm_source"},
        {"type": "static",  "value": "campaign",      "name": "utm_medium"},
        {"type": "dynamic", "value": "campaign_name", "name": "utm_campaign"},
        {"type": "static",  "value": utm_content,     "name": "utm_content"},
    ]
    payload = {"data": {"type": "campaign", "attributes": {
        "name": name,
        "audiences": {
            "included": audience_included,
            "excluded": audience_excluded,
        },
        "send_strategy": {
            "method": "static",
            "options_static": {
                "datetime": iso,
                "is_local": False,
                "send_past_recipients_immediately": False,
            },
        },
        "send_options": {"use_smart_sending": False},
        "tracking_options": {
            "is_tracking_opens":      True,
            "is_tracking_clicks":     True,
            "add_tracking_params":    True,
            "custom_tracking_params": tracking_params,
        },
        "campaign-messages": {"data": [{
            "type": "campaign-message",
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
        }]},
    }}}
    resp = httpx.post(
        "https://a.klaviyo.com/api/campaigns/",
        headers=_headers(), timeout=30, json=payload,
    )
    if not resp.is_success:
        raise RuntimeError(f"Campaign create {resp.status_code}: {resp.text[:600]}")
    data = resp.json()["data"]
    campaign_id = data["id"]
    msgs = data.get("relationships", {}).get("campaign-messages", {}).get("data", [])
    message_id = msgs[0]["id"] if msgs else ""
    return campaign_id, message_id


def _get_campaign(campaign_id: str) -> dict[str, Any]:
    """Fetch the canonical Klaviyo shape so the validator sees what Klaviyo
    actually stored (e.g. send_strategy.options_static, audiences as flat lists)."""
    resp = httpx.get(
        f"https://a.klaviyo.com/api/campaigns/{campaign_id}/",
        params={"include": "campaign-messages"},
        headers=_headers(), timeout=30,
    )
    if not resp.is_success:
        raise RuntimeError(f"Campaign get {resp.status_code}: {resp.text[:400]}")
    body = resp.json()
    attrs = body["data"]["attributes"]
    # Re-attach campaign-messages.data with their definitions so the validator
    # can read the subject. Klaviyo returns them in `included`, keyed by type.
    messages = []
    for item in body.get("included") or []:
        if item.get("type") == "campaign-message":
            messages.append(item)
    attrs["campaign-messages"] = {"data": messages}
    attrs["name"] = attrs.get("name") or ""
    return attrs


def _assign_template(message_id: str, template_id: str) -> None:
    resp = httpx.post(
        "https://a.klaviyo.com/api/campaign-message-assign-template/",
        headers=_headers(), timeout=30,
        json={"data": {
            "type": "campaign-message",
            "id":   message_id,
            "relationships": {
                "template": {"data": {"type": "template", "id": template_id}},
            },
        }},
    )
    if not resp.is_success:
        raise RuntimeError(f"Assign template {resp.status_code}: {resp.text[:400]}")


# ─── Persistence ──────────────────────────────────────────────────────────
def record_send(
    conn,
    *,
    issue_number: int,
    campaign_id: str,
    template_id: str | None,
    discount_code: str,
    discount_amount: str,
    audience_id: str,
    sent_at: datetime,
) -> None:
    """UPSERT a row in tonight_anchor_performance. Metrics (recipients,
    opens, revenue, etc.) are NULL initially and backfilled separately."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tonight_anchor_performance
                (issue_number, campaign_id, template_id, discount_code,
                 discount_amount, audience_id, sent_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (issue_number) DO UPDATE SET
                campaign_id    = EXCLUDED.campaign_id,
                template_id    = EXCLUDED.template_id,
                discount_code  = EXCLUDED.discount_code,
                discount_amount= EXCLUDED.discount_amount,
                audience_id    = EXCLUDED.audience_id,
                sent_at        = EXCLUDED.sent_at,
                last_synced_at = NOW()
            """,
            (issue_number, campaign_id, template_id, discount_code,
             discount_amount, audience_id, sent_at),
        )
    conn.commit()


# ─── Entry point ──────────────────────────────────────────────────────────
def run(
    *,
    issue_number: int,
    protocol_name: str,
    body_copy: str,
    subject: str,
    preview_text: str,
    send_at_et: datetime,
    audience_display: str = "Engaged One-Time Buyers",
    audience_slug:    str = "engaged_otb",
    discount_amount:  str = DEFAULT_DISCOUNT_AMOUNT,
    discount_code:    str | None = None,
    dry_run:          bool = False,
) -> dict[str, Any]:
    """Operator-triggered Tonight's Anchor build.

    `send_at_et` is honored verbatim — the worker does NOT round it to the
    target SEND_HOUR_ET:SEND_MINUTE_ET. Operator may pass 8:15pm, 8:30pm,
    8:45pm, etc. Validator check 4 gates the 20:00–21:00 ET window.

    Returns either:
        {"campaign_id", "template_id", "discount_code", "send_at_utc"}  — success
        {"killed":   reason}                                           — kill rule fired
        {"blocked":  [reasons]}                                        — validator failed
                                                                          (no template assigned;
                                                                           campaign is a blank draft)
    """
    if send_at_et.tzinfo is None:
        send_at_et = send_at_et.replace(tzinfo=_ET)
    send_at_utc = send_at_et.astimezone(timezone.utc)

    with get_conn() as conn:
        kill, reason = kill_check(conn)
        if kill:
            post_draft(
                title="Tonight's Anchor — KILLED",
                summary_lines=[
                    f"Issue:           {issue_number}",
                    f"Test phase RPR:  ${aggregate_rpr(conn):.2f}",
                    f"Threshold:       ${RPR_THRESHOLD:.2f}",
                ],
                body=reason or "(kill condition fired)",
            )
            return {"killed": reason}

    discount = shopify_discounts.create_anchor_discount(
        amount=discount_amount,
        starts_at=send_at_utc,
        ends_at=send_at_utc + timedelta(hours=DISCOUNT_DURATION_HOURS),
        collection_gid=DISCOUNT_COLLECTION_GID,
        issue_number=issue_number,
        discount_code=discount_code,
    )

    template_html = build_template_html(
        protocol_name=protocol_name,
        body_copy=body_copy,
        discount_code=discount["code"],
        discount_amount=discount_amount,
    )

    template_name = TEMPLATE_NAME_PATTERN.format(
        issue_n=issue_number, protocol=protocol_name,
    )
    campaign_name = CAMPAIGN_NAME_PATTERN.format(
        audience_display=audience_display,
        issue_n=issue_number,
        protocol=protocol_name,
    )
    utm_content = UTM_CONTENT_PATTERN.format(
        issue_n=issue_number, audience_slug=audience_slug,
    )

    if dry_run:
        return {
            "dry_run":      True,
            "template_html": template_html,
            "campaign_name": campaign_name,
            "template_name": template_name,
            "discount":      discount,
            "send_at_utc":   send_at_utc.isoformat(),
            "utm_content":   utm_content,
        }

    template_id = _create_template(template_html, template_name)
    campaign_id, message_id = _create_campaign(
        name=campaign_name,
        subject=subject,
        preview_text=preview_text,
        from_email=_DEFAULT_FROM_EMAIL,
        from_label=_DEFAULT_FROM_LABEL,
        audience_included=[TEST_AUDIENCE_ID],
        audience_excluded=EXCLUDED_SEGMENTS,
        send_at_utc=send_at_utc,
        utm_content=utm_content,
    )

    campaign = _get_campaign(campaign_id)
    passed, reasons = tonight_anchor_validator.validate(
        campaign=campaign,
        template_html=template_html,
        discount=discount,
        issue_number=issue_number,
    )
    if not passed:
        post_draft(
            title="Tonight's Anchor — VALIDATOR BLOCKED",
            summary_lines=[
                f"Issue:    {issue_number}",
                f"Campaign: {campaign_id}",
                f"Template: {template_id} (NOT assigned)",
            ],
            body="```\n" + "\n".join(reasons) + "\n```",
        )
        return {"blocked": reasons, "campaign_id": campaign_id, "template_id": template_id}

    _assign_template(message_id, template_id)

    with get_conn() as conn:
        record_send(
            conn,
            issue_number=issue_number,
            campaign_id=campaign_id,
            template_id=template_id,
            discount_code=discount["code"],
            discount_amount=discount_amount,
            audience_id=TEST_AUDIENCE_ID,
            sent_at=send_at_utc,
        )

    post_draft(
        title=f"Tonight's Anchor — Issue {issue_number} built",
        summary_lines=[
            f"Audience:   {audience_display} ({TEST_AUDIENCE_ID})",
            f"Send:       {send_at_et.strftime('%a %b %d %Y %-I:%M %p')} ET",
            f"Discount:   {discount['code']} (${int(float(discount_amount))} off, 12.5h)",
        ],
        body=(
            f"Campaign: https://www.klaviyo.com/campaign/{campaign_id}/wizard\n"
            f"Template: {template_id}\n"
            f"UTM:      {utm_content}\n"
        ),
    )

    return {
        "campaign_id":   campaign_id,
        "template_id":   template_id,
        "discount_code": discount["code"],
        "send_at_utc":   send_at_utc.isoformat(),
    }
