#!/usr/bin/env bash
# install_phase3a4.sh — Phase 3A.4: native Shopify Pages publishing
#
# Writes:
#   db/migrations/004_page_columns.sql   — schema additions for page fields + Shopify IDs
#   lib/shopify_admin.py                 — minimal Admin GraphQL client (httpx)
#   workers/shopify_page_builder.py      — markdown → Hive Mind page HTML (matches Issue 14)
#   workers/shopify_publisher.py         — fileCreate (image upload) + pageCreate
#   scripts/publish_page.py              — CLI: python -m scripts.publish_page --issue 15
#
# After install:
#   python -m scripts.publish_page --issue 15 \
#       --page-title "..." \
#       --page-dek "..." \
#       --breadcrumb "..."
#
# Or, if you've already written page_title/page_dek/page_breadcrumb_label into the
# issues row (e.g. via SQL), drop the flags:
#   python -m scripts.publish_page --issue 15
#
# Defaults: isPublished=false (draft). The page won't be public until you flip
# the visibility in Shopify admin yourself.

set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d workers ]] || [[ ! -f config.py ]]; then
    echo "FATAL: run from beezy-agents workspace root" >&2
    exit 1
fi

mkdir -p db/migrations scripts
touch scripts/__init__.py

echo "[install] writing db/migrations/004_page_columns.sql..."
cat > db/migrations/004_page_columns.sql <<'SQLEOF'
-- 004_page_columns.sql
-- Adds page-related fields for native Shopify Pages publishing (Phase 3A.4).

alter table issues
    add column if not exists page_title text,
    add column if not exists page_dek text,
    add column if not exists page_breadcrumb_label text,
    add column if not exists shopify_image_id text,
    add column if not exists shopify_image_url text,
    add column if not exists shopify_page_id text,
    add column if not exists shopify_page_handle text,
    add column if not exists shopify_page_url text,
    add column if not exists page_published_at timestamptz;

create index if not exists idx_issues_shopify_page_id
    on issues (shopify_page_id) where shopify_page_id is not null;
SQLEOF

echo "[install] writing lib/shopify_admin.py..."
cat > lib/shopify_admin.py <<'PYEOF'
"""Minimal Shopify Admin GraphQL client.

Reads SHOPIFY_SHOP_DOMAIN and SHOPIFY_ACCESS_TOKEN from environment.
Defaults to API version 2025-10 (overridable via SHOPIFY_ADMIN_API_VERSION).
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

import httpx


def _config() -> tuple[str, str, str]:
    shop = os.environ.get("SHOPIFY_SHOP_DOMAIN")
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    if not shop or not token:
        raise RuntimeError(
            "SHOPIFY_SHOP_DOMAIN and SHOPIFY_ACCESS_TOKEN must be set in Replit Secrets."
        )
    api_version = os.environ.get("SHOPIFY_ADMIN_API_VERSION", "2025-10")
    url = f"https://{shop}/admin/api/{api_version}/graphql.json"
    return url, token, api_version


def graphql(query: str, variables: Optional[dict[str, Any]] = None,
            timeout_seconds: float = 30.0) -> dict[str, Any]:
    """Execute a GraphQL query/mutation. Returns the `data` payload.

    Raises RuntimeError on HTTP errors or top-level GraphQL errors.
    Does NOT raise on `userErrors` inside individual mutations — caller checks those.
    """
    url, token, _ = _config()
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {"query": query, "variables": variables or {}}

    with httpx.Client(timeout=timeout_seconds) as client:
        try:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body_preview = e.response.text[:500] if e.response.text else "(no body)"
            raise RuntimeError(
                f"Shopify GraphQL HTTP {e.response.status_code}: {body_preview}"
            ) from e

    data = resp.json()
    if "errors" in data and data["errors"]:
        raise RuntimeError(f"Shopify GraphQL errors: {json.dumps(data['errors'])}")
    return data.get("data") or {}
PYEOF

echo "[install] writing workers/shopify_page_builder.py..."
cat > workers/shopify_page_builder.py <<'PYEOF'
"""Build the HTML body for a Hive Mind native Shopify Page.

Matches the existing trybeezybeez.com/pages/* template structure captured from
Issue 14 (alcohol-sleep-architecture-rem-suppression):

    Breadcrumb · metadata bar · H1 · dek · hero image · long-form body ·
    Until-next-issue · product CTA · subscribe CTA · about · back link

Pure function — no I/O, no API calls. Input: a dict with issue fields. Output: HTML string.
"""
from __future__ import annotations

import html as html_lib
from typing import Any


def build_page_html(issue: dict[str, Any]) -> str:
    """Render the full <body> HTML for a Hive Mind Shopify Page."""
    issue_number = issue["number"]
    num_padded = f"{issue_number:03d}"

    breadcrumb = issue.get("page_breadcrumb_label") or "Issue"
    page_title = issue.get("page_title") or issue.get("subject_line") or ""
    page_dek = issue.get("page_dek") or issue.get("preview_text") or ""
    read_time = issue.get("read_time_min") or "?"
    cover_url = issue.get("shopify_image_url") or issue.get("cover_image_url") or ""
    alt_text = f"The Hive Mind Issue {num_padded} — {page_title}"
    long_form_md = issue.get("long_form_body") or ""
    until_next = issue.get("until_next_teaser") or ""

    body_html = _markdown_to_html(long_form_md)

    parts: list[str] = []

    parts.append(
        f'<p><a href="https://trybeezybeez.com">Home</a> / '
        f'<a href="https://trybeezybeez.com/pages/sleep-science-hub">Sleep Science Hub</a> / '
        f'{_esc(breadcrumb)}</p>'
    )
    parts.append(
        f'<p>The Hive Mind · Issue {num_padded} · {_esc(str(read_time))} min read</p>'
    )
    parts.append(f'<h1>{_esc(page_title)}</h1>')
    parts.append(f'<p><em>{_esc(page_dek)}</em></p>')

    if cover_url:
        parts.append(
            f'<p><img src="{_esc(cover_url)}" alt="{_esc(alt_text)}" /></p>'
        )

    parts.append(body_html)

    if until_next:
        parts.append('<h2>Until next issue</h2>')
        parts.append(f'<p>{_esc(until_next)}</p>')

    parts.append('<hr />')

    parts.append(
        '<h2>Built to Support Your Body\'s Natural Rhythm</h2>'
        '<p>Beezy Beez Botanical Extract Sleep Honey is designed to support the wind-down phase '
        'of your circadian cycle — when your body wants to drop into rest, but stress or '
        'overstimulation gets in the way. Clean ingredients. Trusted by 8,500+ five-star customers.</p>'
        '<p><a href="https://trybeezybeez.com/products/honey-sub"><strong>TRY SLEEP HONEY →</strong></a></p>'
    )

    parts.append(
        '<h2>Get The Hive Mind in Your Inbox</h2>'
        '<p>One sleep science deep-dive every three days. No fluff. No products pushed. '
        'Just the research and what it means for your nights.</p>'
        '<p><a href="https://trybeezybeez.com/pages/the-hive-mind">Browse every Hive Mind issue →</a></p>'
    )

    parts.append(
        '<p><strong>About Beezy Beez.</strong> Beezy Beez crafts '
        '<a href="https://trybeezybeez.com/products/honey-sub">botanical extract honey</a> '
        'for people navigating sleep changes after 50. The Hive Mind is the brand\'s editorial '
        'letter on the science and history of rest.</p>'
    )

    parts.append(
        '<p><a href="https://trybeezybeez.com/pages/sleep-science-hub">← Back to the Sleep Science Hub</a></p>'
    )

    return "\n\n".join(parts)


def _esc(s: str) -> str:
    return html_lib.escape(s or "", quote=True)


def _markdown_to_html(md: str) -> str:
    """Convert the Hive Mind markdown body to HTML.

    Uses python-markdown if available; otherwise a fallback that handles the
    subset of markdown actually used in Hive Mind issues:
      paragraphs, `##` h2, `---` hr, **bold**, *italic*, `text`.
    """
    try:
        import markdown as md_lib  # type: ignore
        return md_lib.markdown(md, extensions=["extra"])
    except ImportError:
        return _fallback_markdown(md)


def _fallback_markdown(md: str) -> str:
    import re
    lines = md.split("\n")
    out: list[str] = []
    para_buf: list[str] = []

    def flush_para():
        if para_buf:
            text = " ".join(para_buf).strip()
            if text:
                out.append(f"<p>{_inline(text)}</p>")
            para_buf.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_para()
            continue
        if stripped == "---":
            flush_para()
            out.append("<hr />")
            continue
        if stripped.startswith("## "):
            flush_para()
            out.append(f"<h2>{_inline(stripped[3:])}</h2>")
            continue
        if stripped.startswith("# "):
            flush_para()
            out.append(f"<h1>{_inline(stripped[2:])}</h1>")
            continue
        para_buf.append(stripped)

    flush_para()
    return "\n\n".join(out)


def _inline(text: str) -> str:
    import re
    # Code spans
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Bold then italic (order matters)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)
    return text
PYEOF

echo "[install] writing workers/shopify_publisher.py..."
cat > workers/shopify_publisher.py <<'PYEOF'
"""Shopify Pages publisher for Hive Mind issues.

Two-step flow:
  1. upload_image_to_shopify(higgsfield_url) — fileCreate via Admin GraphQL,
     polls fileStatus until READY, returns {id, url}.
  2. create_page(title, body_html, handle, ...) — pageCreate via Admin GraphQL,
     returns {id, handle, url}.

Requires:
  SHOPIFY_SHOP_DOMAIN, SHOPIFY_ACCESS_TOKEN in environment.
  Shopify custom app scopes: write_content, write_files.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from lib.shopify_admin import graphql


def upload_image_to_shopify(source_url: str, alt: str = "",
                            poll_timeout_seconds: float = 90.0) -> dict[str, str]:
    """Upload an image to Shopify Files API via URL fetch.

    Returns {"id": <File GID>, "url": <Shopify CDN URL>}.
    Raises RuntimeError on userErrors, timeouts, or missing URL.
    """
    if not source_url:
        raise ValueError("source_url is required")

    create_mutation = """
    mutation fileCreate($files: [FileCreateInput!]!) {
        fileCreate(files: $files) {
            files {
                id
                fileStatus
                alt
                ... on MediaImage {
                    image { url }
                }
            }
            userErrors { field message code }
        }
    }
    """
    variables = {
        "files": [{
            "originalSource": source_url,
            "contentType": "IMAGE",
            "alt": alt or "",
        }]
    }

    data = graphql(create_mutation, variables)
    result = data.get("fileCreate") or {}
    user_errors = result.get("userErrors") or []
    if user_errors:
        raise RuntimeError(f"fileCreate userErrors: {user_errors}")

    files = result.get("files") or []
    if not files:
        raise RuntimeError(f"fileCreate returned no files: {result}")

    file_obj = files[0]
    file_id = file_obj["id"]

    image_url = _extract_image_url(file_obj)
    if image_url:
        return {"id": file_id, "url": image_url}

    # Poll until READY (Shopify ingests asynchronously)
    deadline = time.time() + poll_timeout_seconds
    while time.time() < deadline:
        time.sleep(2.0)
        file_obj = _get_file(file_id)
        status = file_obj.get("fileStatus")
        image_url = _extract_image_url(file_obj)
        if image_url:
            return {"id": file_id, "url": image_url}
        if status == "FAILED":
            raise RuntimeError(f"File ingestion FAILED for {file_id}: {file_obj}")

    raise RuntimeError(
        f"File ingestion did not complete in {poll_timeout_seconds}s (file_id={file_id})"
    )


def _get_file(file_id: str) -> dict[str, Any]:
    query = """
    query getFile($id: ID!) {
        node(id: $id) {
            ... on MediaImage {
                id
                fileStatus
                alt
                image { url }
            }
        }
    }
    """
    data = graphql(query, {"id": file_id})
    return data.get("node") or {}


def _extract_image_url(file_obj: dict[str, Any]) -> Optional[str]:
    img = file_obj.get("image") or {}
    return img.get("url") if isinstance(img, dict) else None


def create_page(
    title: str,
    body_html: str,
    handle: str,
    *,
    seo_title: Optional[str] = None,
    seo_description: Optional[str] = None,
    is_published: bool = False,
    image_file_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create a Shopify Page via pageCreate mutation.

    Returns {"id": <Page GID>, "handle": <handle>, "url": <public URL>, "is_published": <bool>}.
    Raises RuntimeError on userErrors.
    """
    mutation = """
    mutation pageCreate($page: PageCreateInput!) {
        pageCreate(page: $page) {
            page {
                id
                handle
                title
                isPublished
                publishedAt
                onlineStoreUrl
            }
            userErrors { field message code }
        }
    }
    """

    page_input: dict[str, Any] = {
        "title": title,
        "body": body_html,
        "handle": handle,
        "isPublished": is_published,
    }

    if seo_title or seo_description:
        page_input["seo"] = {}
        if seo_title:
            page_input["seo"]["title"] = seo_title
        if seo_description:
            page_input["seo"]["description"] = seo_description

    if image_file_id:
        # Theme-dependent: many themes read this metafield for og:image.
        # If yours doesn't, we can add a 2-line Liquid snippet to theme.liquid.
        page_input["metafields"] = [{
            "namespace": "global",
            "key": "image",
            "value": image_file_id,
            "type": "file_reference",
        }]

    data = graphql(mutation, {"page": page_input})
    result = data.get("pageCreate") or {}
    user_errors = result.get("userErrors") or []
    if user_errors:
        raise RuntimeError(f"pageCreate userErrors: {user_errors}")

    page = result.get("page") or {}
    public_url = page.get("onlineStoreUrl") or f"https://trybeezybeez.com/pages/{page.get('handle', handle)}"

    return {
        "id": page.get("id"),
        "handle": page.get("handle"),
        "title": page.get("title"),
        "url": public_url,
        "is_published": bool(page.get("isPublished")),
        "published_at": page.get("publishedAt"),
    }
PYEOF

echo "[install] writing scripts/publish_page.py..."
cat > scripts/publish_page.py <<'PYEOF'
"""Publish a Hive Mind issue as a native Shopify Page.

Reads the issue from postgres, uploads the cover image to Shopify Files API,
creates the Shopify Page (default: isPublished=false / draft), writes the
Shopify IDs back to the issues row, posts a Slack summary with both the
admin-edit URL and the public URL (which is only visible once you flip the
page to live in Shopify admin).

Usage:
    python -m scripts.publish_page --issue 15

If page_title / page_dek / page_breadcrumb_label aren't yet set on the issues
row, pass them via flags (they'll be written to the row too):

    python -m scripts.publish_page --issue 15 \\
        --page-title "How Your Brain Edits Pain While You Sleep" \\
        --page-dek "A 1978 Chicago sleep lab, a recently divorced woman, and what Rosalind Cartwright found in eight hours of EEG tracings." \\
        --breadcrumb "Dreams"

Refuses to re-create if shopify_page_id already exists. Use --force to override.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from typing import Any, Optional

import psycopg

from config import DATABASE_URL
from lib.slack import post_draft
from workers.shopify_page_builder import build_page_html
from workers.shopify_publisher import create_page, upload_image_to_shopify


def _fetch_issue(conn: psycopg.Connection, number: int) -> Optional[dict[str, Any]]:
    cur = conn.execute(
        """
        select number, subject_line, preview_text, character_name, character_year,
               character_location, pillar, topic_summary, page_slug,
               cover_image_prompt, cover_image_url, long_form_body,
               until_next_teaser, read_time_min, status,
               page_title, page_dek, page_breadcrumb_label,
               shopify_image_id, shopify_image_url,
               shopify_page_id, shopify_page_handle, shopify_page_url
        from issues where number = %s
        """,
        (number,),
    )
    row = cur.fetchone()
    if not row:
        return None
    cols = [d.name for d in cur.description]
    return dict(zip(cols, row))


def _update_page_fields(conn: psycopg.Connection, number: int,
                        page_title: Optional[str], page_dek: Optional[str],
                        breadcrumb: Optional[str]) -> None:
    if not (page_title or page_dek or breadcrumb):
        return
    conn.execute(
        """
        update issues set
            page_title = coalesce(%s, page_title),
            page_dek = coalesce(%s, page_dek),
            page_breadcrumb_label = coalesce(%s, page_breadcrumb_label)
        where number = %s
        """,
        (page_title, page_dek, breadcrumb, number),
    )


def _save_shopify_state(conn: psycopg.Connection, number: int,
                        image_info: dict[str, str], page_info: dict[str, Any]) -> None:
    published_at = datetime.utcnow() if page_info.get("is_published") else None
    conn.execute(
        """
        update issues set
            shopify_image_id = %s,
            shopify_image_url = %s,
            shopify_page_id = %s,
            shopify_page_handle = %s,
            shopify_page_url = %s,
            page_published_at = coalesce(%s, page_published_at)
        where number = %s
        """,
        (
            image_info["id"],
            image_info["url"],
            page_info["id"],
            page_info["handle"],
            page_info["url"],
            published_at,
            number,
        ),
    )


def _admin_url_from_gid(page_gid: str, shop: str) -> str:
    # gid://shopify/OnlineStorePage/123456789  ->  numeric_id
    try:
        numeric = page_gid.rsplit("/", 1)[-1]
        return f"https://{shop}/admin/pages/{numeric}"
    except Exception:
        return f"https://{shop}/admin/pages"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--page-title", default=None)
    parser.add_argument("--page-dek", default=None)
    parser.add_argument("--breadcrumb", default=None,
                        help="One-word leaf breadcrumb (e.g. 'Dreams', 'Alcohol').")
    parser.add_argument("--publish", action="store_true",
                        help="Set isPublished=true (live immediately). Default: draft.")
    parser.add_argument("--force", action="store_true",
                        help="Republish even if shopify_page_id already exists (creates a NEW page).")
    parser.add_argument("--no-slack", action="store_true")
    args = parser.parse_args(argv)

    with psycopg.connect(DATABASE_URL) as conn:
        # Write any flag-provided fields back to the row first
        _update_page_fields(conn, args.issue, args.page_title, args.page_dek, args.breadcrumb)

        issue = _fetch_issue(conn, args.issue)
        if not issue:
            print(f"[publish] No issue {args.issue} in DB", file=sys.stderr)
            return 1

        if issue.get("shopify_page_id") and not args.force:
            print(
                f"[publish] REFUSED: Issue {args.issue} already has shopify_page_id={issue['shopify_page_id']}. "
                "Use --force to create a new page anyway (won't delete the old one).",
                file=sys.stderr,
            )
            return 2

        # Sanity-check required fields
        missing: list[str] = []
        for required in ("page_title", "page_dek", "page_slug", "long_form_body", "cover_image_url"):
            if not issue.get(required):
                missing.append(required)
        if missing:
            print(
                f"[publish] REFUSED: Issue {args.issue} missing required fields: {missing}. "
                "Pass --page-title / --page-dek (and ensure page_slug + long_form_body + cover_image_url are set).",
                file=sys.stderr,
            )
            return 3
        if not issue.get("page_breadcrumb_label"):
            print(
                f"[publish] WARNING: Issue {args.issue} has no page_breadcrumb_label. "
                "Breadcrumb will fall back to 'Issue'. Pass --breadcrumb to fix.",
                file=sys.stderr,
            )

    # Upload image to Shopify (outside the DB transaction)
    print(f"[publish] Issue {args.issue} — uploading cover image to Shopify Files API...")
    alt = f"The Hive Mind Issue {args.issue:03d} — {issue['page_title']}"
    try:
        image_info = upload_image_to_shopify(issue["cover_image_url"], alt=alt)
    except Exception as e:
        print(f"[publish] FAILED at image upload: {type(e).__name__}: {e}", file=sys.stderr)
        return 4
    print(f"[publish]   image_id={image_info['id']}")
    print(f"[publish]   image_url={image_info['url']}")

    # Re-fetch issue so the builder sees the new shopify_image_url
    issue["shopify_image_id"] = image_info["id"]
    issue["shopify_image_url"] = image_info["url"]

    # Build the page HTML
    print(f"[publish] building HTML...")
    body_html = build_page_html(issue)
    print(f"[publish]   body HTML length: {len(body_html):,} chars")

    # Create the page
    print(f"[publish] creating Shopify page (isPublished={args.publish})...")
    try:
        page_info = create_page(
            title=issue["page_title"],
            body_html=body_html,
            handle=issue["page_slug"],
            seo_title=issue.get("page_title"),
            seo_description=issue.get("preview_text"),
            is_published=args.publish,
            image_file_id=image_info["id"],
        )
    except Exception as e:
        print(f"[publish] FAILED at page create: {type(e).__name__}: {e}", file=sys.stderr)
        return 5
    print(f"[publish]   page_id={page_info['id']}")
    print(f"[publish]   public_url={page_info['url']}")

    # Persist Shopify state
    with psycopg.connect(DATABASE_URL) as conn:
        _save_shopify_state(conn, args.issue, image_info, page_info)
    print(f"[publish] saved Shopify state to issues row.")

    # Slack
    import os
    shop = os.environ.get("SHOPIFY_SHOP_DOMAIN", "trybeezybeez.myshopify.com")
    admin_url = _admin_url_from_gid(page_info["id"], shop)

    if not args.no_slack:
        post_draft(
            title=f"Hive Mind Issue {args.issue} — Shopify Page {'PUBLISHED' if args.publish else 'created as DRAFT'}",
            summary_lines=[
                f"*Title:* {issue['page_title']}",
                f"*Dek:* {issue['page_dek']}",
                f"*Breadcrumb:* {issue.get('page_breadcrumb_label') or '(none)'}",
                f"*Handle:* {page_info['handle']}",
                f"*Status:* {'live' if args.publish else 'draft (not yet visible to public)'}",
            ],
            body=(
                f"*Admin edit URL:* {admin_url}\n"
                f"*Public URL:* {page_info['url']} "
                f"{'(live)' if args.publish else '(will be live once you flip visibility in Shopify admin)'}\n"
                f"*Shopify image URL:* {image_info['url']}\n\n"
                "Flip the page to *Visible* in Shopify admin once you've eyeballed the rendering. "
                "If og:image still shows the Beezy logo on social shares, the theme needs a 2-line "
                "Liquid snippet to read the `global.image` page metafield."
            ),
            image_url=image_info["url"],
            image_alt=alt,
        )
        print("[publish] posted to Slack")

    print(f"\n[publish] DONE. Review the page in Shopify admin: {admin_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
PYEOF

# Ensure markdown library is available
echo "[install] ensuring python-markdown is installed..."
pip install markdown --break-system-packages --quiet 2>&1 | tail -3 || true

# Run the migration
echo "[install] applying db/migrations/004_page_columns.sql..."
python -c "
import os, psycopg
from config import DATABASE_URL
with psycopg.connect(DATABASE_URL) as conn:
    with open('db/migrations/004_page_columns.sql') as f:
        conn.execute(f.read())
    conn.commit()
print('  migration applied OK')
"

# Syntax checks
echo "[install] syntax checks..."
python -c "import ast; ast.parse(open('lib/shopify_admin.py').read()); print('  lib/shopify_admin.py OK')"
python -c "import ast; ast.parse(open('workers/shopify_page_builder.py').read()); print('  workers/shopify_page_builder.py OK')"
python -c "import ast; ast.parse(open('workers/shopify_publisher.py').read()); print('  workers/shopify_publisher.py OK')"
python -c "import ast; ast.parse(open('scripts/publish_page.py').read()); print('  scripts/publish_page.py OK')"

echo ""
echo "[install] DONE."
echo ""
echo "To publish Issue 15 as a DRAFT page (not yet visible to public):"
echo ""
echo "  python -m scripts.publish_page --issue 15 \\"
echo "      --page-title \"<the H1>\" \\"
echo "      --page-dek \"<italic 1-sentence dek>\" \\"
echo "      --breadcrumb \"<one-word topic, e.g. Dreams>\""
echo ""
echo "The page will be created in DRAFT status. You flip it to live in Shopify admin"
echo "once you've eyeballed the rendering."
echo ""
echo "Note: --page-title/--page-dek/--breadcrumb values are written back to the issues row,"
echo "so re-publishing the same issue won't need them again."
