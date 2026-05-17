"""Hub page auto-updater.

Injects content cards into Shopify hub/archive pages whenever a Hive Mind
issue is published or an audio episode is deployed.

Hub pages and what feeds them
─────────────────────────────
  /pages/the-hive-mind        ← all published Hive Mind issues (full rebuild from DB)
  /pages/sleep-science-hub    ← sleep_story / soundscape episodes ONLY (no newsletter issues)
  /pages/meditation-library   ← guided_meditation / affirmation_meditation episodes
  /pages/morning-wellness-hub ← morning_meditation episodes

Injection strategy
──────────────────
Each hub page body gets a managed section delimited by two pairs of HTML
comments so we never touch hand-authored content:

    <!-- HUB_SECTION_START -->
    <div ...>
      <h2 ...>Section Heading</h2>
      <!-- HUB_ITEMS_START -->
      ...card divs...
      <!-- HUB_ITEMS_END -->
    </div>
    <!-- HUB_SECTION_END -->

First call: appends the whole block.
Subsequent calls: replaces only what's between the HUB_ITEMS sentinels,
leaving the rest of the page body untouched.

Public API
──────────
  add_issue_to_hubs(issue: dict)    → called after Hive Mind campaign is created
  add_episode_to_hubs(metadata: dict) → called after episode deploy
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from lib.shopify_admin import graphql

# ── Sentinel markers ──────────────────────────────────────────────────────────

_SEC_S   = "<!-- HUB_SECTION_START -->"
_SEC_E   = "<!-- HUB_SECTION_END -->"
_ITEMS_S = "<!-- HUB_ITEMS_START -->"
_ITEMS_E = "<!-- HUB_ITEMS_END -->"

# ── Shared styles (brand colours from CLAUDE.md design system) ────────────────

_S = {
    "section": (
        "max-width:700px; margin:0 auto; padding:40px 20px; "
        "font-family:Georgia,'Times New Roman',serif;"
    ),
    "heading": (
        "font-size:22px; color:#2c2417; margin:0 0 25px 0; "
        "font-family:Georgia,serif; border-bottom:2px solid #d4a847; padding-bottom:12px;"
    ),
    "card": (
        "display:flex; gap:20px; margin:0 0 28px 0; padding:0 0 28px 0; "
        "border-bottom:1px solid #e8dcc8; align-items:flex-start;"
    ),
    "thumb_img": (
        "flex:0 0 110px; width:110px; height:74px; object-fit:cover; "
        "border-radius:4px; display:block;"
    ),
    "thumb_blank": (
        "flex:0 0 110px; width:110px; height:74px; background:#f5f0e8; "
        "border-radius:4px;"
    ),
    "meta": (
        "font-size:13px; color:#8b7355; margin:0 0 5px 0; "
        "text-transform:uppercase; letter-spacing:1px;"
    ),
    "title": (
        "font-size:19px; color:#2c2417; margin:0 0 6px 0; "
        "font-family:Georgia,serif; font-weight:bold; line-height:1.3;"
    ),
    "title_link": "color:#2c2417; text-decoration:none;",
    "dek":        "font-size:15px; color:#5a4a3a; margin:0 0 10px 0; line-height:1.5;",
    "cta":        "color:#8b4513; text-decoration:none; font-weight:bold; font-size:14px;",
}

_EPISODE_TYPE_LABELS = {
    "sleep_story":            "Sleep Story",
    "guided_meditation":      "Guided Meditation",
    "affirmation_meditation": "Affirmation Meditation",
    "morning_meditation":     "Morning Meditation",
    "soundscape":             "Soundscape",
}

# episode_type → hub handles it belongs to
_EPISODE_HUBS = {
    "sleep_story":            ["sleep-science-hub"],
    "soundscape":             ["sleep-science-hub"],
    "guided_meditation":      ["meditation-library"],
    "affirmation_meditation": ["meditation-library"],
    "morning_meditation":     ["morning-wellness-hub"],
}

_HUB_HEADINGS = {
    "the-hive-mind":         "All Issues",
    "sleep-science-hub":     "Sleep Science Deep-Dives",
    "meditation-library":    "Guided Meditations",
    "morning-wellness-hub":  "Morning Wellness Audio",
}


# ── Card builders ─────────────────────────────────────────────────────────────

def _issue_card(issue: dict) -> str:
    number = issue.get("number", "")
    title  = issue.get("subject_line") or issue.get("title", "Untitled")
    dek    = (issue.get("page_dek") or issue.get("topic_summary") or "")[:140]
    img    = issue.get("cover_image_url") or issue.get("shopify_image_url") or ""
    url    = issue.get("shopify_page_url") or "#"
    pillar = issue.get("pillar") or ""
    rt     = issue.get("read_time_min")

    meta_parts = [f"Issue {number:03d}" if number else "Issue"]
    if pillar:
        meta_parts.append(pillar)
    if rt:
        meta_parts.append(f"{rt} min read")

    thumb = (
        f'<img src="{img}" alt="" style="{_S["thumb_img"]}" />'
        if img else
        f'<div style="{_S["thumb_blank"]}"></div>'
    )
    return (
        f'<div style="{_S["card"]}">'
        f'<div>{thumb}</div>'
        f'<div style="flex:1;min-width:0;">'
        f'<p style="{_S["meta"]}">{" · ".join(meta_parts)}</p>'
        f'<p style="{_S["title"]}"><a href="{url}" style="{_S["title_link"]}">{title}</a></p>'
        f'<p style="{_S["dek"]}">{dek}</p>'
        f'<a href="{url}" style="{_S["cta"]}">Read this issue →</a>'
        f'</div></div>'
    )


def _episode_card(metadata: dict) -> str:
    title    = metadata.get("title") or "New Episode"
    ep_type  = metadata.get("episode_type") or "sleep_story"
    url      = metadata.get("shopify_page_url") or metadata.get("buzzsprout_url") or "#"
    img      = (
        metadata.get("cover_image_url")
        or metadata.get("thumbnail_url")
        or metadata.get("image_url")
        or ""
    )
    duration = metadata.get("duration_minutes")

    label = _EPISODE_TYPE_LABELS.get(ep_type, ep_type.replace("_", " ").title())
    meta_parts = [label]
    if duration:
        meta_parts.append(f"{duration} min")

    thumb = (
        f'<img src="{img}" alt="" style="{_S["thumb_img"]}" />'
        if img else
        f'<div style="{_S["thumb_blank"]}"></div>'
    )
    return (
        f'<div style="{_S["card"]}">'
        f'<div>{thumb}</div>'
        f'<div style="flex:1;min-width:0;">'
        f'<p style="{_S["meta"]}">{" · ".join(meta_parts)}</p>'
        f'<p style="{_S["title"]}"><a href="{url}" style="{_S["title_link"]}">{title}</a></p>'
        f'<a href="{url}" style="{_S["cta"]}">Listen now →</a>'
        f'</div></div>'
    )


# ── Sentinel block helpers ────────────────────────────────────────────────────

def _extract_items(body: str) -> str:
    """Return raw HTML between HUB_ITEMS sentinels, or '' if absent."""
    m = re.search(re.escape(_ITEMS_S) + r"(.*?)" + re.escape(_ITEMS_E), body, re.DOTALL)
    return m.group(1).strip() if m else ""


def _replace_items(body: str, new_items_html: str) -> str:
    """Replace only the HUB_ITEMS block, leaving the surrounding section intact."""
    replacement = f"{_ITEMS_S}\n{new_items_html}\n{_ITEMS_E}"
    return re.sub(
        re.escape(_ITEMS_S) + r".*?" + re.escape(_ITEMS_E),
        replacement,
        body,
        flags=re.DOTALL,
    )


def _append_section(body: str, heading: str, items_html: str) -> str:
    """Append a full managed section to the page body."""
    block = (
        f"\n{_SEC_S}\n"
        f'<div style="{_S["section"]}">'
        f'<h2 style="{_S["heading"]}">{heading}</h2>'
        f"{_ITEMS_S}\n{items_html}\n{_ITEMS_E}"
        f"</div>\n"
        f"{_SEC_E}\n"
    )
    return body + block


def _upsert_section(body: str, heading: str, items_html: str) -> str:
    """Replace items if section exists; append the whole section if not."""
    if _SEC_S in body:
        return _replace_items(body, items_html)
    return _append_section(body, heading, items_html)


# ── Shopify helpers ───────────────────────────────────────────────────────────

_PAGE_QUERY = """
query getPage($q: String!) {
  pages(first: 1, query: $q) {
    edges { node { id title body handle } }
  }
}
"""

_PAGE_UPDATE = """
mutation pageUpdate($id: ID!, $page: PageUpdateInput!) {
  pageUpdate(id: $id, page: $page) {
    page { id handle }
    userErrors { field message }
  }
}
"""


def _fetch_page(handle: str) -> dict | None:
    try:
        data  = graphql(_PAGE_QUERY, {"q": f"handle:{handle}"})
        edges = (data.get("pages") or {}).get("edges") or []
        return edges[0]["node"] if edges else None
    except Exception as exc:
        print(f"[hub_updater] fetch '{handle}' failed: {exc}")
        return None


def _save_page(page_id: str, title: str, body: str) -> None:
    data   = graphql(_PAGE_UPDATE, {"id": page_id, "page": {"title": title, "body": body}})
    errors = (data.get("pageUpdate") or {}).get("userErrors") or []
    if errors:
        raise RuntimeError(f"pageUpdate errors: {errors}")


def _update_hub(handle: str, items_html: str) -> str:
    """Fetch hub page, upsert section, save. Returns 'updated' or 'error: ...'."""
    page = _fetch_page(handle)
    if not page:
        print(f"[hub_updater] /pages/{handle} not found in Shopify — skipping")
        return "page not found"
    heading  = _HUB_HEADINGS.get(handle, "Archive")
    new_body = _upsert_section(page["body"] or "", heading, items_html)
    try:
        _save_page(page["id"], page["title"], new_body)
        print(f"[hub_updater] /pages/{handle} updated")
        return "updated"
    except Exception as exc:
        print(f"[hub_updater] /pages/{handle} save failed: {exc}")
        return f"error: {exc}"


# ── DB helpers ────────────────────────────────────────────────────────────────

def _all_published_issues() -> list[dict]:
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT number, subject_line, page_dek, cover_image_url,
                          shopify_page_url, pillar, read_time_min
                   FROM issues
                   WHERE status = 'published'
                   ORDER BY number DESC"""
            ).fetchall()
        return [
            {
                "number":           r[0],
                "subject_line":     r[1],
                "page_dek":         r[2],
                "cover_image_url":  r[3],
                "shopify_page_url": r[4],
                "pillar":           r[5],
                "read_time_min":    r[6],
            }
            for r in rows
        ]
    except Exception as exc:
        print(f"[hub_updater] DB issue query failed: {exc}")
        return []


# ── Public API ────────────────────────────────────────────────────────────────

def add_issue_to_hubs(issue: dict) -> dict[str, str]:
    """Called after a Hive Mind issue's Klaviyo campaign is created (page is live).

    Rebuilds /pages/the-hive-mind from all published DB issues (newest first).
    Does NOT touch /pages/sleep-science-hub — that page is for audio content only.

    Returns {handle: status} for each hub touched.
    """
    results: dict[str, str] = {}

    # /pages/the-hive-mind — full rebuild from DB so order is always correct
    all_issues = _all_published_issues()
    if not all_issues:
        all_issues = [issue]
    all_cards = "".join(_issue_card(i) for i in all_issues)
    results["the-hive-mind"] = _update_hub("the-hive-mind", all_cards)

    return results


def _episodes_for_hub(handle: str) -> list[dict]:
    """Return all deployed episodes for a hub, newest first, from the episodes table."""
    handle_to_types = {
        "sleep-science-hub":    ("sleep_story", "soundscape"),
        "meditation-library":   ("guided_meditation", "affirmation_meditation"),
        "morning-wellness-hub": ("morning_meditation",),
    }
    ep_types = handle_to_types.get(handle)
    if not ep_types:
        return []
    try:
        from db.connection import get_conn
        placeholders = ", ".join(["%s"] * len(ep_types))
        with get_conn() as conn:
            rows = conn.execute(
                f"""SELECT title, episode_type, shopify_page_url, buzzsprout_url,
                           cover_image_url, duration_minutes
                    FROM episodes
                    WHERE episode_type IN ({placeholders})
                    ORDER BY deployed_at DESC""",
                ep_types,
            ).fetchall()
        return [
            {
                "title":           r[0],
                "episode_type":    r[1],
                "shopify_page_url":r[2] or r[3],
                "cover_image_url": r[4],
                "duration_minutes":r[5],
            }
            for r in rows
        ]
    except Exception as exc:
        print(f"[hub_updater] episodes DB query failed: {exc}")
        return []


def add_episode_to_hubs(metadata: dict) -> dict[str, str]:
    """Called after a sleep audio episode is deployed.

    Rebuilds each relevant hub from the episodes table (all episodes for that
    hub type, newest first).  Falls back to prepend-only if DB query returns
    nothing (e.g., before migration 012 is applied on older envs).

    Returns {handle: status} for each hub touched.
    """
    ep_type = metadata.get("episode_type") or "sleep_story"
    hubs    = _EPISODE_HUBS.get(ep_type, ["sleep-science-hub"])
    results: dict[str, str] = {}

    for handle in hubs:
        hub_page = _fetch_page(handle)
        if not hub_page:
            results[handle] = "page not found"
            continue

        # Try full rebuild from DB first
        all_episodes = _episodes_for_hub(handle)
        if all_episodes:
            all_cards = "".join(_episode_card(e) for e in all_episodes)
            print(f"[hub_updater] /pages/{handle} — rebuilding from {len(all_episodes)} episodes")
        else:
            # Fallback: prepend new card only (no DB data yet)
            existing  = _extract_items(hub_page["body"] or "")
            all_cards = _episode_card(metadata) + ("\n" + existing if existing else "")
            print(f"[hub_updater] /pages/{handle} — prepend-only (no episodes in DB yet)")

        heading  = _HUB_HEADINGS.get(handle, "Audio Library")
        new_body = _upsert_section(hub_page["body"] or "", heading, all_cards)
        try:
            _save_page(hub_page["id"], hub_page["title"], new_body)
            print(f"[hub_updater] /pages/{handle} updated — '{metadata.get('title')}'")
            results[handle] = "updated"
        except Exception as exc:
            print(f"[hub_updater] /pages/{handle} save failed: {exc}")
            results[handle] = f"error: {exc}"

    return results
