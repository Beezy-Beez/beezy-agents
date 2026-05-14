#!/usr/bin/env bash
# fix_phase3a4_v1.sh
# Two patches to Phase 3A.4:
#   1. workers/shopify_publisher.py — drop `onlineStoreUrl` from pageCreate response
#      selection (field doesn't exist on Page in Admin API 2025-10). Always construct
#      the public URL from `handle` instead.
#   2. scripts/publish_page.py — checkpoint shopify_image_id and shopify_image_url to
#      the DB immediately after a successful image upload, so retries reuse the existing
#      Shopify File instead of re-uploading.
#
# Idempotent.

set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d workers ]] || [[ ! -f config.py ]]; then
    echo "FATAL: run from beezy-agents workspace root" >&2
    exit 1
fi

echo "[fix] rewriting workers/shopify_publisher.py (drop onlineStoreUrl)..."
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


PUBLIC_HOST = "https://trybeezybeez.com"


def upload_image_to_shopify(source_url: str, alt: str = "",
                            poll_timeout_seconds: float = 90.0) -> dict[str, str]:
    """Upload an image to Shopify Files API via URL fetch.

    Returns {"id": <File GID>, "url": <Shopify CDN URL>}.
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

    Returns {"id": <Page GID>, "handle": <handle>, "url": <public URL>, "is_published": <bool>, "published_at": <iso or None>}.
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
    page_handle = page.get("handle") or handle
    public_url = f"{PUBLIC_HOST}/pages/{page_handle}"

    return {
        "id": page.get("id"),
        "handle": page_handle,
        "title": page.get("title"),
        "url": public_url,
        "is_published": bool(page.get("isPublished")),
        "published_at": page.get("publishedAt"),
    }
PYEOF
echo "[fix]   workers/shopify_publisher.py ($(wc -l < workers/shopify_publisher.py) lines)"

echo "[fix] rewriting scripts/publish_page.py (add image checkpoint)..."
cat > scripts/publish_page.py <<'PYEOF'
"""Publish a Hive Mind issue as a native Shopify Page.

Reads the issue from postgres, uploads the cover image to Shopify Files API
(or reuses an existing shopify_image_id if one's already saved), creates the
Shopify Page (default: isPublished=false / draft), writes the Shopify IDs back
to the issues row, posts a Slack summary.

Usage:
    python -m scripts.publish_page --issue 15 \\
        --page-title "<H1>" \\
        --page-dek "<italic dek>" \\
        --breadcrumb "<one-word topic>"

After first successful run, the flags can be dropped:
    python -m scripts.publish_page --issue 15  # uses stored fields

Refuses to re-create if shopify_page_id already exists. Use --force to override.
"""
from __future__ import annotations

import argparse
import os
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


def _checkpoint_image(conn: psycopg.Connection, number: int,
                      image_info: dict[str, str]) -> None:
    conn.execute(
        "update issues set shopify_image_id = %s, shopify_image_url = %s where number = %s",
        (image_info["id"], image_info["url"], number),
    )


def _save_page_state(conn: psycopg.Connection, number: int,
                     page_info: dict[str, Any]) -> None:
    published_at = datetime.utcnow() if page_info.get("is_published") else None
    conn.execute(
        """
        update issues set
            shopify_page_id = %s,
            shopify_page_handle = %s,
            shopify_page_url = %s,
            page_published_at = coalesce(%s, page_published_at)
        where number = %s
        """,
        (
            page_info["id"],
            page_info["handle"],
            page_info["url"],
            published_at,
            number,
        ),
    )


def _admin_url_from_gid(page_gid: str, shop: str) -> str:
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
                        help="Set isPublished=true. Default: draft.")
    parser.add_argument("--force", action="store_true",
                        help="Create a new page even if shopify_page_id already exists.")
    parser.add_argument("--no-slack", action="store_true")
    args = parser.parse_args(argv)

    # Sanity-check the page-title/dek/breadcrumb values (catch placeholder paste)
    for label, value in (("--page-title", args.page_title),
                         ("--page-dek", args.page_dek),
                         ("--breadcrumb", args.breadcrumb)):
        if value and value.startswith("<") and value.endswith(">"):
            print(
                f"[publish] REFUSED: {label} looks like a placeholder ({value!r}). "
                "Pass the actual value or omit the flag entirely.",
                file=sys.stderr,
            )
            return 6

    with psycopg.connect(DATABASE_URL) as conn:
        _update_page_fields(conn, args.issue, args.page_title, args.page_dek, args.breadcrumb)
        issue = _fetch_issue(conn, args.issue)
        if not issue:
            print(f"[publish] No issue {args.issue} in DB", file=sys.stderr)
            return 1

        if issue.get("shopify_page_id") and not args.force:
            print(
                f"[publish] REFUSED: Issue {args.issue} already has shopify_page_id={issue['shopify_page_id']}. "
                "Use --force to create a NEW page (the old one stays — delete manually if needed).",
                file=sys.stderr,
            )
            return 2

        missing: list[str] = []
        for required in ("page_title", "page_dek", "page_slug", "long_form_body", "cover_image_url"):
            if not issue.get(required):
                missing.append(required)
        if missing:
            print(
                f"[publish] REFUSED: Issue {args.issue} missing required fields: {missing}.",
                file=sys.stderr,
            )
            return 3

        # Also detect stored placeholder values from a prior buggy run
        for field in ("page_title", "page_dek", "page_breadcrumb_label"):
            val = issue.get(field) or ""
            if val.startswith("<") and val.endswith(">"):
                print(
                    f"[publish] REFUSED: Issue {args.issue} has a placeholder stored in {field} ({val!r}). "
                    f"Pass --{field.replace('_', '-').replace('page-breadcrumb-label', 'breadcrumb')} with the real value to overwrite it.",
                    file=sys.stderr,
                )
                return 7

        if not issue.get("page_breadcrumb_label"):
            print(
                f"[publish] WARNING: page_breadcrumb_label not set. Falls back to 'Issue'.",
                file=sys.stderr,
            )

    # Image: reuse if already uploaded, otherwise upload + checkpoint
    alt = f"The Hive Mind Issue {args.issue:03d} — {issue['page_title']}"
    if issue.get("shopify_image_id") and issue.get("shopify_image_url"):
        print(f"[publish] reusing existing Shopify image: {issue['shopify_image_id']}")
        image_info = {"id": issue["shopify_image_id"], "url": issue["shopify_image_url"]}
    else:
        print(f"[publish] uploading cover image to Shopify Files API...")
        try:
            image_info = upload_image_to_shopify(issue["cover_image_url"], alt=alt)
        except Exception as e:
            print(f"[publish] FAILED at image upload: {type(e).__name__}: {e}", file=sys.stderr)
            return 4
        print(f"[publish]   image_id={image_info['id']}")
        print(f"[publish]   image_url={image_info['url']}")
        with psycopg.connect(DATABASE_URL) as conn:
            _checkpoint_image(conn, args.issue, image_info)
        print(f"[publish]   image checkpointed to DB")

    issue["shopify_image_id"] = image_info["id"]
    issue["shopify_image_url"] = image_info["url"]

    print(f"[publish] building page HTML...")
    body_html = build_page_html(issue)
    print(f"[publish]   body HTML length: {len(body_html):,} chars")

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

    with psycopg.connect(DATABASE_URL) as conn:
        _save_page_state(conn, args.issue, page_info)
    print(f"[publish] saved page state to issues row.")

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
                "Flip the page to *Visible* in Shopify admin once you've eyeballed it."
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
echo "[fix]   scripts/publish_page.py ($(wc -l < scripts/publish_page.py) lines)"

echo "[fix] syntax checks..."
python -c "import ast; ast.parse(open('workers/shopify_publisher.py').read()); print('  workers/shopify_publisher.py OK')"
python -c "import ast; ast.parse(open('scripts/publish_page.py').read()); print('  scripts/publish_page.py OK')"

echo ""
echo "[fix] DONE."
echo ""
echo "Run with your ACTUAL values (not placeholders this time):"
echo ""
echo "  python -m scripts.publish_page --issue 15 \\"
echo "      --page-title 'YOUR REAL H1 HERE' \\"
echo "      --page-dek 'YOUR REAL ITALIC DEK HERE' \\"
echo "      --breadcrumb 'YourWord'"
echo ""
echo "The script now refuses any value that starts with < and ends with > to prevent"
echo "the placeholder-paste mistake. It will also refuse if the DB row currently has"
echo "stored placeholders — pass --page-title / --page-dek / --breadcrumb with real"
echo "values and they'll overwrite."
echo ""
echo "Image upload is also skipped on retry since it already succeeded — your existing"
echo "Shopify File at gid://shopify/MediaImage/41701139742969 will be reused."
