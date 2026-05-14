"""
Standalone Klaviyo deployer — correct REST endpoints, confirmed May 2026.

Used by slack_agent.py to deploy both:
  1. Regular beezy campaigns (from #beezy-agents payloads)
  2. Sleep audio episodes (from #beezy-new-episodes metadata)

REST endpoint reference (all confirmed working):
  Templates:   POST /api/templates/              {editor_type: "CODE"}
  Campaigns:   POST /api/campaigns/              {send_options: {use_smart_sending: false}}
  Assignment:  POST /api/campaign-message-assign-template/  {type: "campaign-message", id, relationships.template}
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import httpx

TRACKING_PARAMS = [
    {"type": "static",  "value": "Klaviyo",       "name": "utm_source"},
    {"type": "static",  "value": "campaign",      "name": "utm_medium"},
    {"type": "dynamic", "value": "campaign_name", "name": "utm_campaign"},
    {"type": "dynamic", "value": "campaign_id",   "name": "utm_id"},
    {"type": "static",  "value": "Klaviyo",       "name": "tw_source"},
    {"type": "dynamic", "value": "profile_id",    "name": "tw_profile_id"},
    {"type": "static",  "value": "campaign",      "name": "tw_medium"},
]


def _headers() -> dict:
    return {
        "Authorization": "Klaviyo-API-Key " + os.environ.get("KLAVIYO_API_KEY", ""),
        "revision": "2025-10-15",
        "Content-Type": "application/json",
    }


def create_template(html: str, name: str) -> str:
    resp = httpx.post(
        "https://a.klaviyo.com/api/templates/",
        headers=_headers(), timeout=30,
        json={"data": {"type": "template", "attributes": {
            "name": name, "html": html, "editor_type": "CODE"
        }}},
    )
    if not resp.is_success:
        raise RuntimeError("Template create " + str(resp.status_code) + ": " + resp.text[:400])
    return resp.json()["data"]["id"]


def create_campaign(name: str, subject: str, from_email: str, from_label: str,
                    segment_ids: list[str], excluded_ids: list[str] | None = None) -> tuple[str, str]:
    """Returns (campaign_id, message_id)."""
    payload = {"data": {"type": "campaign", "attributes": {
        "name": name,
        "audiences": {
            "included": segment_ids,
            "excluded": excluded_ids or [],
        },
        "send_options": {"use_smart_sending": False},
        "tracking_options": {
            "is_tracking_opens": True,
            "is_tracking_clicks": True,
            "add_tracking_params": True,
            "custom_tracking_params": TRACKING_PARAMS,
        },
        "campaign-messages": {"data": [{"type": "campaign-message", "attributes": {
            "definition": {
                "channel": "email",
                "content": {
                    "subject":      subject,
                    "preview_text": "",
                    "from_email":   from_email,
                    "from_label":   from_label,
                },
            },
        }}]},
    }}}
    resp = httpx.post(
        "https://a.klaviyo.com/api/campaigns/",
        headers=_headers(), timeout=30, json=payload,
    )
    if not resp.is_success:
        raise RuntimeError("Campaign create " + str(resp.status_code) + ": " + resp.text[:400])
    data       = resp.json()["data"]
    campaign_id = data["id"]
    messages   = data.get("relationships", {}).get("campaign-messages", {}).get("data", [])
    message_id = messages[0]["id"] if messages else ""
    return campaign_id, message_id


def assign_template(message_id: str, template_id: str) -> None:
    """POST /api/campaign-message-assign-template/ — confirmed endpoint."""
    resp = httpx.post(
        "https://a.klaviyo.com/api/campaign-message-assign-template/",
        headers=_headers(), timeout=30,
        json={"data": {
            "type": "campaign-message",
            "id": message_id,
            "relationships": {
                "template": {"data": {"type": "template", "id": template_id}},
            },
        }},
    )
    if not resp.is_success:
        raise RuntimeError("Assign template " + str(resp.status_code) + ": " + resp.text[:400])


def deploy_campaign(html: str, name: str, subject: str, from_email: str,
                    from_label: str, segment_ids: list[str],
                    excluded_ids: list[str] | None = None) -> str:
    """Full pipeline: template → campaign → assign. Returns campaign URL."""
    tpl_id = create_template(html, name)
    camp_id, msg_id = create_campaign(
        name, subject, from_email, from_label, segment_ids, excluded_ids
    )
    if msg_id:
        assign_template(msg_id, tpl_id)
    return "https://www.klaviyo.com/campaign/" + camp_id + "/wizard"


def deploy_episode(metadata: dict, conn=None) -> str:
    """
    Deploy a sleep audio episode from #beezy-new-episodes metadata.
    Creates two campaigns: Email A (engaged customers) + Email B (active seal).
    Returns status string.
    """
    from lib.email_builder_episode import build_episode_emails

    title        = metadata.get("title", "")
    episode_type = metadata.get("episode_type", "sleep_story")
    page_url     = metadata.get("shopify_page_url") or metadata.get("buzzsprout_url", "")
    from_email   = os.environ.get("KLAVIYO_FROM_EMAIL", "help@trybeezybeez.com")

    email_a_html, email_b_html = build_episode_emails(metadata, page_url)

    # Email A — Engaged Customers excl Active Seal at 8pm ET
    camp_a_url = deploy_campaign(
        html=email_a_html,
        name=title + " | Engaged Customers | " + metadata.get("suggested_send_date",""),
        subject="Tonight on Sleep Better: " + title,
        from_email=from_email,
        from_label="Beezy Beez",
        segment_ids=["RvtHdn"],
        excluded_ids=["UBFUcH"],
    )

    # Email B — Active Seal at 8:15pm ET
    camp_b_url = deploy_campaign(
        html=email_b_html,
        name=title + " | Active Seal | " + metadata.get("suggested_send_date",""),
        subject="New sleep audio for members: " + title,
        from_email=from_email,
        from_label="Beezy Beez",
        segment_ids=["UBFUcH"],
    )

    return "Email A: " + camp_a_url + "\nEmail B: " + camp_b_url


def deploy_episode_from_slack(conn) -> str:
    """Read latest episode metadata from #beezy-new-episodes and deploy."""
    import httpx as _httpx
    token   = os.environ.get("SLACK_BOT_TOKEN","")
    channel = os.environ.get("NEW_EPISODES_CHANNEL_ID", "")
    if not channel:
        return "NEW_EPISODES_CHANNEL_ID not set in Secrets."
    resp = _httpx.get(
        "https://slack.com/api/conversations.history",
        headers={"Authorization": "Bearer " + token},
        params={"channel": channel, "limit": 10},
        timeout=15,
    )
    messages = resp.json().get("messages", [])
    for msg in messages:
        text = msg.get("text","")
        if '"episode_id"' in text:
            s, e = text.find("{"), text.rfind("}")
            if s != -1:
                metadata = json.loads(text[s:e+1])
                return deploy_episode(metadata, conn)
    return "No unprocessed episodes found in #beezy-new-episodes."
