"""Publish a Hive Mind issue as a native Shopify Page.

Default: isPublished=false (page created as draft). You flip to live in Shopify admin.
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


PLACEHOLDER_MARKERS = (
    "YOUR REAL", "YOUR_REAL", "PLACEHOLDER", "EXAMPLE TEXT",
    "PASTE_", "PASTE HERE", "REPLACE THIS", "REPLACE_THIS",
    "FILL IN", "FILL_IN", "TODO", "XXXXX",
)


def _looks_like_placeholder(value: Optional[str]) -> bool:
    if not value:
        return False
    v = value.strip()
    if not v:
        return False
    if v.startswith("<") and v.endswith(">"):
        return True
    upper = v.upper()
    return any(marker in upper for marker in PLACEHOLDER_MARKERS)


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


def _checkpoint_image(conn: psycopg.Connection, number: int, image_info: dict[str, str]) -> None:
    conn.execute(
        "update issues set shopify_image_id = %s, shopify_image_url = %s where number = %s",
        (image_info["id"], image_info["url"], number),
    )


def _save_page_state(conn: psycopg.Connection, number: int, page_info: dict[str, Any]) -> None:
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
            page_info["id"], page_info["handle"], page_info["url"],
            published_at, number,
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
    parser.add_argument("--breadcrumb", default=None)
    parser.add_argument("--publish", action="store_true",
                        help="Set isPublished=true. Default: draft.")
    parser.add_argument("--force", action="store_true",
                        help="Create a new page even if shopify_page_id already exists.")
    parser.add_argument("--no-slack", action="store_true")
    args = parser.parse_args(argv)

    # Flag-value placeholder check
    for label, value in (("--page-title", args.page_title),
                         ("--page-dek", args.page_dek),
                         ("--breadcrumb", args.breadcrumb)):
        if _looks_like_placeholder(value):
            print(
                f"[publish] REFUSED: {label} looks like a placeholder ({value!r}). "
                "Pass the actual value or omit the flag.",
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
                "Use --force to create a NEW page.",
                file=sys.stderr,
            )
            return 2

        missing = [f for f in ("page_title", "page_dek", "page_slug", "long_form_body", "cover_image_url")
                   if not issue.get(f)]
        if missing:
            print(f"[publish] REFUSED: Issue {args.issue} missing required fields: {missing}.", file=sys.stderr)
            return 3

        for field in ("page_title", "page_dek", "page_breadcrumb_label"):
            if _looks_like_placeholder(issue.get(field)):
                print(
                    f"[publish] REFUSED: Issue {args.issue} has a placeholder in {field} ({issue.get(field)!r}). "
                    f"Pass --{field.replace('_','-').replace('page-breadcrumb-label','breadcrumb')} with the real value.",
                    file=sys.stderr,
                )
                return 7

    alt = f"The Hive Mind Issue {args.issue:03d} — {issue['page_title']}"

    if issue.get("shopify_image_id") and issue.get("shopify_image_url"):
        print(f"[publish] reusing existing Shopify image: {issue['shopify_image_id']}")
        image_info = {"id": issue["shopify_image_id"], "url": issue["shopify_image_url"]}
    else:
        print(f"[publish] uploading cover to Shopify Files API...")
        try:
            image_info = upload_image_to_shopify(issue["cover_image_url"], alt=alt)
        except Exception as e:
            print(f"[publish] FAILED at image upload: {type(e).__name__}: {e}", file=sys.stderr)
            return 4
        print(f"[publish]   image_id={image_info['id']}")
        with psycopg.connect(DATABASE_URL) as conn:
            _checkpoint_image(conn, args.issue, image_info)
        print(f"[publish]   checkpointed image to DB")

    issue["shopify_image_id"] = image_info["id"]
    issue["shopify_image_url"] = image_info["url"]

    print(f"[publish] building page HTML...")
    body_html = build_page_html(issue)
    print(f"[publish]   HTML length: {len(body_html):,} chars")

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
                f"*Public URL:* {page_info['url']}\n"
                f"*Shopify image URL:* {image_info['url']}\n\n"
                "Flip to *Visible* in Shopify admin once you've eyeballed it."
            ),
            image_url=image_info["url"],
            image_alt=alt,
        )
        print("[publish] posted to Slack")

    print(f"\n[publish] DONE. Review in Shopify admin: {admin_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
