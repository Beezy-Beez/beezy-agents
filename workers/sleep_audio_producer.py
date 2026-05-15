"""
Sleep Audio Producer — orchestrator handler for calendar sleep_audio slots.

Full automatic pipeline:
  1. invoke_skill("sleep_audio")    → script + title + description + cover_image_prompt
  2. Higgsfield image generation
  3. Shopify CDN upload
  4. Shopify landing page (published, audio embed added later by sleep-audio-platform)
  5. deploy_episode()               → two Klaviyo campaigns + episodes DB row + hub pages
  6. Slack: summary + full script for Boris to feed into sleep-audio-platform
             (TTS → Buzzsprout → posts to #beezy-new-episodes → watcher auto-updates page)
"""
from __future__ import annotations

import re
import uuid
from datetime import date

from workers.skill_runner import invoke_skill
from lib.slack import post_draft

_SHOPIFY_DOMAIN = "https://trybeezybeez.com"

_EPISODE_LABELS = {
    "sleep_story":            "Sleep Story",
    "guided_meditation":      "Guided Meditation",
    "affirmation_meditation": "Affirmation Meditation",
    "morning_meditation":     "Morning Meditation",
    "soundscape":             "Sleep Soundscape",
}

_HUB_URLS = {
    "sleep_story":            f"{_SHOPIFY_DOMAIN}/pages/sleep-science-hub",
    "soundscape":             f"{_SHOPIFY_DOMAIN}/pages/sleep-science-hub",
    "guided_meditation":      f"{_SHOPIFY_DOMAIN}/pages/meditation-library",
    "affirmation_meditation": f"{_SHOPIFY_DOMAIN}/pages/meditation-library",
    "morning_meditation":     f"{_SHOPIFY_DOMAIN}/pages/morning-wellness-hub",
}


def _slug(title: str) -> str:
    return "episode-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _page_html(title: str, description: str, episode_type: str,
               duration_minutes: int | None, cover_url: str) -> str:
    label   = _EPISODE_LABELS.get(episode_type, "Sleep Audio")
    hub_url = _HUB_URLS.get(episode_type, f"{_SHOPIFY_DOMAIN}/pages/sleep-science-hub")
    dur     = f" · {duration_minutes} min" if duration_minutes else ""
    cover   = (
        f'<img src="{cover_url}" alt="{title}" '
        f'style="width:100%;max-width:680px;height:auto;display:block;'
        f'border-radius:8px;margin:0 0 28px 0;"/>'
        if cover_url else ""
    )
    return (
        f'<div style="max-width:680px;margin:0 auto;padding:40px 20px;'
        f'font-family:Georgia,serif;color:#2c2417;background:#faf6ee;">'
        f'{cover}'
        f'<p style="margin:0 0 8px 0;font-size:13px;letter-spacing:1.5px;'
        f'text-transform:uppercase;color:#8b7355;">{label}{dur}</p>'
        f'<h1 style="margin:0 0 20px 0;font-size:32px;line-height:1.2;color:#2c2417;">{title}</h1>'
        f'<p style="font-size:18px;line-height:1.75;color:#2c2417;margin:0 0 32px 0;">{description}</p>'
        f'<p style="font-size:16px;line-height:1.75;color:#5a4a3a;font-style:italic;">'
        f'Audio coming shortly — bookmark this page or return from your email link.</p>'
        f'<p style="margin:32px 0 0 0;">'
        f'<a href="{hub_url}" style="color:#8b4513;font-weight:bold;text-decoration:none;">'
        f'&rarr; Browse the full audio library</a>'
        f'</p></div>'
    )


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
    meta         = skill_result.output_json or {}
    title        = meta.get("title") or topic
    description  = meta.get("description", "")
    script       = meta.get("script", "")
    image_prompt = meta.get("cover_image_prompt", "")
    episode_type = meta.get("episode_type", episode_type)
    duration     = int(meta.get("duration_minutes", duration))
    print(f"[sleep_audio] Script ready — {len(script):,} chars — '{title}'")

    # ── 2. Cover image ────────────────────────────────────────────────────────
    cover_url = ""
    if image_prompt:
        try:
            from workers.image_gen import generate_cover
            cover_url = generate_cover(image_prompt).url
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
        result   = create_page(
            title=title,
            body_html=_page_html(title, description, episode_type, duration, cdn_url),
            handle=page_slug,
            seo_description=(description[:155] if description else None),
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

    # ── 6. Slack handoff with full script ─────────────────────────────────────
    label = _EPISODE_LABELS.get(episode_type, "Sleep Audio")
    _post_slack_handoff(
        title=title, label=label, duration=duration, slot_date=slot_date,
        page_url=page_url, deploy_result=deploy_result,
        script=script, cover_url=cdn_url, run_id=skill_result.run_id,
    )

    return f"deployed:{episode_id}"


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
