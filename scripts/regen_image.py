"""Regenerate the cover image for an existing Hive Mind draft.

Does NOT call Anthropic. Does NOT redraft. Reads the cover_image_prompt from
the issues table, calls Higgsfield, updates cover_image_url, posts the image
to Slack.

Usage:
    python -m scripts.regen_image --issue 15
    python -m scripts.regen_image --issue 15 --image-model google/imagen-4-ultra/text-to-image
    python -m scripts.regen_image --issue 15 --no-slack
"""
from __future__ import annotations

import argparse
import sys

import psycopg

from config import NEON_DATABASE_URL
from lib.slack import post_draft
from workers.image_gen import generate_cover, DEFAULT_MODEL


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--image-model", default=None)
    parser.add_argument("--no-slack", action="store_true")
    args = parser.parse_args(argv)

    with psycopg.connect(NEON_DATABASE_URL) as conn:
        cur = conn.execute(
            """
            select cover_image_prompt, subject_line, character_name, character_year,
                   character_location, pillar, status, cover_image_url
            from issues where number = %s
            """,
            (args.issue,),
        )
        row = cur.fetchone()

    if not row:
        print(f"[regen] No issue {args.issue} in DB", file=sys.stderr)
        return 1

    prompt, subject, char, year, loc, pillar, status, existing_url = row

    if not prompt:
        print(f"[regen] Issue {args.issue} has no cover_image_prompt", file=sys.stderr)
        return 1

    if status not in ("draft",):
        print(f"[regen] WARNING: Issue {args.issue} status is '{status}', not 'draft'", file=sys.stderr)

    model = args.image_model or DEFAULT_MODEL
    print(f"[regen] Issue {args.issue} status={status} char={char} ({year})")
    print(f"[regen] existing cover_image_url: {existing_url or '(none)'}")
    print(f"[regen] model={model}")
    print(f"[regen] prompt: {prompt[:160]}...")

    try:
        img = generate_cover(prompt, model=model)
    except Exception as e:
        print(f"[regen] FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(f"[regen] image url: {img.url}")
    print(f"[regen] elapsed: {img.elapsed_seconds:.1f}s")

    with psycopg.connect(NEON_DATABASE_URL) as conn:
        conn.execute(
            "update issues set cover_image_url = %s where number = %s",
            (img.url, args.issue),
        )
    print(f"[regen] saved cover_image_url to issues table (number={args.issue})")

    if not args.no_slack:
        post_draft(
            title=f"Hive Mind Issue {args.issue} — cover image",
            summary_lines=[
                f"*Subject:* {subject}",
                f"*Character:* {char} ({year}), {loc}",
                f"*Pillar:* {pillar}",
            ],
            body=f"*Model:* `{model}`\n*Image URL:* {img.url}\n*Elapsed:* {img.elapsed_seconds:.1f}s",
            image_url=img.url,
            image_alt=f"Cover image for Hive Mind Issue {args.issue}",
        )
        print("[regen] posted to Slack")

    return 0


if __name__ == "__main__":
    sys.exit(main())
