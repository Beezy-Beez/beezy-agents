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

import json
import os
import re
import uuid
from datetime import date, datetime, timezone

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
    # No "episode-" prefix per beezy-sleep-story-page v2.0 (Task 5).
    # 50-char length cap + DRY consolidation deferred to Task 5.5.
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


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
        from lib.sleep_audio_page_validator import validate_page, format_failure_slack
        from lib.slack import notify_failure
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
        body_html = _build_page_html(_page_meta, page_url)

        # v2.0 template gate — blocks broken templates from reaching Shopify (Task 5).
        v_result = validate_page(body_html, page_slug)
        if not v_result.passed:
            v_title, v_body = format_failure_slack(title, v_result)
            notify_failure("sleep_audio_producer/page_validation", v_body)
            print(f"[sleep_audio] PAGE VALIDATION FAILED — aborting publish ({v_result.summary()})")
            raise RuntimeError(f"page validation failed: {v_result.summary()}")

        result   = create_page(
            title=title,
            body_html=body_html,
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
        dispatched, tts_run_id = _dispatch_to_tts(
            episode_id=episode_id, title=title, script=script,
            episode_type=episode_type, duration=duration,
            profile="sleep_story_philosophical",
            slot_date=slot_date, page_url=page_url,
            description_short=description_short,
        )
        if dispatched:
            _store_pending_tts_run(episode_id=episode_id, run_id=tts_run_id, title=title)
            _post_slack_auto_dispatch(
                title=title, label=label, duration=duration,
                slot_date=slot_date, page_url=page_url,
                deploy_result=deploy_result, cover_url=cdn_url,
                run_id=tts_run_id,
            )
            return f"deployed:{episode_id}"
        # SAP returned non-202 — post error and fall through to manual handoff
        _post_slack_error(title=title, label=label, run_id=tts_run_id, episode_id=episode_id)

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
) -> tuple[bool, str]:
    """POST script to the sleep-audio-platform cloud API.
    Returns (accepted, run_id). run_id is '' on failure.
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
            run_id = resp.json().get("run_id", "")
            print(f"[sleep_audio] TTS dispatched to sleep-audio-platform — run_id: {run_id}")
            return True, run_id
        print(f"[sleep_audio] sleep-audio-platform returned {resp.status_code}: {resp.text[:200]}")
        return False, ""
    except Exception as exc:
        print(f"[sleep_audio] TTS dispatch failed (falling back to Slack handoff): {exc}")
        return False, ""


def _store_pending_tts_run(*, episode_id: str, run_id: str, title: str) -> None:
    """Append a pending TTS run to agent_state for the 30-min watchdog."""
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM agent_state WHERE key='pending_tts_runs'"
            ).fetchone()
            runs = json.loads(row[0]) if row else []
            runs.append({
                "episode_id":    episode_id,
                "run_id":        run_id,
                "title":         title,
                "dispatched_at": datetime.now(timezone.utc).isoformat(),
            })
            conn.execute(
                "INSERT INTO agent_state (key, value, updated_at) VALUES ('pending_tts_runs', %s, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                (json.dumps(runs),),
            )
            conn.commit()
    except Exception as exc:
        print(f"[sleep_audio] Failed to store pending TTS run (non-fatal): {exc}")


# Shared by the predicate and the actor — never two hardcoded numbers.
TTS_TIMEOUT_AGE_MIN = 30  # minutes a TTS run may be pending before alerting


def _tts_candidates_due() -> bool:
    """Pure read — a GATE, not a handoff. Returns True iff >=1 pending TTS run
    is past TTS_TIMEOUT_AGE_MIN. Never mutates or writes. The actor re-checks
    (incl. the Buzzsprout-URL lookup) and decides whether to alert."""
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM agent_state WHERE key='pending_tts_runs'"
            ).fetchone()
    except Exception:
        return False
    if not row:
        return False
    try:
        runs = json.loads(row[0])
    except Exception:
        return False
    now = datetime.now(timezone.utc)
    for run in runs:
        try:
            dispatched_at = datetime.fromisoformat(run["dispatched_at"])
        except (KeyError, ValueError):
            continue
        if (now - dispatched_at).total_seconds() / 60 >= TTS_TIMEOUT_AGE_MIN:
            return True
    return False


def check_tts_timeouts() -> bool:
    """Called every 5 min by cron. Alerts #beezy-agents if a TTS run has been
    pending > TTS_TIMEOUT_AGE_MIN without a Buzzsprout URL in the episodes
    table. Returns True iff it sent >=1 timeout alert this pass.
    """
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM agent_state WHERE key='pending_tts_runs'"
            ).fetchone()
        if not row:
            return False
        runs: list[dict] = json.loads(row[0])
    except Exception:
        return False

    if not runs:
        return False

    now = datetime.now(timezone.utc)
    still_pending: list[dict] = []
    acted = False

    for run in runs:
        try:
            dispatched_at = datetime.fromisoformat(run["dispatched_at"])
        except (KeyError, ValueError):
            continue
        age_minutes = (now - dispatched_at).total_seconds() / 60

        if age_minutes < TTS_TIMEOUT_AGE_MIN:
            still_pending.append(run)
            continue

        # Check whether the episode has a Buzzsprout URL yet
        try:
            from db.connection import get_conn
            with get_conn() as conn:
                ep = conn.execute(
                    "SELECT buzzsprout_url FROM episodes WHERE episode_id = %s",
                    (run["episode_id"],),
                ).fetchone()
        except Exception:
            still_pending.append(run)
            continue

        if ep and ep[0]:
            # Completed — drop from watchlist
            print(f"[sleep_audio] TTS run {run['run_id'][:8]}… completed (Buzzsprout URL in DB)")
            continue

        # Still no audio after 30 min — alert Boris
        acted = True
        run_id  = run.get("run_id", "unknown")
        title   = run.get("title", "unknown")
        print(f"[sleep_audio] TTS timeout alert — run_id: {run_id}")
        post_draft(
            title=f"⚠️ TTS timeout — {title}",
            summary_lines=[
                f"*Episode:* {title}",
                f"*Run ID:* `{run_id}`",
                f"*Dispatched:* {run['dispatched_at'][:19].replace('T', ' ')} UTC",
                f"*Age:* {int(age_minutes)} min — no Buzzsprout URL yet",
                "Check sleep-audio-platform logs for this run_id.",
            ],
            body="",
        )
        # Drop from list — alert sent once, don't spam
        # (if genuinely stuck Boris can redeploy manually)

    # Persist updated watchlist
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO agent_state (key, value, updated_at) VALUES ('pending_tts_runs', %s, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                (json.dumps(still_pending),),
            )
            conn.commit()
    except Exception as exc:
        print(f"[sleep_audio] Failed to update pending_tts_runs: {exc}")
    return acted


def _post_slack_auto_dispatch(
    *, title: str, label: str, duration: int, slot_date: str,
    page_url: str, deploy_result: str, cover_url: str, run_id: str,
) -> None:
    """Slack notification when TTS is accepted by sleep-audio-platform."""
    eta_min = max(5, duration // 2)
    summary_lines = [
        f"*Title:* {title}",
        f"*Type:* {label}  ·  {duration} min",
        f"*Send date:* {slot_date}",
        f"*Page:* {page_url}",
        f"*TTS run ID:* `{run_id}`",
        f"*ETA:* ~{eta_min}–{eta_min * 3} min — campaigns auto-created on Buzzsprout upload",
    ]
    post_draft(
        title=f"🎙 Sleep Audio Dispatched — {title}",
        summary_lines=summary_lines,
        body="TTS pipeline running in the cloud. No action needed — campaigns post here when audio is ready.",
        image_url=cover_url or None,
        image_alt=title,
    )


def _post_slack_error(*, title: str, label: str, run_id: str, episode_id: str) -> None:
    """Slack alert when sleep-audio-platform returns non-202 or is unreachable."""
    post_draft(
        title=f"❌ TTS dispatch failed — {title}",
        summary_lines=[
            f"*Episode:* {title}",
            f"*Type:* {label}",
            f"*Episode ID:* `{episode_id}`",
            f"*Run ID:* `{run_id or 'n/a'}`",
            "sleep-audio-platform returned a non-202 response or is unreachable.",
            "Script has been posted below for manual TTS — see handoff message.",
        ],
        body="",
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
