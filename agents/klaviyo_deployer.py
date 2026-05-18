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

    title          = metadata.get("title", "")
    episode_type   = metadata.get("episode_type", "sleep_story")
    buzzsprout_url = metadata.get("buzzsprout_url", "")
    from_email     = os.environ.get("KLAVIYO_FROM_EMAIL", "help@trybeezybeez.com")

    # Block: Buzzsprout URL must be confirmed before creating campaigns.
    # The Shopify page CTA points to the episode page, which must have a working
    # audio embed before we drive traffic to it via email.
    if not buzzsprout_url:
        msg = f"[deployer] BLOCKED — no buzzsprout_url for '{title}'. Upload audio to Buzzsprout first."
        print(msg)
        return msg

    # Enrich cover_image_url from DB if the Slack metadata payload didn't include it.
    # sleep_audio_producer generates the image in phase 1 and stores it in episodes;
    # sleep-audio-platform's Slack post only contains what it was given at dispatch time.
    metadata = dict(metadata)
    if not metadata.get("cover_image_url"):
        ep_id = metadata.get("episode_id", "")
        if ep_id:
            try:
                from db.connection import get_conn
                with get_conn() as _c:
                    row = _c.execute(
                        "SELECT cover_image_url FROM episodes WHERE episode_id = %s", (ep_id,)
                    ).fetchone()
                if row and row[0]:
                    metadata["cover_image_url"] = row[0]
                    print(f"[deployer] cover_image_url enriched from DB: {row[0][:70]}")
            except Exception as exc:
                print(f"[deployer] DB cover_image lookup failed (non-fatal): {exc}")

    # Generate and upload a cover image if still missing.
    if not metadata.get("cover_image_url"):
        try:
            from workers.image_gen import generate_cover
            from workers.episode_deployer import _episode_image_prompt, _NEGATIVE_PROMPT
            prompt = _episode_image_prompt({
                "episode_type": episode_type,
                "title": title,
                "description_short": metadata.get("description_short", ""),
            })
            cover_url = generate_cover(prompt, negative_prompt=_NEGATIVE_PROMPT).url
            from workers.shopify_publisher import upload_image_to_shopify
            cdn_url = upload_image_to_shopify(cover_url, alt=title).get("url", cover_url)
            metadata["cover_image_url"] = cdn_url
            print(f"[deployer] Cover image generated + uploaded: {cdn_url[:70]}")
        except Exception as exc:
            print(f"[deployer] Image generation failed (non-fatal): {exc}")

    page_url = metadata.get("shopify_page_url") or buzzsprout_url

    # Update the Shopify episode page with the real audio embed BEFORE building
    # email HTML — the CTA drives recipients to this page, so audio must be live first.
    try:
        _update_episode_page_audio(title, buzzsprout_url, episode_type)
    except Exception as exc:
        print(f"[deployer] Page audio update failed (non-fatal): {exc}")

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

    # Page audio embed is updated at the top of deploy_episode (before email build).
    # This second call is a no-op guard — skipped when embed_src already present.
    if buzzsprout_url:
        try:
            _update_episode_page_audio(title, buzzsprout_url, episode_type)
        except Exception as exc:
            print("[deployer] Page audio update failed (non-fatal): " + str(exc))

    return "Email A: " + camp_a_url + "\nEmail B: " + camp_b_url


_STORY_TYPES = {"sleep_story", "soundscape"}


def _update_episode_page_audio(title: str, buzzsprout_url: str, episode_type: str = "sleep_story") -> None:
    """Find the Shopify episode page by handle and replace the audio placeholder with the real embed.

    Uses small_player for meditations (matches _page_html_meditation's iframe format).
    Uses large_player for sleep stories / soundscapes (matches _page_html_story format).
    Matches both placeholder variants written by the two page builders.
    """
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

    node      = edges[0]["node"]
    page_id   = node["id"]
    body_html = node.get("body") or ""

    # Build embed src — episode MP3 URLs contain the episode ID.
    # https://www.buzzsprout.com/2292260/episodes/19196112-slug.mp3
    # → player: https://www.buzzsprout.com/2292260/19196112?client_source=<player>&iframe=true
    m = _re.search(r"buzzsprout\.com/(\d+)/episodes/(\d+)", buzzsprout_url)
    if m:
        base = f"https://www.buzzsprout.com/{m.group(1)}/{m.group(2)}"
    else:
        base = buzzsprout_url.split("?")[0]

    is_story  = episode_type in _STORY_TYPES
    player    = "large_player" if is_story else "small_player"
    embed_src = f"{base}?client_source={player}&iframe=true"

    if is_story:
        iframe = (
            f'<iframe src="{embed_src}" loading="lazy" frameborder="0" '
            f'scrolling="no" title="Beezy Beez Sleep Story: {title}"></iframe>'
        )
    else:
        iframe = (
            f'<iframe src="{embed_src}" loading="lazy" width="100%" height="200" '
            f'frameborder="0" scrolling="no" '
            f'title="Sleep Better Podcast - {_EPISODE_LABELS.get(episode_type, "Episode")} - {title}"></iframe>'
        )

    # Match either placeholder variant:
    #   _page_html_story:      "Audio coming shortly — bookmark this page or return from your email link."
    #   _page_html_meditation: "Audio coming soon — bookmark this page or return from your email link."
    if _re.search(r'Audio coming s(?:hortly|oon)', body_html):
        new_body = _re.sub(
            r'<p[^>]*>Audio coming s(?:hortly|oon)[^<]*</p>',
            iframe,
            body_html,
        )
        print(f"[deployer] Replaced audio placeholder in epis-audio section")
    elif embed_src in body_html:
        print(f"[deployer] Audio embed already present — skipping")
        return
    else:
        new_body = body_html + "\n" + iframe
        print(f"[deployer] Placeholder not found — appending embed to end of page")

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
