"""
CLI for invoking Skill workers.

For hive_mind: reads the `issues` table to determine the next issue number and
the previous issue's teaser (the topic assignment), drafts the issue, generates
a cover image via Higgsfield, inserts a row in `issues` with status='draft',
and posts a Slack summary with the cover image attached.

Examples:
    python -m workers.run --skill hive_mind                  # auto-detect next issue
    python -m workers.run --skill hive_mind --issue 15       # explicit issue number
    python -m workers.run --skill hive_mind --issue 15 --allow-overwrite
    python -m workers.run --skill hive_mind --issue 15 --dry-run
    python -m workers.run --skill hive_mind --no-image

NUMBERING (fixed May 2026):
    Auto-detect uses FIRST-GAP logic, not MAX(number)+1. The next issue is the
    one after the last *contiguous* issue. A stray high-numbered row (e.g. a
    mis-numbered 020) can never create or widen a gap. The gap self-heals as
    issues are filled in.

SCHEDULING:
    scheduled_send_at is populated on insert from the fixed 3-day cadence
    anchored on Issue 014 (May 15, 2026). This is what the publish_and_index
    worker reads to know which issue sends today.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import psycopg

from config import DATABASE_URL
from lib.slack import notify_failure, post_draft
from workers.skill_runner import invoke_skill


# ─────────────────────────────────────────────────────────────────────────────
# Hive Mind cadence — single source of truth for issue send dates.
# Issue 014 sent May 15, 2026. Every issue sends 3 days after the previous one.
# To change the cadence, change these three constants.
# ─────────────────────────────────────────────────────────────────────────────
HIVE_MIND_ANCHOR_ISSUE = 14
HIVE_MIND_ANCHOR_DATE = date(2026, 5, 15)
HIVE_MIND_CADENCE_DAYS = 3


def _compute_scheduled_send_at(issue_number: int) -> Optional[datetime]:
    """Send timestamp for an issue, derived from the fixed 3-day cadence.

    Stored at 12:00 UTC on the send date so that `scheduled_send_at::date`
    always yields the correct calendar send date regardless of DB session
    timezone. The actual send *time* (8pm ET) lives on the Klaviyo campaign.
    """
    if issue_number is None:
        return None
    send_date = HIVE_MIND_ANCHOR_DATE + timedelta(
        days=(issue_number - HIVE_MIND_ANCHOR_ISSUE) * HIVE_MIND_CADENCE_DAYS
    )
    return datetime(send_date.year, send_date.month, send_date.day, 12, 0, 0, tzinfo=timezone.utc)


def _fetch_state(conn: psycopg.Connection, target_issue: Optional[int]) -> dict[str, Any]:
    """Build context for the draft call.

    If target_issue is given (explicit), look up issue (target_issue - 1) as the topic-
    binding teaser, and filter recent_issues to numbers < target_issue.

    If target_issue is None (auto-detect), use FIRST-GAP logic to find the next
    issue number, and chain the topic from the last contiguous issue's teaser.
    """
    if target_issue is not None:
        cur = conn.execute(
            """
            select number, character_name, character_year, pillar, topic_summary, until_next_teaser
            from issues where number = %s
            """,
            (target_issue - 1,),
        )
        prev_row = cur.fetchone()
        previous_teaser = prev_row[5] if prev_row else None
        previous_teaser_source = target_issue - 1 if prev_row else None

        cur = conn.execute(
            """
            select number, character_name, character_year, pillar, topic_summary
            from issues where number < %s
            order by number desc limit 6
            """,
            (target_issue,),
        )
        rows = cur.fetchall()
    else:
        # ── Auto-detect the next issue number — FIRST-GAP logic. ──────────────
        # The next issue is the one after the last *contiguous* issue, NOT
        # max(number)+1. A stray high-numbered row (e.g. a mis-numbered 020)
        # must never create or widen a gap. Walk the sorted numbers from the
        # bottom and stop at the first gap.
        cur = conn.execute("select number from issues order by number asc")
        all_numbers = [r[0] for r in cur.fetchall()]
        if not all_numbers:
            return {
                "target_issue_number": 1,
                "previous_teaser": None,
                "previous_teaser_source": None,
                "recent_issues": [],
            }

        last_contiguous = all_numbers[0]
        for n in all_numbers[1:]:
            if n == last_contiguous + 1:
                last_contiguous = n
            elif n <= last_contiguous:
                continue  # duplicate / lower — ignore
            else:
                break  # first gap — stop here

        target_issue = last_contiguous + 1

        # Topic binding: chain from the issue we are following (last_contiguous),
        # using its until_next_teaser as the assignment for the new issue.
        cur = conn.execute(
            """
            select number, until_next_teaser
            from issues where number = %s
            """,
            (last_contiguous,),
        )
        prev_row = cur.fetchone()
        previous_teaser = prev_row[1] if prev_row else None
        previous_teaser_source = prev_row[0] if prev_row else None

        cur = conn.execute(
            """
            select number, character_name, character_year, pillar, topic_summary
            from issues where number < %s
            order by number desc limit 6
            """,
            (target_issue,),
        )
        rows = cur.fetchall()

    recent = [
        {
            "number": r[0],
            "character": r[1],
            "character_year": r[2],
            "pillar": r[3],
            "topic_summary": r[4],
        }
        for r in rows
    ]
    return {
        "target_issue_number": target_issue,
        "previous_teaser": previous_teaser,
        "previous_teaser_source": previous_teaser_source,
        "recent_issues": recent,
    }


def _check_overwrite_safety(
    conn: psycopg.Connection,
    issue_number: int,
    allow_overwrite: bool,
) -> tuple[bool, Optional[str]]:
    """Return (ok_to_write, reason_if_not). Refuse to clobber published/scheduled
    issues regardless of flag. Refuse to clobber drafts unless allow_overwrite is True."""
    cur = conn.execute("select status from issues where number = %s", (issue_number,))
    row = cur.fetchone()
    if row is None:
        return True, None
    existing_status = row[0]
    if existing_status in ("scheduled", "published"):
        return False, f"Issue {issue_number} is {existing_status}. Refusing to overwrite ever."
    if existing_status == "draft" and not allow_overwrite:
        return False, (
            f"Issue {issue_number} already has a draft. "
            "Pass --allow-overwrite to replace it, or use a different --issue number."
        )
    return True, None


def _insert_draft_issue(
    conn: psycopg.Connection,
    issue_data: dict[str, Any],
    run_id: str,
    cover_image_url: Optional[str] = None,
) -> None:
    """Insert (or refresh-if-draft) a row in issues."""
    long_form = issue_data.get("long_form_body") or ""
    email_teaser = issue_data.get("email_teaser_body") or ""
    long_form_wc = len(long_form.split())
    teaser_wc = len(email_teaser.split())

    # page_seo_title → page_title (H1 + SEO title); page_meta_description → page_dek
    # page_breadcrumb_label derived from topic_summary (6-10 word mechanism descriptor)
    page_title = issue_data.get("page_seo_title") or ""
    page_dek = issue_data.get("page_meta_description") or ""
    page_breadcrumb_label = issue_data.get("topic_summary") or ""

    # scheduled_send_at — derived from the fixed cadence so publish_and_index
    # and the campaign scheduler always know when this issue sends.
    scheduled_send_at = _compute_scheduled_send_at(issue_data.get("issue_number"))

    conn.execute(
        """
        insert into issues (
            number, subject_line, subject_line_48h, preview_text,
            character_name, character_year, character_location, pillar,
            topic_summary, page_slug,
            page_title, page_dek, page_breadcrumb_label,
            cover_image_prompt, cover_image_url,
            long_form_body, email_teaser_body,
            until_next_teaser, previous_issues_referenced,
            read_time_min, word_count_long_form, word_count_email_teaser,
            drafted_at, scheduled_send_at, status, run_id
        ) values (
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s,
            %s, %s, %s,
            %s, %s,
            %s, %s,
            %s, %s,
            %s, %s, %s,
            now(), %s, 'draft', %s
        )
        on conflict (number) do update set
            subject_line = excluded.subject_line,
            subject_line_48h = excluded.subject_line_48h,
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
            cover_image_prompt = excluded.cover_image_prompt,
            cover_image_url = coalesce(excluded.cover_image_url, issues.cover_image_url),
            long_form_body = excluded.long_form_body,
            email_teaser_body = excluded.email_teaser_body,
            until_next_teaser = excluded.until_next_teaser,
            previous_issues_referenced = excluded.previous_issues_referenced,
            read_time_min = excluded.read_time_min,
            word_count_long_form = excluded.word_count_long_form,
            word_count_email_teaser = excluded.word_count_email_teaser,
            drafted_at = now(),
            scheduled_send_at = excluded.scheduled_send_at,
            status = 'draft',
            run_id = excluded.run_id
        where issues.status = 'draft'
        """,
        (
            issue_data.get("issue_number"),
            issue_data.get("subject_line"),
            issue_data.get("subject_line_48h"),
            issue_data.get("preview_text"),
            issue_data.get("character"),
            issue_data.get("character_year"),
            issue_data.get("character_location"),
            issue_data.get("pillar"),
            issue_data.get("topic_summary"),
            issue_data.get("page_slug"),
            page_title,
            page_dek,
            page_breadcrumb_label,
            issue_data.get("cover_image_prompt"),
            cover_image_url,
            long_form,
            email_teaser,
            issue_data.get("until_next_teaser"),
            issue_data.get("previous_issues_referenced") or [],
            issue_data.get("read_time_min"),
            long_form_wc,
            teaser_wc,
            scheduled_send_at,
            uuid.UUID(run_id) if run_id else None,
        ),
    )


def _update_cover_image_only(conn: psycopg.Connection, issue_number: int, url: str) -> None:
    conn.execute(
        "update issues set cover_image_url = %s where number = %s and status = 'draft'",
        (url, issue_number),
    )


def _post_hive_mind_draft_to_slack(
    issue_data: dict[str, Any],
    run_id: str,
    cost_usd: float,
    cover_image_url: Optional[str] = None,
    image_error: Optional[str] = None,
) -> None:
    long_form = issue_data.get("long_form_body") or ""
    email_teaser = issue_data.get("email_teaser_body") or ""
    long_form_wc = len(long_form.split())
    teaser_wc = len(email_teaser.split())

    summary = [
        f"*Subject:* {issue_data.get('subject_line', '(none)')}",
        f"*48h follow-up:* {issue_data.get('subject_line_48h', '(none)')}",
        f"*Preview text:* {issue_data.get('preview_text', '(none)')}",
        f"*Read time:* {issue_data.get('read_time_min', '?')} min",
        f"*Long-form word count:* {long_form_wc:,}",
        f"*Email teaser word count:* {teaser_wc:,}",
    ]
    metadata = {
        "Issue": issue_data.get("issue_number"),
        "Pillar": issue_data.get("pillar"),
        "Character": f"{issue_data.get('character')} ({issue_data.get('character_year')})",
        "Location": issue_data.get("character_location"),
        "Page slug": issue_data.get("page_slug"),
        "Previous issues referenced": ", ".join(str(n) for n in (issue_data.get("previous_issues_referenced") or [])),
        "Run ID": run_id,
        "Cost (draft)": f"${cost_usd:.4f}",
    }
    if image_error:
        metadata["⚠ Image gen"] = f"FAILED — {image_error[:200]}"

    sections: list[str] = [
        "*— Email teaser body (what goes in Klaviyo email) —*",
        email_teaser,
        "*— Until next issue (binding teaser for issue N+1) —*",
        issue_data.get("until_next_teaser") or "(none)",
        "*— Cover image prompt —*",
        issue_data.get("cover_image_prompt") or "(none)",
        "*— Testimonial suggestion —*",
        issue_data.get("testimonial_suggestion") or "(none)",
        f"_Long-form body ({long_form_wc:,} words) in the `issues` table:_ ```SELECT long_form_body FROM issues WHERE number = {issue_data.get('issue_number')};```",
    ]
    body = "\n\n".join(sections)

    post_draft(
        title=f"Hive Mind Issue {issue_data.get('issue_number', '?')} — draft",
        summary_lines=summary,
        body=body,
        metadata=metadata,
        image_url=cover_image_url,
        image_alt=f"Cover image for Hive Mind Issue {issue_data.get('issue_number', '?')}",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Invoke a Skill worker.")
    parser.add_argument("--skill", required=True, help="Skill name (e.g. hive_mind)")
    parser.add_argument("--issue", type=int, help="Issue number to draft (default: auto-detect next from DB)")
    parser.add_argument("--topic-override", help="Override the previous_teaser as the topic")
    parser.add_argument("--pillar", choices=["Signal", "Surrender", "Renewal"], help="Pillar override")
    parser.add_argument("--model", help="Claude model override")
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--allow-overwrite", action="store_true",
                        help="Allow overwriting an existing draft for this issue. Never overwrites scheduled/published.")
    parser.add_argument("--no-slack", action="store_true")
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--no-image", action="store_true", help="Skip cover image generation")
    parser.add_argument("--image-model", help="Higgsfield image model override")
    parser.add_argument("--dry-run", action="store_true", help="Print output, don't post anywhere or save")
    args = parser.parse_args(argv)

    context: dict[str, Any] = {}
    state: dict[str, Any] = {}

    if args.skill == "hive_mind":
        with psycopg.connect(DATABASE_URL) as conn:
            state = _fetch_state(conn, target_issue=args.issue)
            target_issue_number = state["target_issue_number"]

            # ── Gap guard ────────────────────────────────────────────────────
            # An explicit --issue must never leap past the sequence. This is
            # the exact hole that let a "020" get drafted while max was 016.
            # Filling an existing gap (e.g. --issue 19 when 18 is missing) and
            # overwriting an existing issue both remain allowed; only numbers
            # beyond max+1 are refused.
            if args.issue is not None:
                cur = conn.execute("select coalesce(max(number), 0) from issues")
                max_existing = cur.fetchone()[0]
                if args.issue > max_existing + 1:
                    print(
                        f"[run] REFUSED: --issue {args.issue} would create a gap. "
                        f"Highest existing issue is {max_existing}; the next allowed "
                        f"number is {max_existing + 1}. Use --issue {max_existing + 1}, "
                        f"or omit --issue to auto-detect.",
                        file=sys.stderr,
                    )
                    return 2

            if not args.dry_run and not args.no_save:
                ok, reason = _check_overwrite_safety(conn, target_issue_number, args.allow_overwrite)
                if not ok:
                    print(f"[run] REFUSED: {reason}", file=sys.stderr)
                    return 2

        context = {
            "target_issue_number": target_issue_number,
            "previous_teaser": state["previous_teaser"],
            "recent_issues": state["recent_issues"],
        }
        if args.topic_override:
            context["topic_override"] = args.topic_override
        if args.pillar:
            context["pillar"] = args.pillar

        src = state.get("previous_teaser_source")
        teaser_preview = (state["previous_teaser"] or "(none)")[:100]
        print(f"[run] drafting Issue {target_issue_number}")
        print(f"[run] topic assignment from Issue {src}'s teaser: \"{teaser_preview}...\"")
        print(f"[run] recent_issues considered: {[r['number'] for r in state['recent_issues']]}")
    else:
        if args.issue:
            context["issue_number"] = args.issue
        if args.topic_override:
            context["topic"] = args.topic_override
        if args.pillar:
            context["pillar"] = args.pillar

    kwargs: dict[str, Any] = {"max_tokens": args.max_tokens}
    if args.model:
        kwargs["model"] = args.model

    print(f"[run] invoking skill={args.skill}")

    try:
        result = invoke_skill(args.skill, context, **kwargs)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        if not args.no_slack and not args.dry_run:
            notify_failure(f"workers.run --skill {args.skill}", msg, context=context)
        print(f"[run] FAILED: {msg}", file=sys.stderr)
        return 1

    print(
        f"[run] draft done run_id={result.run_id} status={result.status} "
        f"tokens=in:{result.input_tokens}/out:{result.output_tokens} "
        f"cost=${result.cost_usd:.4f} elapsed={result.elapsed_seconds:.1f}s"
    )

    if args.dry_run:
        print("\n----- OUTPUT -----\n")
        print(result.output_text or "(empty)")
        return 0

    if args.skill == "hive_mind" and result.output_json:
        issue_data = result.output_json
        issue_number = issue_data.get("issue_number")
        if not args.no_save:
            try:
                with psycopg.connect(DATABASE_URL) as conn:
                    _insert_draft_issue(conn, issue_data, result.run_id, cover_image_url=None)
                print(f"[run] saved draft to issues table (number={issue_number})")
            except Exception as e:
                print(f"[run] WARNING: failed to save draft: {e}", file=sys.stderr)

        cover_url: Optional[str] = None
        image_error: Optional[str] = None
        if not args.no_image:
            cover_prompt = issue_data.get("cover_image_prompt")
            if cover_prompt:
                try:
                    from workers.image_gen import generate_cover, DEFAULT_MODEL as IMG_DEFAULT_MODEL
                    image_model = args.image_model or IMG_DEFAULT_MODEL
                    print(f"[run] generating cover image (model={image_model})...")
                    img_result = generate_cover(cover_prompt, model=image_model)
                    cover_url = img_result.url
                    print(f"[run] cover image: {cover_url}")
                    if not args.no_save:
                        try:
                            with psycopg.connect(DATABASE_URL) as conn:
                                _update_cover_image_only(conn, issue_number, cover_url)
                            print("[run] saved cover_image_url to issues table")
                        except Exception as e:
                            print(f"[run] WARNING: failed to save cover_image_url: {e}", file=sys.stderr)
                except Exception as e:
                    image_error = f"{type(e).__name__}: {e}"
                    print(f"[run] WARNING: cover image generation failed: {image_error}", file=sys.stderr)
            else:
                image_error = "no cover_image_prompt in draft output"

        if not args.no_slack:
            _post_hive_mind_draft_to_slack(
                issue_data, result.run_id, result.cost_usd,
                cover_image_url=cover_url, image_error=image_error,
            )
            print("[run] posted Hive Mind draft to Slack")
    else:
        if not result.output_json:
            print("[run] WARNING: expected JSON output but parse failed; posting raw text", file=sys.stderr)
        if not args.no_slack:
            post_draft(
                title=f"{args.skill} draft",
                summary_lines=[f"Run ID: {result.run_id}", f"Cost: ${result.cost_usd:.4f}"],
                body=result.output_text or "(empty)",
            )
            print("[run] posted draft to Slack")

    return 0


if __name__ == "__main__":
    sys.exit(main())
