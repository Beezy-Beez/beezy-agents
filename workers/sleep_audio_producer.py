"""
Sleep Audio Producer — orchestrator handler for calendar sleep_audio slots.

Full automatic pipeline (runs entirely in beezy-agents — no external TTS service):
  1. invoke_skill("sleep_audio")    → script + title + description + cover_image_prompt
  2. Higgsfield image generation
  3. Shopify CDN upload
  4. Shopify landing page (published, audio embed added later when Buzzsprout URL is known)
  5. episodes DB stub + hub pages
  6. TTS pipeline in background thread:
       workers/tts_pipeline.py → ElevenLabs chunks → ffmpeg concat → Buzzsprout
       → posts JSON to #beezy-new-episodes
       → Slack watcher auto-creates Klaviyo campaigns + posts ✅ to #beezy-agents
"""
from __future__ import annotations

import re
import threading
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


def _slug(title: str) -> str:
    return "episode-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _save_stub_and_update_hubs(episode_meta: dict) -> str:
    """Save episode stub to DB and update hub pages. Returns status string."""
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
        print(f"[sleep_audio] Episode stub saved: {episode_meta.get('episode_id')}")
        status_parts.append("db:ok")
    except Exception as exc:
        print(f"[sleep_audio] DB stub save failed (non-fatal): {exc}")
        status_parts.append("db:error")

    try:
        from workers.hub_updater import add_episode_to_hubs
        hub_results = add_episode_to_hubs(episode_meta)
        print(f"[sleep_audio] Hub pages updated: {hub_results}")
        status_parts.append("hubs:ok")
    except Exception as exc:
        print(f"[sleep_audio] Hub update failed (non-fatal): {exc}")
        status_parts.append("hubs:error")

    return "pending_audio | " + " ".join(status_parts)


def _run_tts_thread(
    episode_id: str, title: str, script_text: str, episode_type: str,
    duration_minutes: int, description_short: str,
    shopify_page_url: str, suggested_send_date: str,
) -> None:
    """Background thread: runs the full TTS pipeline, posts to #beezy-new-episodes on success."""
    try:
        from workers.tts_pipeline import run_tts_pipeline, _post_tts_error
        run_tts_pipeline(
            episode_id=episode_id,
            title=title,
            script_text=script_text,
            episode_type=episode_type,
            duration_minutes=duration_minutes,
            description_short=description_short,
            shopify_page_url=shopify_page_url,
            suggested_send_date=suggested_send_date,
        )
    except Exception as exc:
        print(f"[tts_thread] FAILED — {title!r}: {exc}")
        try:
            from workers.tts_pipeline import _post_tts_error
            _post_tts_error(title=title, episode_id=episode_id, error=str(exc))
        except Exception as slack_exc:
            print(f"[tts_thread] Could not post Slack error: {slack_exc}")


def _start_tts(
    *, episode_id: str, title: str, script_text: str, episode_type: str,
    duration_minutes: int, description_short: str,
    shopify_page_url: str, suggested_send_date: str,
) -> None:
    """Start the TTS pipeline in a daemon thread. Returns immediately."""
    t = threading.Thread(
        target=_run_tts_thread,
        args=(episode_id, title, script_text, episode_type,
              duration_minutes, description_short,
              shopify_page_url, suggested_send_date),
        daemon=True,
        name=f"tts-{episode_id[:8]}",
    )
    t.start()
    print(f"[sleep_audio] TTS thread started — {t.name}")


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
        exc_str = str(exc)
        if "TAKEN" in exc_str or "already been taken" in exc_str.lower():
            print(f"[sleep_audio] Page already exists at {page_url} — reusing")
        else:
            print(f"[sleep_audio] Page creation failed (non-fatal): {exc}")

    # ── 5. Save episode stub to DB + update hub pages ────────────────────────
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

    # ── 6. TTS in background thread ───────────────────────────────────────────
    # Completes asynchronously: ElevenLabs → ffmpeg → Buzzsprout → #beezy-new-episodes
    # Slack watcher then auto-creates two Klaviyo campaigns and posts ✅ to #beezy-agents.
    label = _EPISODE_LABELS.get(episode_type, "Sleep Audio")
    eta_min = max(5, duration // 2)

    _start_tts(
        episode_id=episode_id,
        title=title,
        script_text=script,
        episode_type=episode_type,
        duration_minutes=duration,
        description_short=description_short,
        shopify_page_url=page_url,
        suggested_send_date=slot_date,
    )

    post_draft(
        title=f"🎙 Sleep Audio — {title}",
        summary_lines=[
            f"*Title:* {title}",
            f"*Type:* {label}  ·  {duration} min",
            f"*Send date:* {slot_date}",
            f"*Page:* {page_url}",
            f"*Episode ID:* `{episode_id}`",
            f"*ETA:* ~{eta_min}–{eta_min * 3} min — TTS running locally, campaigns auto-created when audio is ready",
        ],
        body="TTS pipeline is running. No action needed — campaigns and episode update post here when complete.",
        image_url=cdn_url or None,
        image_alt=title,
    )

    return f"deployed:{episode_id}"
