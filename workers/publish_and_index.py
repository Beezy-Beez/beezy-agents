"""
workers/publish_and_index.py

Publishes Shopify pages and updates all index pages on send day.
Eliminates the daily manual tax of updating hub pages before each email send.

Called from:
  - main.py cron at 8:05am ET  (after orchestrator at 8:00am)
  - Slack: "update indexes" or "publish today"

What it does
────────────
  1. Finds Hive Mind issues with scheduled_send_at = today
       → makes the issue's own Shopify page VISIBLE (isPublished:true)
       → inserts entry into /pages/the-hive-mind archive
       → replaces SSH_FEATURED block on /pages/sleep-science-hub
  2. Finds sleep audio episodes whose Klaviyo campaign fires today
       → adds card to /pages/meditation-library  (sleep / guided meditation)
       → adds card to /pages/morning-wellness-hub (morning meditation)

Publish-day rule: issue pages are created HIDDEN by shopify_publisher.create_page()
(is_published defaults to False). This worker is what flips them live — exactly
on send day, right before the email goes out. Nothing else publishes them.

Idempotent: already-present content / already-visible pages are detected and
re-applying is a no-op.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from db.connection import get_conn
from lib.shopify_admin import graphql
from lib.slack import _post as _slack_post

SLACK_CHANNEL = "C0B3DEUJS9G"  # #beezy-agents — NEVER change


# ── Shopify page helpers ───────────────────────────────────────────────────────

_PAGE_BY_HANDLE = """
query ($q: String!) {
  pages(first: 1, query: $q) {
    edges { node { id handle body } }
  }
}
"""

_PAGE_UPDATE = """
mutation pageUpdate($id: ID!, $body: String!) {
  pageUpdate(id: $id, page: { body: $body }) {
    page { id handle }
    userErrors { field message }
  }
}
"""

_PAGE_PUBLISH = """
mutation pagePublish($id: ID!) {
  pageUpdate(id: $id, page: { isPublished: true }) {
    page { id handle }
    userErrors { field message }
  }
}
"""


def _fetch_page(handle: str) -> dict | None:
    data = graphql(_PAGE_BY_HANDLE, {"q": f"handle:{handle}"})
    edges = ((data or {}).get("pages") or {}).get("edges") or []
    return edges[0]["node"] if edges else None


def _save_page(page_id: str, body: str) -> bool:
    data = graphql(_PAGE_UPDATE, {"id": page_id, "body": body})
    errors = ((data or {}).get("pageUpdate") or {}).get("userErrors") or []
    if errors:
        print(f"  [publish_and_index] pageUpdate errors: {errors}")
        return False
    return True


# ── DB queries ─────────────────────────────────────────────────────────────────

def _today_hive_mind_issues() -> list[dict]:
    today = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT number, subject_line, page_dek, shopify_page_url,
                      cover_image_url, scheduled_send_at
               FROM issues
               WHERE scheduled_send_at::date = %s
                 AND shopify_page_url IS NOT NULL
               ORDER BY number DESC""",
            (today,),
        ).fetchall()
    return [
        {
            "number": r[0],
            "subject_line": r[1] or "",
            "page_dek": (r[2] or "")[:200],
            "shopify_page_url": r[3] or "",
            "cover_image_url": r[4] or "",
            "scheduled_send_at": r[5],
        }
        for r in rows
    ]


def _today_sleep_audio_episodes() -> list[dict]:
    today = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT e.title, e.episode_type, e.shopify_page_url,
                      e.cover_image_url, e.duration_minutes
               FROM episodes e
               JOIN calendar_executions ce
                 ON ce.klaviyo_campaign_id = e.klaviyo_campaign_id_a
               WHERE ce.slot_date = %s
                 AND ce.content_type = 'sleep_audio'
                 AND e.shopify_page_url IS NOT NULL""",
            (today,),
        ).fetchall()
    if not rows:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT title, episode_type, shopify_page_url,
                          cover_image_url, duration_minutes
                   FROM episodes
                   WHERE deployed_at::date = %s
                     AND shopify_page_url IS NOT NULL""",
                (today,),
            ).fetchall()
    return [
        {
            "title": r[0] or "",
            "episode_type": r[1] or "",
            "shopify_page_url": r[2] or "",
            "cover_image_url": r[3] or "",
            "duration_minutes": r[4] or 0,
        }
        for r in rows
    ]


# ── Make the issue's own page visible ──────────────────────────────────────────

def _publish_issue_page(issue: dict) -> str:
    """Flip the issue's own Shopify page to visible (isPublished:true) on send day.

    Pages are created HIDDEN by shopify_publisher.create_page(); this is the step
    that makes them live, exactly when the email sends. Idempotent — running it
    on an already-visible page is a harmless no-op.

    Returns 'published', 'error:no_slug', 'error:page_not_found', or
    'error:publish_failed'.
    """
    slug = issue["shopify_page_url"].rstrip("/").split("/")[-1]
    if not slug:
        return "error:no_slug"
    page = _fetch_page(slug)
    if not page:
        return "error:page_not_found"
    data = graphql(_PAGE_PUBLISH, {"id": page["id"]})
    errors = ((data or {}).get("pageUpdate") or {}).get("userErrors") or []
    if errors:
        print(f"  [publish_and_index] page publish errors: {errors}")
        return "error:publish_failed"
    return "published"


# ── Hive Mind archive (/pages/the-hive-mind) ──────────────────────────────────

_ARCHIVE_OL = 'id="hma-archive"'


def _archive_entry(issue: dict) -> str:
    slug = issue["shopify_page_url"].rstrip("/").split("/")[-1]
    url = f"https://trybeezybeez.com/pages/{slug}"
    n = issue["number"]
    title = issue["subject_line"]
    dek = issue["page_dek"]
    return (
        f'\n<li class="hma-item">\n'
        f'<p class="hma-issue-num">Issue {n:03d}</p>\n'
        f'<h2 class="hma-h2"><a href="{url}">{title}</a></h2>\n'
        f'<p class="hma-dek">{dek}</p>\n'
        f'<a class="hma-read" href="{url}">Read Issue {n:03d} \u2192</a>\n'
        f'</li>'
    )


def _add_issue_to_archive(issue: dict) -> str:
    page = _fetch_page("the-hive-mind")
    if not page:
        return "error:page_not_found"
    slug = issue["shopify_page_url"].rstrip("/").split("/")[-1]
    if slug in page["body"]:
        return "already_present"
    ol_tag_end = page["body"].find(">", page["body"].find(_ARCHIVE_OL))
    if ol_tag_end == -1:
        return "error:archive_ol_not_found"
    entry = _archive_entry(issue)
    new_body = page["body"][:ol_tag_end + 1] + entry + page["body"][ol_tag_end + 1:]
    return "added" if _save_page(page["id"], new_body) else "error:save_failed"


# ── Sleep Science Hub SSH_FEATURED (/pages/sleep-science-hub) ─────────────────

_SSH_START = "<!-- SSH_FEATURED_START -->"
_SSH_END   = "<!-- SSH_FEATURED_END -->"


def _featured_block(issue: dict) -> str:
    slug = issue["shopify_page_url"].rstrip("/").split("/")[-1]
    url = f"https://trybeezybeez.com/pages/{slug}"
    img = issue["cover_image_url"]
    title = issue["subject_line"]
    dek = issue["page_dek"]
    n = issue["number"]
    return (
        f'{_SSH_START}\n'
        f'<section class="ssh-section"><div class="ssh-featured">\n'
        f'<div><img src="{img}" alt="The Hive Mind Issue {n:03d}" class="ssh-featured-image"></div>\n'
        f'<div>\n'
        f'<div class="ssh-featured-eyebrow">Latest Issue \u00b7 The Hive Mind</div>\n'
        f'<h2 class="ssh-featured-title">{title}</h2>\n'
        f'<p class="ssh-featured-excerpt">{dek}</p>\n'
        f'<a href="{url}" class="ssh-featured-link">Read Issue {n:03d} \u2192</a>\n'
        f'</div>\n'
        f'</div></section>\n'
        f'{_SSH_END}'
    )


def _update_ssh_featured(issue: dict) -> str:
    page = _fetch_page("sleep-science-hub")
    if not page:
        return "error:page_not_found"
    slug = issue["shopify_page_url"].rstrip("/").split("/")[-1]
    body = page["body"]
    if _SSH_START in body and slug in body[body.find(_SSH_START):body.find(_SSH_END) + len(_SSH_END)]:
        return "already_current"
    new_block = _featured_block(issue)
    if _SSH_START in body and _SSH_END in body:
        start = body.find(_SSH_START)
        end = body.find(_SSH_END) + len(_SSH_END)
        new_body = body[:start] + new_block + body[end:]
    else:
        print("  [publish_and_index] WARNING: SSH_FEATURED sentinels not found — prepending block.")
        new_body = new_block + "\n" + body
    return "updated" if _save_page(page["id"], new_body) else "error:save_failed"


# ── Meditation/Morning library pages ──────────────────────────────────────────

_SLEEP_GRID_ANCHOR  = 'class="ssh-article-grid"'
_MORNING_EPISODE_TYPES = {"morning_meditation"}


def _episode_card(episode: dict) -> str:
    slug = episode["shopify_page_url"].rstrip("/").split("/")[-1]
    url = f"https://trybeezybeez.com/pages/{slug}"
    img = episode["cover_image_url"]
    title = episode["title"]
    dur = episode["duration_minutes"]
    return (
        f'<a href="{url}" class="ssh-article-card">'
        f'<img src="{img}" alt="{title}" class="ssh-article-card-image">'
        f'<div class="ssh-article-card-body">'
        f'<div class="ssh-article-card-eyebrow">Guided Meditation \u00b7 {dur} min</div>'
        f'<h3 class="ssh-article-card-title">{title}</h3>'
        f'<div class="ssh-article-card-meta">Sleep Better Podcast \u00b7 New</div>'
        f'</div></a>\n'
    )


def _add_episode_to_library(episode: dict) -> str:
    ep_type = episode["episode_type"].lower()
    handle = "morning-wellness-hub" if ep_type in _MORNING_EPISODE_TYPES else "meditation-library"
    page = _fetch_page(handle)
    if not page:
        return f"error:{handle}_not_found"
    slug = episode["shopify_page_url"].rstrip("/").split("/")[-1]
    if slug in page["body"]:
        return "already_present"
    card = _episode_card(episode)
    grid_pos = page["body"].find(_SLEEP_GRID_ANCHOR)
    if grid_pos == -1:
        return f"error:grid_anchor_not_found_in_{handle}"
    tag_end = page["body"].find(">", grid_pos) + 1
    new_body = page["body"][:tag_end] + "\n" + card + page["body"][tag_end:]
    return "added" if _save_page(page["id"], new_body) else "error:save_failed"


# ── Main entry point ───────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> dict:
    results = {"hive_mind": [], "episodes": [], "errors": []}
    today_str = date.today().strftime("%B %-d")

    hm_issues = _today_hive_mind_issues()
    for issue in hm_issues:
        n = issue["number"]
        print(f"  [publish_and_index] Processing Issue {n:03d}...")
        entry = {"issue": n}
        if not dry_run:
            # Page goes live FIRST — before the archive/hub link to it.
            entry["page"] = _publish_issue_page(issue)
            entry["archive"] = _add_issue_to_archive(issue)
            entry["featured"] = _update_ssh_featured(issue)
        else:
            entry["page"] = entry["archive"] = entry["featured"] = "dry_run"
        results["hive_mind"].append(entry)
        if any("error:" in str(v) for v in entry.values()):
            results["errors"].append(f"Issue {n:03d}: {entry}")

    episodes = _today_sleep_audio_episodes()
    for ep in episodes:
        print(f"  [publish_and_index] Processing episode: {ep['title'][:50]}...")
        entry = {"title": ep["title"][:60]}
        if not dry_run:
            entry["library"] = _add_episode_to_library(ep)
        else:
            entry["library"] = "dry_run"
        results["episodes"].append(entry)
        if "error:" in str(entry.get("library", "")):
            results["errors"].append(f"Episode {ep['title'][:40]}: {entry}")

    _notify_slack(results, today_str, dry_run)
    return results


def _notify_slack(results: dict, today_str: str, dry_run: bool) -> None:
    mode = " (DRY RUN)" if dry_run else ""
    lines = [f":newspaper: *Index update — {today_str}{mode}*\n"]
    if results["hive_mind"]:
        for entry in results["hive_mind"]:
            n = entry["issue"]
            p = entry.get("page", "?")
            a = entry.get("archive", "?")
            f = entry.get("featured", "?")
            icon = ":x:" if any("error:" in str(v) for v in (p, a, f)) else ":white_check_mark:"
            lines.append(
                f"{icon} Issue {n:03d} page: `{p}` · archive: `{a}` · SSH_FEATURED: `{f}`"
            )
    else:
        lines.append("No Hive Mind issues today")
    for entry in results.get("episodes", []):
        lib = entry.get("library", "?")
        icon = ":white_check_mark:" if "added" in lib or "already" in lib else ":x:"
        lines.append(f"{icon} Episode `{entry['title'][:40]}`: `{lib}`")
    if results["errors"]:
        lines.append(f"\n:red_circle: *{len(results['errors'])} error(s):*")
        for e in results["errors"]:
            lines.append(f"  • {e}")
    try:
        _slack_post({"text": "\n".join(lines)})
    except Exception as exc:
        print(f"  [publish_and_index] Slack notify failed: {exc}")
