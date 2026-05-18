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
from config import KLAVIYO_REVISION

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
        "revision": KLAVIYO_REVISION,
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


def _episode_subjects(title: str, episode_type: str) -> tuple[str, str]:
    """
    Returns (subject_a, subject_b) for A/B testing episode emails.
    subject_a = curiosity-led, subject_b = benefit-led.
    Chooses which to send first based on subject_patterns in agent_state.
    """
    _CURIOSITY = {
        "sleep_story":        f"Tonight: {title}",
        "guided_meditation":  f"5 minutes could change tonight — {title}",
        "affirmation_meditation": f"What if you woke up feeling different? — {title}",
        "morning_meditation": f"Start tomorrow right — {title}",
        "soundscape":         f"Something new for tonight — {title}",
    }
    _BENEFIT = {
        "sleep_story":        f"New sleep story for members: {title}",
        "guided_meditation":  f"New guided meditation: {title}",
        "affirmation_meditation": f"New affirmation session: {title}",
        "morning_meditation": f"New morning session: {title}",
        "soundscape":         f"New soundscape: {title}",
    }
    a = _CURIOSITY.get(episode_type, f"Tonight: {title}")
    b = _BENEFIT.get(episode_type, f"New sleep audio: {title}")
    return a, b


def _pick_episode_subject(audience_key: str, subj_a: str, subj_b: str) -> tuple[str, str]:
    """
    Alternates curiosity / benefit based on subject_patterns in agent_state.
    Returns (subject_to_send, other_subject).
    """
    try:
        from db.connection import get_conn
        with get_conn() as c:
            row = c.execute(
                "SELECT value FROM agent_state WHERE key='subject_patterns'"
            ).fetchone()
        patterns = json.loads(row[0]) if row else {}
    except Exception:
        patterns = {}
    last_used = patterns.get(audience_key, {}).get("last_used", "benefit")
    if last_used == "curiosity":
        return subj_b, subj_a   # this time: benefit
    return subj_a, subj_b       # this time: curiosity (default)


def _record_episode_subject(audience_key: str, used: str) -> None:
    """Persist which subject variant was sent for this audience."""
    try:
        from db.connection import get_conn
        with get_conn() as c:
            row = c.execute("SELECT value FROM agent_state WHERE key='subject_patterns'").fetchone()
            patterns = json.loads(row[0]) if row else {}
            if audience_key not in patterns:
                patterns[audience_key] = {}
            patterns[audience_key]["last_used"] = used
            c.execute(
                "INSERT INTO agent_state (key, value, updated_at) VALUES ('subject_patterns', %s, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                (json.dumps(patterns),)
            )
            c.commit()
    except Exception as exc:
        print("[deployer] subject_patterns update failed (non-fatal): " + str(exc))


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

    # A/B subject selection
    subj_a, subj_b = _episode_subjects(title, episode_type)
    subj_a_send, subj_a_alt = _pick_episode_subject("episode_engaged_customers_" + episode_type, subj_a, subj_b)
    subj_b_send, subj_b_alt = _pick_episode_subject("episode_active_seal_" + episode_type, subj_a, subj_b)

    # Email A — Engaged Customers excl Active Seal at 8pm ET
    camp_a_url = deploy_campaign(
        html=email_a_html,
        name=title + " | Engaged Customers | " + metadata.get("suggested_send_date",""),
        subject=subj_a_send,
        from_email=from_email,
        from_label="Beezy Beez",
        segment_ids=["RvtHdn"],
        excluded_ids=["UBFUcH"],
    )
    _record_episode_subject(
        "episode_engaged_customers_" + episode_type,
        "curiosity" if subj_a_send == subj_a else "benefit"
    )

    # Email B — Active Seal at 8:15pm ET
    camp_b_url = deploy_campaign(
        html=email_b_html,
        name=title + " | Active Seal | " + metadata.get("suggested_send_date",""),
        subject=subj_b_send,
        from_email=from_email,
        from_label="Beezy Beez",
        segment_ids=["UBFUcH"],
    )
    _record_episode_subject(
        "episode_active_seal_" + episode_type,
        "curiosity" if subj_b_send == subj_a else "benefit"
    )

    # Persist episode to DB for hub rebuild and historical record
    # URL format: https://www.klaviyo.com/campaign/{ID}/wizard — take the segment before /wizard
    def _extract_campaign_id(url: str) -> str | None:
        if not url:
            return None
        parts = [p for p in url.rstrip("/").split("/") if p and p != "wizard"]
        return parts[-1] if parts else None

    _camp_a_id = _extract_campaign_id(camp_a_url)
    _camp_b_id = _extract_campaign_id(camp_b_url)
    try:
        from db.connection import get_conn
        with get_conn() as _conn:
            _conn.execute(
                """INSERT INTO episodes
                   (episode_id, title, episode_type, buzzsprout_url, shopify_page_url,
                    cover_image_url, duration_minutes, suggested_send_date,
                    klaviyo_campaign_id_a, klaviyo_campaign_id_b)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (episode_id) DO UPDATE SET
                     buzzsprout_url        = EXCLUDED.buzzsprout_url,
                     klaviyo_campaign_id_a = EXCLUDED.klaviyo_campaign_id_a,
                     klaviyo_campaign_id_b = EXCLUDED.klaviyo_campaign_id_b,
                     deployed_at           = NOW()""",
                (
                    metadata.get("episode_id"),
                    title,
                    episode_type,
                    metadata.get("buzzsprout_url"),
                    page_url,
                    metadata.get("cover_image_url"),
                    metadata.get("duration_minutes"),
                    metadata.get("suggested_send_date"),
                    _camp_a_id,
                    _camp_b_id,
                )
            )
            _conn.commit()
        print("[deployer] Episode saved to DB")
    except Exception as exc:
        print("[deployer] Episode DB save failed (non-fatal): " + str(exc))

    # Update hub/archive pages with the new episode card
    try:
        from workers.hub_updater import add_episode_to_hubs
        hub_results = add_episode_to_hubs(metadata)
        print("[deployer] Hub updates: " + str(hub_results))
    except Exception as exc:
        print("[deployer] Hub update failed (non-fatal): " + str(exc))

    # Inject audio embed into the existing Shopify episode page
    buzzsprout_url = metadata.get("buzzsprout_url", "")
    if buzzsprout_url:
        try:
            _update_episode_page_audio(title, buzzsprout_url)
        except Exception as exc:
            print("[deployer] Page audio update failed (non-fatal): " + str(exc))

    return "Email A: " + camp_a_url + "\nEmail B: " + camp_b_url


def _update_episode_page_audio(title: str, buzzsprout_url: str) -> None:
    """Find the Shopify episode page by handle and replace the audio placeholder with the real embed."""
    import re as _re
    from lib.shopify_admin import graphql
    from workers.shopify_publisher import update_page

    handle = "episode-" + _re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    query = """
    query($q: String!) {
      pages(first: 1, query: $q) {
        edges { node { id title body } }
      }
    }
    """
    data = graphql(query, {"q": f"handle:{handle}"})
    edges = (data.get("pages") or {}).get("edges") or []
    if not edges:
        print(f"[deployer] Page not found for handle: {handle}")
        return

    node     = edges[0]["node"]
    page_id  = node["id"]
    body_html = node.get("body") or ""

    # Build the embed iframe.
    # buzzsprout_url may be the MP3 download URL:
    #   https://www.buzzsprout.com/2292260/episodes/19196112-slug.mp3
    # Buzzsprout's iframe player requires the short form:
    #   https://www.buzzsprout.com/2292260/19196112
    m = _re.search(r"buzzsprout\.com/(\d+)/episodes/(\d+)", buzzsprout_url)
    if m:
        embed_src = f"https://www.buzzsprout.com/{m.group(1)}/{m.group(2)}?client_source=large_player&iframe=true"
    else:
        sep = "&" if "?" in buzzsprout_url else "?"
        embed_src = f"{buzzsprout_url}{sep}client_source=large_player&iframe=true"
    iframe = (
        f'<iframe src="{embed_src}" loading="lazy" frameborder="0" '
        f'scrolling="no" title="Beezy Beez Sleep Story: {title}"></iframe>'
    )

    placeholder = (
        '<p style="font-size:16px;color:#6b5947;font-style:italic;">'
        "Audio coming shortly — bookmark this page or return from your email link.</p>"
    )

    if placeholder in body_html:
        new_body = body_html.replace(placeholder, iframe)
    elif "Audio coming shortly" in body_html:
        # Looser match in case of minor HTML variation
        new_body = _re.sub(
            r'<p[^>]*>Audio coming shortly[^<]*</p>',
            iframe,
            body_html,
        )
    else:
        # Embed not already present — append before closing body tag or end
        new_body = body_html + "\n" + iframe
        print(f"[deployer] Placeholder not found — appending embed to page")

    update_page(page_id, title=title, body_html=new_body)
    print(f"[deployer] Episode page updated with audio embed: /pages/{handle}")


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
