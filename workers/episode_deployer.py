"""
workers/episode_deployer.py — deploy a pre-produced sleep audio episode.

Called by the orchestrator for sleep_audio calendar slots.

Two modes:
  PRE-PRODUCED  slot["notes"] contains JSON episode metadata (title, buzzsprout_url,
                cover_image_url, etc.) — full pipeline runs here.
  GENERATE      slot["notes"] is absent or empty — delegates to
                sleep_audio_producer.run_sleep_audio_slot() (script generation flow).

Pre-produced pipeline (when notes metadata is present):
  1. Parse episode metadata from slot["notes"]
  2. Create Shopify page (isPublished=True) using episode page template
  3. Update hub index pages via lib.index_updater
  4. Build two email HTML variants via lib.email_builder_episode
  5. Create Klaviyo DRAFT campaigns: Email A (Engaged Customers excl Active Seal)
     and Email B (Active Seal) using confirmed REST sequence
  6. Save episode row to episodes DB table
  7. Post Slack notification to #beezy-agents
  8. Return {"campaign_id": camp_a_id} for orchestrator to store in calendar_executions

Slot metadata keys (in slot["notes"] as JSON string):
    title               str   — episode title
    episode_type        str   — sleep_story | guided_meditation | affirmation_meditation
                                 | morning_meditation | soundscape
    buzzsprout_url      str   — canonical Buzzsprout URL (also used as page CTA)
    buzzsprout_embed_url str  — embed player URL (optional; embedded in page)
    hero_image_url      str   — cover image URL (Higgsfield CDN or similar)
    description_short   str   — short description for email hook (1–2 sentences)
    description_long    str   — longer description for page body
    script_text         str   — full narration script (stored in page body)
    duration_minutes    int   — episode length in minutes
    suggested_send_date str   — ISO date for campaign naming (YYYY-MM-DD)
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any

import psycopg

from config import DATABASE_URL
from lib.slack import post_draft, notify_failure


# ── Audience IDs ──────────────────────────────────────────────────────────────

_ENGAGED_CUSTOMERS = "RvtHdn"
_ACTIVE_SEAL       = "UBFUcH"
_FROM_EMAIL        = os.environ.get("KLAVIYO_FROM_EMAIL", "help@trybeezybeez.com")
_FROM_LABEL        = "Beezy Beez"
_SHOPIFY_DOMAIN    = "https://trybeezybeez.com"

_EPISODE_LABELS = {
    "sleep_story":            "Sleep Story",
    "guided_meditation":      "Guided Meditation",
    "affirmation_meditation": "Affirmation Meditation",
    "morning_meditation":     "Morning Meditation",
    "soundscape":             "Sleep Soundscape",
}

# episode_type → hub handles to update (matches hub_updater._EPISODE_HUBS)
_HUB_MAP: dict[str, list[str]] = {
    "sleep_story":            ["sleep-science-hub"],
    "soundscape":             ["sleep-science-hub"],
    "guided_meditation":      ["sleep-science-hub", "meditation-library"],
    "affirmation_meditation": ["sleep-science-hub", "meditation-library"],
    "morning_meditation":     ["sleep-science-hub", "meditation-library", "morning-wellness-hub"],
}

# page_type hint for index_updater per episode_type
_PAGE_TYPE: dict[str, str] = {
    "sleep_story":            "sleep_story",
    "soundscape":             "sleep_story",
    "guided_meditation":      "meditation",
    "affirmation_meditation": "meditation",
    "morning_meditation":     "morning_meditation",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slug(title: str) -> str:
    return "episode-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _build_page_html(meta: dict[str, Any]) -> str:
    """Build episode page body HTML (re-uses sleep_audio_producer template)."""
    from workers.sleep_audio_producer import _page_html
    title        = meta.get("title", "")
    description  = meta.get("description_long") or meta.get("description_short") or ""
    episode_type = meta.get("episode_type", "sleep_story")
    duration     = meta.get("duration_minutes")
    cover_url    = meta.get("hero_image_url") or meta.get("cover_image_url") or ""
    embed_url    = meta.get("buzzsprout_embed_url") or ""

    html = _page_html(title, description, episode_type, duration, cover_url)

    # Inject Buzzsprout embed player after the description paragraph if URL present
    if embed_url:
        embed_block = (
            f'\n<div style="margin:0 0 32px 0;">'
            f'<iframe src="{embed_url}" height="200" width="100%" '
            f'frameborder="0" scrolling="no"></iframe>'
            f'</div>'
        )
        # Insert before the "Audio coming shortly" placeholder if it exists
        html = html.replace(
            '<p style="font-size:16px;line-height:1.75;color:#5a4a3a;font-style:italic;">'
            'Audio coming shortly',
            embed_block + '<p style="font-size:16px;line-height:1.75;color:#5a4a3a;font-style:italic;">'
            'Audio coming shortly',
            1,
        )

    return html


def _create_klaviyo_draft(
    html: str,
    name: str,
    subject: str,
    segment_ids: list[str],
    excluded_ids: list[str] | None = None,
) -> str:
    """Create template → campaign → assign template. Returns campaign_id."""
    from agents.klaviyo_deployer import create_template, create_campaign, assign_template
    tpl_id = create_template(html, name)
    camp_id, msg_id = create_campaign(
        name=name,
        subject=subject,
        from_email=_FROM_EMAIL,
        from_label=_FROM_LABEL,
        segment_ids=segment_ids,
        excluded_ids=excluded_ids,
    )
    if msg_id:
        assign_template(msg_id, tpl_id)
    return camp_id


def _parse_meta(slot: dict[str, Any]) -> dict[str, Any] | None:
    """Extract episode metadata from slot["notes"]. Returns None if absent/invalid."""
    raw = slot.get("notes")
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw if raw.get("title") else None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) and parsed.get("title") else None
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _save_episode(meta: dict[str, Any], page_url: str,
                  camp_a_id: str, camp_b_id: str) -> None:
    """Upsert episode row to episodes table."""
    episode_id = meta.get("episode_id") or f"ep_{uuid.uuid4().hex[:10]}"
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute(
                """
                INSERT INTO episodes
                    (episode_id, title, episode_type, buzzsprout_url, shopify_page_url,
                     cover_image_url, duration_minutes, suggested_send_date,
                     klaviyo_campaign_id_a, klaviyo_campaign_id_b)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (episode_id) DO UPDATE SET
                    klaviyo_campaign_id_a = EXCLUDED.klaviyo_campaign_id_a,
                    klaviyo_campaign_id_b = EXCLUDED.klaviyo_campaign_id_b,
                    shopify_page_url      = EXCLUDED.shopify_page_url,
                    deployed_at           = NOW()
                """,
                (
                    episode_id,
                    meta.get("title"),
                    meta.get("episode_type", "sleep_story"),
                    meta.get("buzzsprout_url"),
                    page_url,
                    meta.get("hero_image_url") or meta.get("cover_image_url"),
                    meta.get("duration_minutes"),
                    meta.get("suggested_send_date"),
                    camp_a_id,
                    camp_b_id,
                ),
            )
            conn.commit()
        print(f"[episode_deployer] Episode saved to DB: {episode_id}")
    except Exception as exc:
        print(f"[episode_deployer] DB save failed (non-fatal): {exc}")


# ── Public API ────────────────────────────────────────────────────────────────

def run(slot: dict[str, Any]) -> dict[str, Any] | str:
    """
    Main orchestrator entry point for sleep_audio slots.

    If slot["notes"] contains valid episode metadata (JSON with "title"), runs the
    full pre-produced deployment pipeline and returns {"campaign_id": camp_a_id}.

    Otherwise delegates to sleep_audio_producer.run_sleep_audio_slot(slot)
    (generate-from-scratch two-phase flow) and returns its status string.
    """
    meta = _parse_meta(slot)
    if not meta:
        print("[episode_deployer] No episode metadata in slot — delegating to sleep_audio_producer")
        from workers.sleep_audio_producer import run_sleep_audio_slot
        return run_sleep_audio_slot(slot)

    return _deploy_pre_produced(slot, meta)


def _deploy_pre_produced(slot: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Full deployment pipeline for a pre-produced episode."""
    title        = meta["title"]
    episode_type = meta.get("episode_type", "sleep_story")
    label        = _EPISODE_LABELS.get(episode_type, episode_type.replace("_", " ").title())
    slot_date    = slot.get("date", date.today().isoformat())
    send_date    = meta.get("suggested_send_date") or slot_date
    page_slug    = _slug(title)
    page_url     = f"{_SHOPIFY_DOMAIN}/pages/{page_slug}"

    print(f"[episode_deployer] Deploying '{title}' ({label}) — {slot_date}")

    # ── 1. Shopify page (isPublished=True) ────────────────────────────────
    try:
        from workers.shopify_publisher import create_page
        body_html = _build_page_html(meta)
        page_result = create_page(
            title=title,
            body_html=body_html,
            handle=page_slug,
            seo_description=(meta.get("description_short") or "")[:155] or None,
            is_published=True,
        )
        page_url = page_result["url"]
        print(f"[episode_deployer] Page created: {page_url}")
    except Exception as exc:
        print(f"[episode_deployer] Page creation failed (continuing with predicted URL): {exc}")
        notify_failure("episode_deployer/page", str(exc))

    # ── 2. Update hub index pages ─────────────────────────────────────────
    try:
        from workers.hub_updater import _episode_card
        from lib.index_updater import update_index_page
        card_meta = {**meta, "shopify_page_url": page_url,
                     "cover_image_url": meta.get("hero_image_url") or meta.get("cover_image_url") or ""}
        card_html   = _episode_card(card_meta)
        page_type   = _PAGE_TYPE.get(episode_type, "sleep_story")
        hub_handles = _HUB_MAP.get(episode_type, ["sleep-science-hub"])
        hub_results = {h: update_index_page(h, card_html, page_type) for h in hub_handles}
        print(f"[episode_deployer] Hub updates: {hub_results}")
    except Exception as exc:
        print(f"[episode_deployer] Hub update failed (non-fatal): {exc}")
        hub_results = {}

    # Also call hub_updater.add_episode_to_hubs for DB-backed full rebuild
    try:
        from workers.hub_updater import add_episode_to_hubs
        add_episode_to_hubs({**meta, "shopify_page_url": page_url})
    except Exception as exc:
        print(f"[episode_deployer] add_episode_to_hubs failed (non-fatal): {exc}")

    # ── 3. Build email HTML ───────────────────────────────────────────────
    email_meta = {**meta, "shopify_page_url": page_url,
                  "cover_image_url": meta.get("hero_image_url") or meta.get("cover_image_url") or ""}
    try:
        from lib.email_builder_episode import build_episode_emails
        email_a_html, email_b_html = build_episode_emails(email_meta, page_url)
    except Exception as exc:
        raise RuntimeError(f"Email HTML build failed: {exc}") from exc

    # ── 4. Klaviyo DRAFT campaigns ────────────────────────────────────────
    camp_name_base = f"{title} | {send_date}"

    # Subject lines — curiosity variant first
    _SUBJECT_A = {
        "sleep_story":            f"Tonight: {title}",
        "soundscape":             f"Something new for tonight — {title}",
        "guided_meditation":      f"5 minutes could change tonight — {title}",
        "affirmation_meditation": f"What if you woke up feeling different? — {title}",
        "morning_meditation":     f"Start tomorrow right — {title}",
    }
    _SUBJECT_B = {
        "sleep_story":            f"New sleep story for members: {title}",
        "soundscape":             f"New soundscape for members: {title}",
        "guided_meditation":      f"New guided meditation: {title}",
        "affirmation_meditation": f"New affirmation session: {title}",
        "morning_meditation":     f"New morning session: {title}",
    }
    subj_a = _SUBJECT_A.get(episode_type, f"Tonight: {title}")
    subj_b = _SUBJECT_B.get(episode_type, f"New {label}: {title}")

    print(f"[episode_deployer] Creating Klaviyo campaign A (Engaged Customers)...")
    camp_a_id = _create_klaviyo_draft(
        html=email_a_html,
        name=f"{camp_name_base} | Engaged Customers",
        subject=subj_a,
        segment_ids=[_ENGAGED_CUSTOMERS],
        excluded_ids=[_ACTIVE_SEAL],
    )
    print(f"[episode_deployer]   campaign_a: {camp_a_id}")

    print(f"[episode_deployer] Creating Klaviyo campaign B (Active Seal)...")
    camp_b_id = _create_klaviyo_draft(
        html=email_b_html,
        name=f"{camp_name_base} | Active Seal",
        subject=subj_b,
        segment_ids=[_ACTIVE_SEAL],
    )
    print(f"[episode_deployer]   campaign_b: {camp_b_id}")

    # ── 5. Save episode to DB ─────────────────────────────────────────────
    _save_episode(meta, page_url, camp_a_id, camp_b_id)

    # ── 6. Slack notification ─────────────────────────────────────────────
    admin_a = f"https://www.klaviyo.com/campaign/{camp_a_id}/wizard"
    admin_b = f"https://www.klaviyo.com/campaign/{camp_b_id}/wizard"
    post_draft(
        title=f"Episode deployed: {title}",
        summary_lines=[
            f"*Title:* {title}",
            f"*Type:* {label}",
            f"*Page:* {page_url}",
            f"*Email A (Engaged Customers):* {admin_a}",
            f"*Email B (Active Seal):* {admin_b}",
            "Ready for Boris review.",
        ],
        body=(
            f"Klaviyo A: {admin_a}\n"
            f"Klaviyo B: {admin_b}\n\n"
            f"Both campaigns are DRAFT — review subject lines, then schedule "
            f"Email A for 8:00pm ET and Email B for 8:15pm ET."
        ),
        image_url=meta.get("hero_image_url") or meta.get("cover_image_url") or None,
        image_alt=title,
    )
    print(f"[episode_deployer] Slack posted")

    return {"campaign_id": camp_a_id}
