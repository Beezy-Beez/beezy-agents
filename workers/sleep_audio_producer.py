"""
Sleep Audio Producer — orchestrator handler for calendar sleep_audio slots.

Full automatic pipeline:
  1. invoke_skill("sleep_audio")    → script + title + description + cover_image_prompt
  2. Higgsfield image generation
  3. Shopify CDN upload
  4. Shopify landing page (published, audio embed added later by sleep-audio-platform)
  5. episodes DB stub + hub pages
  6a. If SLEEP_AUDIO_API_URL is set: POST script to sleep-audio-platform cloud API
       → TTSPipeline runs in Replit → Buzzsprout upload → #beezy-new-episodes posted
       → watcher creates Klaviyo campaigns + updates page with audio player
  6b. Fallback (no API URL configured): Slack handoff with full script for Boris to run manually
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date

import httpx

from workers.skill_runner import invoke_skill
from lib.slack import post_draft

_SAP_URL = os.environ.get("SLEEP_AUDIO_API_URL", "").rstrip("/")   # sleep-audio-platform Replit URL
_SAP_KEY = os.environ.get("SLEEP_AUDIO_API_KEY", "")

_SHOPIFY_DOMAIN = "https://trybeezybeez.com"

_EPISODE_LABELS = {
    "sleep_story":            "Sleep Story",
    "guided_meditation":      "Guided Meditation",
    "affirmation_meditation": "Affirmation Meditation",
    "morning_meditation":     "Morning Meditation",
    "soundscape":             "Sleep Soundscape",
}

def _slug(title: str) -> str:
    return "episode-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _save_stub_and_update_hubs(episode_meta: dict) -> str:
    """Save episode stub to DB and update hub pages. Returns status string.

    Klaviyo campaigns are deliberately NOT created here — they're created by
    the #beezy-new-episodes watcher (agents/klaviyo_deployer.deploy_episode)
    once Boris uploads the audio and the Buzzsprout URL is available.
    """
    status_parts = []

    try:
        from db.connection import get_conn
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO episodes
                   (episode_id, title, episode_type, shopify_page_url,
                    cover_image_url, duration_minutes, suggested_send_date)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (episode_id) DO NOTHING""",
                (
                    episode_meta.get("episode_id"),
                    episode_meta.get("title"),
                    episode_meta.get("episode_type"),
                    episode_meta.get("shopify_page_url"),
                    episode_meta.get("cover_image_url"),
                    episode_meta.get("duration_minutes"),
                    episode_meta.get("suggested_send_date"),
                )
            )
            conn.commit()
        print(f"[sleep_audio] Episode stub saved to DB: {episode_meta.get('episode_id')}")
        status_parts.append("db:ok")
    except Exception as exc:
        print(f"[sleep_audio] DB stub save failed (non-fatal): {exc}")
        status_parts.append(f"db:error")

    try:
        from workers.hub_updater import add_episode_to_hubs
        hub_results = add_episode_to_hubs(episode_meta)
        print(f"[sleep_audio] Hub pages updated: {hub_results}")
        status_parts.append("hubs:ok")
    except Exception as exc:
        print(f"[sleep_audio] Hub update failed (non-fatal): {exc}")
        status_parts.append("hubs:error")

    return "pending_audio | " + " ".join(status_parts)


def run_sleep_audio_slot(slot: dict) -> str:
    """Full automatic pipeline for a calendar sleep_audio slot.
    Returns a status string written to calendar_executions.notes.
    """
    from lib.dryrun import is_dry_run
    if is_dry_run():
        topic = slot.get("topic_angle", "?")
        print(f"[sleep_audio/DRY RUN] would generate episode: {topic!r}")
        return "dry-run:sleep_audio_skipped"

    topic        = slot.get("topic_angle", "")
    episode_type = slot.get("episode_type", "sleep_story")
    duration     = int(slot.get("duration_minutes", 25))
    slot_date    = slot.get("date", date.today().isoformat())

    # ── 1. Generate script ────────────────────────────────────────────────────
    print(f"[sleep_audio] Generating script — topic: {topic!r}")
    skill_result = invoke_skill("sleep_audio", {
        "topic":            topic,
        "episode_type":     episode_type,
        "duration_minutes": duration,
        "tone_notes":       slot.get("tone_notes", ""),
    })
    meta              = skill_result.output_json or {}
    title             = meta.get("title") or topic
    description_short = (meta.get("description_short") or meta.get("description", "")[:150]).strip()
    description_long  = (meta.get("description_long") or meta.get("description", "")).strip()
    script            = meta.get("script", "")
    episode_type      = meta.get("episode_type", episode_type)
    duration          = int(meta.get("duration_minutes", duration))
    print(f"[sleep_audio] Script ready — {len(script):,} chars — '{title}'")

    # ── 2. Cover image ────────────────────────────────────────────────────────
    cover_url = ""
    try:
        from workers.image_gen import generate_cover
        from workers.episode_deployer import _episode_image_prompt, _NEGATIVE_PROMPT
        image_prompt = _episode_image_prompt({
            "episode_type": episode_type,
            "title": title,
            "description_short": description_short,
        })
        print(f"[sleep_audio] Image prompt: {image_prompt!r}")
        print(f"[sleep_audio] Negative prompt: {_NEGATIVE_PROMPT!r}")
        cover_url = generate_cover(image_prompt, negative_prompt=_NEGATIVE_PROMPT).url
        print(f"[sleep_audio] Cover generated: {cover_url[:70]}...")
    except Exception as exc:
        print(f"[sleep_audio] Image gen failed (non-fatal): {exc}")

    # ── 3. Shopify CDN upload ─────────────────────────────────────────────────
    cdn_url = cover_url
    if cover_url:
        try:
            from workers.shopify_publisher import upload_image_to_shopify
            cdn_url = upload_image_to_shopify(cover_url, alt=title).get("url", cover_url)
            print(f"[sleep_audio] CDN: {cdn_url[:70]}...")
        except Exception as exc:
            print(f"[sleep_audio] CDN upload failed (non-fatal): {exc}")

    # ── 4. Shopify landing page ───────────────────────────────────────────────
    page_slug = _slug(title)
    page_url  = f"{_SHOPIFY_DOMAIN}/pages/{page_slug}"
    try:
        from workers.shopify_publisher import create_page
        from workers.episode_deployer import _build_page_html
        _page_meta = {
            "title":             title,
            "episode_type":      episode_type,
            "duration_minutes":  duration,
            "description_short": description_short,
            "description_long":  description_long,
            "hero_image_url":    cdn_url,
            "buzzsprout_url":    None,
            "script_text":       script,
        }
        result   = create_page(
            title=title,
            body_html=_build_page_html(_page_meta, page_url),
            handle=page_slug,
            seo_description=(description_short[:155] if description_short else None),
            is_published=True,
        )
        page_url = result["url"]
        print(f"[sleep_audio] Page: {page_url}")
    except Exception as exc:
        print(f"[sleep_audio] Page creation failed (non-fatal): {exc}")

    # ── 5. Save episode stub to DB + update hub pages ────────────────────────
    # Klaviyo campaigns are created ONLY by the #beezy-new-episodes watcher,
    # after Boris uploads the audio to Buzzsprout. Calling deploy_episode() here
    # would create duplicate campaigns (one with no audio URL, one with).
    episode_id   = f"ep_cal_{slot_date.replace('-', '')}_{uuid.uuid4().hex[:6]}"
    episode_meta = {
        "episode_id":          episode_id,
        "title":               title,
        "episode_type":        episode_type,
        "duration_minutes":    duration,
        "cover_image_url":     cdn_url,
        "shopify_page_url":    page_url,
        "buzzsprout_url":      None,
        "suggested_send_date": slot_date,
    }
    deploy_result = _save_stub_and_update_hubs(episode_meta)

    # ── 6. Dispatch TTS ───────────────────────────────────────────────────────
    label = _EPISODE_LABELS.get(episode_type, "Sleep Audio")
    if _SAP_URL:
        dispatched = _dispatch_to_tts(
            episode_id=episode_id, title=title, script=script,
            episode_type=episode_type, duration=duration,
            profile="sleep_story_philosophical",
            slot_date=slot_date, page_url=page_url,
            description_short=description_short,
        )
        if dispatched:
            _post_slack_auto_dispatch(
                title=title, label=label, duration=duration,
                slot_date=slot_date, page_url=page_url,
                deploy_result=deploy_result, cover_url=cdn_url,
            )
            return f"deployed:{episode_id}"
        # SAP call failed → fall through to manual handoff

    _post_slack_handoff(
        title=title, label=label, duration=duration, slot_date=slot_date,
        page_url=page_url, deploy_result=deploy_result,
        script=script, cover_url=cdn_url, run_id=skill_result.run_id,
    )

    return f"deployed:{episode_id}"


def _dispatch_to_tts(
    *, episode_id: str, title: str, script: str, episode_type: str,
    duration: int, profile: str, slot_date: str, page_url: str,
    description_short: str,
) -> bool:
    """POST script to the sleep-audio-platform cloud API.
    Returns True if the server accepted (202), False on any failure.
    """
    payload = {
        "episode_id":          episode_id,
        "title":               title,
        "topic":               title,
        "script_text":         script,
        "episode_type":        episode_type,
        "duration_minutes":    duration,
        "profile":             profile,
        "suggested_send_date": slot_date,
        "shopify_page_url":    page_url,
        "description_short":   description_short,
    }
    headers = {"Content-Type": "application/json"}
    if _SAP_KEY:
        headers["X-API-Key"] = _SAP_KEY
    try:
        resp = httpx.post(
            f"{_SAP_URL}/api/v1/generate",
            json=payload,
            headers=headers,
            timeout=15,
            verify=False,   # Replit CA bundle doesn't include all intermediate certs
        )
        if resp.status_code == 202:
            print(f"[sleep_audio] TTS dispatched to sleep-audio-platform — run_id: {resp.json().get('run_id', '?')}")
            return True
        print(f"[sleep_audio] sleep-audio-platform returned {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as exc:
        print(f"[sleep_audio] TTS dispatch failed (falling back to Slack handoff): {exc}")
        return False


def _post_slack_auto_dispatch(
    *, title: str, label: str, duration: int, slot_date: str,
    page_url: str, deploy_result: str, cover_url: str,
) -> None:
    """Minimal Slack notification used when the webhook handles TTS automatically."""
    summary_lines = [
        f"*Title:* {title}",
        f"*Type:* {label}  ·  {duration} min",
        f"*Send date:* {slot_date}",
        f"*Page:* {page_url}",
        f"*TTS:* Dispatched to sleep-audio-platform ⚡",
        f"*Campaigns:* Created automatically once Buzzsprout upload completes",
    ]
    post_draft(
        title=f"Sleep Audio Dispatched — {title}",
        summary_lines=summary_lines,
        body="TTS pipeline is running in the cloud. No action needed.",
        image_url=cover_url or None,
        image_alt=title,
    )


def _post_slack_handoff(*, title: str, label: str, duration: int, slot_date: str,
                        page_url: str, deploy_result: str, script: str,
                        cover_url: str, run_id: str) -> None:
    summary_lines = [
        f"*Title:* {title}",
        f"*Type:* {label}  ·  {duration} min",
        f"*Send date:* {slot_date}",
        f"*Page:* {page_url}",
        f"*Campaigns:* Created automatically after you upload audio to Buzzsprout",
    ]
    body = (
        "*Next step — Boris:*\n"
        "1. Copy the script below into your *sleep-audio-platform* Claude chat\n"
        "2. It produces TTS (ElevenLabs) → uploads to Buzzsprout automatically\n"
        "3. sleep-audio-platform posts metadata to *#beezy-new-episodes* when done\n"
        "4. This system picks it up and updates the episode page with the audio player\n\n"
        f"_Script also stored in `runs` table — run ID: `{run_id}`_\n\n"
        "─────────────────── SCRIPT ───────────────────\n\n"
        + script
    )
    post_draft(
        title=f"Sleep Audio Ready — {title}",
        summary_lines=summary_lines,
        body=body,
        image_url=cover_url or None,
        image_alt=title,
    )
