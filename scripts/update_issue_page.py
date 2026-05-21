"""Rebuild and update a Hive Mind issue's Shopify page body in-place.

Preserves shopify_page_id and shopify_page_url. Does NOT toggle visibility.
Use this to fix a broken page after the builder is improved.

Usage:
    python -m scripts.update_issue_page --issue 15
    python -m scripts.update_issue_page --issue 15 --template-suffix zipifypages
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg

from config import NEON_DATABASE_URL
from lib.slack import post_draft
from workers.shopify_page_builder import build_page_html
from workers.shopify_publisher import update_page


def _fetch_issue(conn: psycopg.Connection, number: int):
    cur = conn.execute(
        """
        select number, page_title, page_dek, page_breadcrumb_label, page_slug,
               long_form_body, until_next_teaser, read_time_min,
               cover_image_url, shopify_image_id, shopify_image_url,
               shopify_page_id, shopify_page_handle, shopify_page_url, preview_text
        from issues where number = %s
        """,
        (number,),
    )
    row = cur.fetchone()
    if not row:
        return None
    cols = [d.name for d in cur.description]
    return dict(zip(cols, row))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--template-suffix", default="zipifypages",
                        help="Shopify template suffix. Default: zipifypages (matches existing pages).")
    parser.add_argument("--no-slack", action="store_true")
    args = parser.parse_args(argv)

    with psycopg.connect(NEON_DATABASE_URL) as conn:
        issue = _fetch_issue(conn, args.issue)

    if not issue:
        print(f"[update] No issue {args.issue} in DB", file=sys.stderr)
        return 1
    if not issue.get("shopify_page_id"):
        print(f"[update] Issue {args.issue} has no shopify_page_id. Run publish_page.py first.", file=sys.stderr)
        return 2

    print(f"[update] rebuilding page HTML for issue {args.issue}...")
    body_html = build_page_html(issue)
    print(f"[update]   HTML length: {len(body_html):,} chars")

    print(f"[update] pushing pageUpdate to Shopify ({issue['shopify_page_id']})...")
    try:
        page_info = update_page(
            page_id=issue["shopify_page_id"],
            title=issue["page_title"],
            body_html=body_html,
            seo_title=issue.get("page_title"),
            seo_description=issue.get("preview_text"),
            image_file_id=issue.get("shopify_image_id"),
            template_suffix=args.template_suffix,
        )
    except Exception as e:
        print(f"[update] FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 3

    print(f"[update]   page handle: {page_info['handle']}")
    print(f"[update]   public URL:  {page_info['url']}")
    print(f"[update]   template:    {page_info.get('template_suffix')}")
    print(f"[update]   isPublished: {page_info['is_published']}")

    shop = os.environ.get("SHOPIFY_SHOP_DOMAIN", "trybeezybeez.myshopify.com")
    numeric = page_info["id"].rsplit("/", 1)[-1]
    admin_url = f"https://{shop}/admin/pages/{numeric}"

    if not args.no_slack:
        post_draft(
            title=f"Hive Mind Issue {args.issue} — Page body UPDATED in-place",
            summary_lines=[
                f"*Title:* {issue['page_title']}",
                f"*Handle:* {page_info['handle']}",
                f"*Visibility:* {'Visible' if page_info.get('is_published') else 'Hidden (still draft)'}",
                f"*Body length:* {len(body_html):,} chars",
                f"*Template:* {page_info.get('template_suffix') or '(default)'}",
            ],
            body=(
                f"*Admin edit URL:* {admin_url}\n"
                f"*Public URL:* {page_info['url']}\n\n"
                "Page body regenerated to match the existing Hive Mind template "
                "(byte-for-byte equivalent to Issue 14's layout: inline-styled, "
                "boxed callout, product banner, subscribe form). "
                "Eyeball in admin; flip to Visible on publish day."
            ),
            image_url=issue.get("shopify_image_url"),
            image_alt=f"The Hive Mind Issue {args.issue}",
        )
        print("[update] posted to Slack")

    print(f"\n[update] DONE. Admin: {admin_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
