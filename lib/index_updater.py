"""
lib/index_updater.py — shared utility for updating Shopify hub/index pages.

Inserts a new card at position 0 (most-recent-first) in the managed
HUB_ITEMS section of any hub page.  Supported handles:

    sleep-science-hub       /pages/sleep-science-hub
    the-hive-mind           /pages/the-hive-mind
    meditation-library      /pages/meditation-library
    morning-wellness-hub    /pages/morning-wellness-hub

All structural state (sentinel markers, section wrapper, heading) lives in
workers/hub_updater.py which this module wraps — no duplication.

Public API
──────────
    update_index_page(handle, card_html, page_type) -> str
        Prepend card_html at the top of the managed section.
        Returns "updated:{handle}" | "not_found:{handle}" | "error:{handle}:{reason}"

    VALID_HANDLES  — frozenset of recognised handle strings
"""
from __future__ import annotations

from workers.hub_updater import (
    _HUB_HEADINGS,
    _extract_items,
    _fetch_page,
    _save_page,
    _upsert_section,
)

# page_type fallback headings when handle is not in _HUB_HEADINGS
_PAGE_TYPE_HEADINGS: dict[str, str] = {
    "hive_mind":          "The Hive Mind — All Issues",
    "sleep_story":        "Sleep Science Deep-Dives",
    "meditation":         "Guided Meditations",
    "morning_meditation": "Morning Wellness Audio",
}

VALID_HANDLES: frozenset[str] = frozenset({
    "sleep-science-hub",
    "the-hive-mind",
    "meditation-library",
    "morning-wellness-hub",
})


def update_index_page(handle: str, card_html: str, page_type: str) -> str:
    """Prepend card_html at position 0 in the managed section of /pages/{handle}.

    Args:
        handle:    Shopify page handle, e.g. "sleep-science-hub"
        card_html: Pre-rendered HTML for the single new card to insert
        page_type: Content category hint for section heading:
                   hive_mind | sleep_story | meditation | morning_meditation

    Returns a status string — never raises; errors are returned as strings.
    """
    if handle not in VALID_HANDLES:
        msg = f"invalid_handle:{handle}"
        print(f"[index_updater] {msg}")
        return msg

    page = _fetch_page(handle)
    if not page:
        msg = f"not_found:{handle}"
        print(f"[index_updater] /pages/{handle} not found in Shopify")
        return msg

    existing   = _extract_items(page["body"] or "")
    new_items  = card_html + ("\n" + existing if existing else "")
    heading    = _HUB_HEADINGS.get(handle) or _PAGE_TYPE_HEADINGS.get(page_type, "Archive")
    new_body   = _upsert_section(page["body"] or "", heading, new_items)

    try:
        _save_page(page["id"], page["title"], new_body)
        print(f"[index_updater] /pages/{handle} updated ({page_type})")
        return f"updated:{handle}"
    except Exception as exc:
        msg = f"error:{handle}:{exc}"
        print(f"[index_updater] /pages/{handle} save failed: {exc}")
        return msg
