#!/usr/bin/env python3
"""
fix_issues_scheduling.py  --  ONE-SHOT repair script. Run once from ~/workspace:

    python3 fix_issues_scheduling.py

It does three things, then verifies:

  1. Backfills scheduled_send_at on every existing issue (14,15,16,17,20),
     derived from the fixed 3-day cadence anchored on Issue 014 = May 15, 2026.
     This is the column publish_and_index reads to know what sends today;
     it was NULL on every row, including the issues that already sent.

  2. Backfills Issue 016's cover image (cover_image_url + shopify_image_url),
     which was NULL -- publish_and_index needs it for the Sleep Science Hub
     featured block.

  3. Registers Issue 018 (built by hand this session) in the issues table so
     the generator's new first-gap numbering counts it and the chain continues
     cleanly to 019, chaining the next topic from 018's until_next_teaser.

Safe to re-run: every write is idempotent (UPDATE / INSERT ... ON CONFLICT).
"""
import sys
sys.path.insert(0, ".")

from datetime import date, datetime, timedelta, timezone

import psycopg
from config import NEON_DATABASE_URL

# ── Cadence -- MUST match the constants in workers/run.py ─────────────────────
ANCHOR_ISSUE = 14
ANCHOR_DATE = date(2026, 5, 15)        # Issue 014 send date
CADENCE_DAYS = 3


def scheduled_send_at(n: int) -> datetime:
    """Send timestamp for issue n. Stored at 12:00 UTC on the send date so that
    scheduled_send_at::date always yields the correct calendar send date."""
    d = ANCHOR_DATE + timedelta(days=(n - ANCHOR_ISSUE) * CADENCE_DAYS)
    return datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)


IMG_016 = ("https://d8j0ntlcm91z4.cloudfront.net/user_3D95Y4KmkeRJAvb6MGHFgJfBpIh/"
           "hf_20260519_141656_221e77ef-7aaf-448c-b20f-7707f15f8d2e.png")
IMG_018 = ("https://d8j0ntlcm91z4.cloudfront.net/user_3D95Y4KmkeRJAvb6MGHFgJfBpIh/"
           "hf_20260519_155148_04960718-3758-4097-a63b-557138d7171c.png")


def main() -> int:
    with psycopg.connect(NEON_DATABASE_URL) as conn:
        # ── 1. Backfill scheduled_send_at on all existing issues ─────────────
        print("=== 1. Backfilling scheduled_send_at ===")
        nums = [r[0] for r in conn.execute(
            "select number from issues order by number").fetchall()]
        for n in nums:
            ts = scheduled_send_at(n)
            conn.execute(
                "update issues set scheduled_send_at = %s where number = %s",
                (ts, n),
            )
            print(f"   Issue {n:03d}  ->  sends {ts.date()}")

        # ── 2. Backfill Issue 016 cover image ────────────────────────────────
        print("\n=== 2. Backfilling Issue 016 cover image ===")
        conn.execute(
            "update issues set cover_image_url = %s, shopify_image_url = %s "
            "where number = 16",
            (IMG_016, IMG_016),
        )
        print("   Issue 016 cover_image_url + shopify_image_url set")

        # ── 3. Register Issue 018 ────────────────────────────────────────────
        print("\n=== 3. Registering Issue 018 ===")
        conn.execute(
            """
            insert into issues (
                number, subject_line, preview_text,
                character_name, character_year, character_location, pillar,
                topic_summary, page_slug, page_title, page_dek, page_breadcrumb_label,
                email_teaser_body, until_next_teaser, previous_issues_referenced,
                read_time_min, cover_image_url, shopify_image_url,
                shopify_page_id, shopify_page_handle, shopify_page_url,
                klaviyo_campaign_id, klaviyo_template_id, klaviyo_message_id,
                scheduled_send_at, status,
                drafted_at, campaign_drafted_at
            ) values (
                18, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, 'scheduled',
                now(), now()
            )
            on conflict (number) do update set
                subject_line = excluded.subject_line,
                preview_text = excluded.preview_text,
                character_name = excluded.character_name,
                character_year = excluded.character_year,
                character_location = excluded.character_location,
                pillar = excluded.pillar,
                topic_summary = excluded.topic_summary,
                page_slug = excluded.page_slug,
                page_title = excluded.page_title,
                page_dek = excluded.page_dek,
                page_breadcrumb_label = excluded.page_breadcrumb_label,
                email_teaser_body = excluded.email_teaser_body,
                until_next_teaser = excluded.until_next_teaser,
                previous_issues_referenced = excluded.previous_issues_referenced,
                read_time_min = excluded.read_time_min,
                cover_image_url = excluded.cover_image_url,
                shopify_image_url = excluded.shopify_image_url,
                shopify_page_id = excluded.shopify_page_id,
                shopify_page_handle = excluded.shopify_page_handle,
                shopify_page_url = excluded.shopify_page_url,
                klaviyo_campaign_id = excluded.klaviyo_campaign_id,
                klaviyo_template_id = excluded.klaviyo_template_id,
                klaviyo_message_id = excluded.klaviyo_message_id,
                scheduled_send_at = excluded.scheduled_send_at,
                status = 'scheduled'
            """,
            (
                "Your gut keeps its own clock \u2014 and your late dinner sets it wrong",
                ("In 2014, a researcher collecting samples around the clock found the gut "
                 "microbiome is not stable. It runs a night shift \u2014 and a late dinner "
                 "scrambles it."),
                "Christoph Thaiss", 2014, "Weizmann Institute, Rehovot, Israel", "Signal",
                "gut microbiome circadian rhythm and overnight repair chemistry",
                "gut-microbiome-circadian-clock-sleep",
                "Your Gut Runs on a Clock \u2014 And When You Eat Sets It",
                ("In 2014, a PhD student in Israel collecting samples around the clock found "
                 "that the trillions of bacteria in your gut rise, fall, and migrate on a "
                 "24-hour rhythm \u2014 and that rhythm decides what they build while you sleep."),
                "gut microbiome circadian rhythm",
                ("In 2014, a graduate student named Christoph Thaiss was collecting samples from "
                 "mice every few hours, around the clock, at the Weizmann Institute in Israel. He "
                 "expected the gut microbiome to be stable \u2014 a fixed community of bacteria, "
                 "constant from one hour to the next. It was not. The trillions of bacteria living "
                 "in the gut rise, fall, and physically migrate along the intestinal wall on a "
                 "24-hour cycle \u2014 and that cycle is set not by light, but by when you eat. The "
                 "bacteria run a night shift, and its chemistry is different from the day's. The "
                 "full piece explains what the gut clock does while you sleep, and why a late "
                 "dinner can leave you depleted no matter how long you stayed in bed."),
                ("Next: why a certain kind of weight on the body convinces the nervous system it "
                 "is safe \u2014 and the discovery, made by someone studying something else "
                 "entirely, that explains why a heavier blanket can change a night."),
                [1, 7, 16],
                5,
                IMG_018, IMG_018,
                "gid://shopify/Page/132929028345",
                "gut-microbiome-circadian-clock-sleep",
                "https://trybeezybeez.com/pages/gut-microbiome-circadian-clock-sleep",
                "01KS0F4F9K9RW5G6Y248M3RYAY", "VBNcyW", "01KS0F4F9X23A51RV74FB4DASA",
                scheduled_send_at(18),
            ),
        )
        print("   Issue 018 registered (status=scheduled, sends 2026-05-27)")

        # ── Verify ───────────────────────────────────────────────────────────
        print("\n=== Verification ===")
        for r in conn.execute(
            "select number, status, scheduled_send_at::date, "
            "(cover_image_url is not null) from issues order by number"
        ).fetchall():
            print(f"   {r[0]:03d} | {r[1]:<10s} | sends {r[2]} | has_image={r[3]}")

        # Confirm what the fixed generator will draft next (first-gap logic).
        alln = sorted(r[0] for r in conn.execute("select number from issues").fetchall())
        last = alln[0]
        for n in alln[1:]:
            if n == last + 1:
                last = n
            elif n > last + 1:
                break
        print(f"\n   >> Next issue the generator will auto-draft: {last + 1}")
        if last + 1 == 19:
            print("   >> Correct. The 020 orphan no longer poisons numbering.")
        else:
            print(f"   >> WARNING: expected 19, got {last + 1} -- investigate.")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
