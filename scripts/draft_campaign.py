"""
CLI for Phase 3A.5 — Klaviyo campaign drafter.

Usage:
    python -m scripts.draft_campaign --issue N [--dry-run]

--dry-run: builds and prints the email HTML without calling Klaviyo.
           Useful to eyeball the email before creating the draft.

After a successful run:
  - Klaviyo campaign is in DRAFT status
  - DB issue row: status='scheduled', klaviyo_campaign_id set
  - Slack notification posted with admin link
  - Flip the Shopify page to Visible, then schedule/send from Klaviyo
"""
from __future__ import annotations

import argparse
import sys

import psycopg

from config import NEON_DATABASE_URL
from lib.email_builder import build_email_html
from workers.klaviyo_campaign import create_campaign_for_issue


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draft Klaviyo campaign for a Hive Mind issue.")
    parser.add_argument("--issue", type=int, required=True, help="Issue number")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build email HTML and print stats only. No Klaviyo API calls.")
    args = parser.parse_args(argv)

    if args.dry_run:
        with psycopg.connect(NEON_DATABASE_URL) as conn:
            row = conn.execute(
                "SELECT number, subject_line, preview_text, page_slug, email_teaser_body "
                "FROM issues WHERE number = %s",
                (args.issue,),
            ).fetchone()
        if not row:
            print(f"Issue {args.issue} not found in DB.", file=sys.stderr)
            return 1

        issue = dict(zip(
            ["number", "subject_line", "preview_text", "page_slug", "email_teaser_body"],
            row,
        ))
        html = build_email_html(issue)

        print(f"Issue:         {issue['number']:03d}")
        print(f"Subject:       {issue['subject_line']}")
        print(f"Preview:       {issue['preview_text']}")
        print(f"Email HTML:    {len(html):,} chars")
        print(f"CTA URL slug:  {issue['page_slug']}?s=1&utm_...")
        print()
        print("--- FIRST 800 CHARS OF EMAIL HTML ---")
        print(html[:800])
        print()
        print("DRY-RUN — no Klaviyo calls made.")
        return 0

    print(f"Creating Klaviyo campaign for Issue {args.issue}...")
    try:
        result = create_campaign_for_issue(args.issue)
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print()
    print("=" * 60)
    print("DONE")
    print(f"Campaign ID:  {result['campaign_id']}")
    print(f"Template ID:  {result['template_id']}")
    print(f"Message ID:   {result['message_id']}")
    print(f"Admin URL:    {result['admin_url']}")
    print()
    print("Next steps:")
    print("  1. Flip Issue page to Visible in Shopify")
    print("  2. Update /pages/sleep-science-hub and /pages/the-hive-mind indexes")
    print("  3. Schedule or send the campaign from Klaviyo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
