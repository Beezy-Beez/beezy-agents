"""
Migrate existing live Shopify pages to the new hm_subscriber cookie gate.

Strategy A — Issue pages (full rebuild from DB):
  All data needed by build_page_html() lives in the `issues` table.
  Rebuild each published issue page and push via pageUpdate.

Strategy B — Episode pages (targeted HTML patch):
  Episode pages store script/description only in the original metadata payload
  (not in DB), so a full rebuild isn't possible.  Instead we surgically remove
  the old newsletter form divs, archive link, and subscriber JS, then inject
  the new gate block just before the back-link paragraph.

Run:
  python -m scripts.migrate_hm_gate [--dry-run] [--issues] [--episodes]
  (with no flags both are run)
"""
import sys, os, re, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from lib.hm_gate import build_gate, build_gate_episode
from lib.shopify_admin import graphql


# ── Issue page rebuild ────────────────────────────────────────────────────────

def _all_issues_with_pages() -> list[dict]:
    from db.connection import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT number, subject_line, page_dek, page_breadcrumb_label,
                      long_form_body, until_next_teaser, read_time_min,
                      shopify_image_url, cover_image_url, shopify_page_id,
                      shopify_page_handle, shopify_page_url, page_title,
                      buzzsprout_url
               FROM issues
               WHERE shopify_page_id IS NOT NULL
               ORDER BY number"""
        ).fetchall()
    return [
        {
            "number":               r[0],
            "subject_line":         r[1],
            "page_dek":             r[2],
            "page_breadcrumb_label":r[3],
            "long_form_body":       r[4],
            "until_next_teaser":    r[5],
            "read_time_min":        r[6],
            "shopify_image_url":    r[7],
            "cover_image_url":      r[8],
            "shopify_page_id":      r[9],
            "shopify_page_handle":  r[10],
            "shopify_page_url":     r[11],
            "page_title":           r[12],
            "buzzsprout_url":       r[13],
        }
        for r in rows
    ]


def _update_shopify_page(page_id: str, new_body: str) -> None:
    data = graphql(
        """mutation pageUpdate($id: ID!, $page: PageUpdateInput!) {
             pageUpdate(id: $id, page: $page) {
               page { id handle }
               userErrors { field message }
             }
           }""",
        {"id": page_id, "page": {"body": new_body}},
    )
    errors = (data.get("pageUpdate") or {}).get("userErrors") or []
    if errors:
        raise RuntimeError(f"pageUpdate errors: {errors}")


def rebuild_issue_pages(dry_run: bool = False) -> None:
    from workers.shopify_page_builder import build_page_html
    issues = _all_issues_with_pages()
    print(f"\n── Issue pages: rebuilding {len(issues)} pages ──")
    for issue in issues:
        n = issue.get("number", "?")
        handle = issue.get("shopify_page_handle") or issue.get("shopify_page_url", "")
        page_id = issue["shopify_page_id"]
        print(f"  Issue {n:03d}  handle={handle}  id={page_id}")
        if dry_run:
            # Verify build_page_html runs without error
            html = build_page_html(issue)
            has_gate = 'id="hm-gate"' in html
            has_old  = 'id="hive-mind-pre-sub"' in html or 'bb_hivemind_sub' in html
            print(f"    [DRY RUN] has_gate={has_gate}  has_old={has_old}  len={len(html)}")
            continue
        try:
            html = build_page_html(issue)
            _update_shopify_page(page_id, html)
            print(f"    OK — gate injected")
        except Exception as exc:
            print(f"    ERROR: {exc}")


# ── Episode page patching ─────────────────────────────────────────────────────

_PAGE_QUERY = """
query($q: String!) {
  pages(first: 1, query: $q) {
    edges { node { id handle body } }
  }
}
"""

def _fetch_page_by_handle(handle: str) -> dict | None:
    try:
        data  = graphql(_PAGE_QUERY, {"q": f"handle:{handle}"})
        edges = (data.get("pages") or {}).get("edges") or []
        return edges[0]["node"] if edges else None
    except Exception as exc:
        print(f"    fetch error: {exc}")
        return None


def _patch_episode_page(body: str) -> tuple[str, bool]:
    """Remove old subscribe elements and inject the new gate.

    Returns (new_body, changed).
    """
    original = body

    # 1. Remove both epis-newsletter divs (top + bottom forms)
    body = re.sub(
        r'<div class="epis-newsletter"[^>]*>.*?</div>\s*',
        "",
        body,
        flags=re.DOTALL,
    )

    # 2. Remove the archive link div
    body = re.sub(
        r'<div id="hm-archive-link"[^>]*>.*?</div>\s*',
        "",
        body,
        flags=re.DOTALL,
    )

    # 3. Remove the old subscriber JS
    body = re.sub(
        r'<script>\(function\(\)\{var SUB_KEY="bb_hivemind_sub".*?</script>\s*',
        "",
        body,
        flags=re.DOTALL,
    )

    # 4. Inject new gate just before the back-link paragraph
    gate = build_gate_episode()
    back_pattern = r'(<p class="epis-back">)'
    if re.search(back_pattern, body):
        body = re.sub(back_pattern, lambda m: gate + "\n" + m.group(1), body, count=1)
    else:
        body = body + "\n" + gate

    # 5. Remove any already-injected gate from a previous run (idempotent)
    # If we matched and replaced, there should only be one gate. But if the
    # gate was already there AND we re-ran, we'd have two.  Collapse duplicates.
    gate_sections = list(re.finditer(r'<div id="hm-gate"', body))
    if len(gate_sections) > 1:
        # Keep only the last occurrence (closest to back-link)
        first_start = gate_sections[0].start()
        second_start = gate_sections[1].start()
        # Find end of first gate block (next </script> after it)
        first_end_m = re.search(r'</script>', body[first_start:second_start])
        if first_end_m:
            first_end = first_start + first_end_m.end()
            body = body[:first_start] + body[first_end:]

    changed = body.strip() != original.strip()
    return body, changed


def _all_meditation_episodes() -> list[dict]:
    from db.connection import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT episode_id, title, episode_type, shopify_page_url
               FROM episodes
               WHERE episode_type IN ('guided_meditation', 'affirmation_meditation',
                                      'morning_meditation', 'soundscape')
                 AND shopify_page_url IS NOT NULL
               ORDER BY deployed_at DESC"""
        ).fetchall()
    return [
        {"episode_id": r[0], "title": r[1], "episode_type": r[2], "shopify_page_url": r[3]}
        for r in rows
    ]


def _handle_from_url(url: str) -> str:
    """Extract Shopify page handle from a trybeezybeez.com/pages/... URL."""
    m = re.search(r"/pages/([^/?#]+)", url)
    return m.group(1) if m else ""


def patch_episode_pages(dry_run: bool = False) -> None:
    episodes = _all_meditation_episodes()
    print(f"\n── Episode pages: patching {len(episodes)} meditation/soundscape pages ──")
    for ep in episodes:
        title  = ep["title"][:45]
        handle = _handle_from_url(ep["shopify_page_url"])
        print(f"  {ep['episode_type']}  handle={handle}  '{title}'")

        page = _fetch_page_by_handle(handle)
        if not page:
            print(f"    SKIP — page not found on Shopify")
            continue

        new_body, changed = _patch_episode_page(page["body"] or "")

        has_gate = 'id="hm-gate"' in new_body
        has_old  = 'class="epis-newsletter"' in new_body or 'id="hm-archive-link"' in new_body
        print(f"    changed={changed}  has_gate={has_gate}  has_old_form={has_old}")

        if dry_run:
            print(f"    [DRY RUN] skipping write")
            continue
        if not changed:
            print(f"    already up-to-date — skipping")
            continue
        try:
            _update_shopify_page(page["id"], new_body)
            print(f"    OK — gate injected")
        except Exception as exc:
            print(f"    ERROR: {exc}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Migrate Hive Mind subscription gate")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--issues",   action="store_true")
    parser.add_argument("--episodes", action="store_true")
    args = parser.parse_args()

    run_issues   = args.issues   or not (args.issues or args.episodes)
    run_episodes = args.episodes or not (args.issues or args.episodes)

    if args.dry_run:
        print("DRY RUN — no Shopify writes")

    if run_issues:
        rebuild_issue_pages(dry_run=args.dry_run)
    if run_episodes:
        patch_episode_pages(dry_run=args.dry_run)

    print("\nDone.")


if __name__ == "__main__":
    main()
