#!/usr/bin/env bash
# install_phase3a3_v2.sh
# Bundles Phase 3A.3 (image generation) AND the state-lookup bug fix for workers/run.py.
#
# Idempotent — safe to re-run.
#
# What this writes:
#   workers/image_gen.py    — Higgsfield cover image wrapper (Phase 3A.3)
#   lib/slack.py            — adds image block to post_draft
#   workers/run.py          — Phase 3A.3 image-gen chain + bug fix:
#                             * --issue X now looks up issue (X-1)'s teaser as topic source
#                             * recent_issues filtered to number < X
#                             * --allow-overwrite gate (refuses to clobber existing drafts by default)
#                             * --no-image flag, --image-model override
#                             * verbose logging of teaser source + recent issues
#
# After install:
#   python -m workers.run --skill hive_mind --issue 15 --allow-overwrite
#
# That will redraft Issue 15 with Issue 14's teaser as the topic assignment
# (dream forgetting / Crick territory), generate a cover image, save to DB,
# and post to Slack.

set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -d workers ]] || [[ ! -d lib ]] || [[ ! -f config.py ]]; then
    echo "FATAL: must be run from the beezy-agents workspace root (workers/, lib/, config.py expected)" >&2
    exit 1
fi

echo "[install] writing workers/image_gen.py..."
cat > workers/image_gen.py <<'PYEOF'
"""Cover image generation via Higgsfield Cloud SDK.

Requires HIGGSFIELD_API_KEY and HIGGSFIELD_SECRET in environment.
The SDK reads them automatically.

Usage:
    from workers.image_gen import generate_cover
    result = generate_cover("editorial illustration of...")
    print(result.url)
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Optional

# Default model. Override via HIGGSFIELD_IMAGE_MODEL env var if you want
# Imagen 4, FLUX, or another model from cloud.higgsfield.ai's catalog.
DEFAULT_MODEL = os.environ.get(
    "HIGGSFIELD_IMAGE_MODEL",
    "bytedance/seedream/v4/text-to-image",
)
DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_RESOLUTION = "2K"


@dataclass
class ImageGenResult:
    url: str
    model: str
    request_id: str
    elapsed_seconds: float
    raw: dict[str, Any]


def generate_cover(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    resolution: Optional[str] = DEFAULT_RESOLUTION,
    extra_args: Optional[dict[str, Any]] = None,
) -> ImageGenResult:
    """Submit an image generation request and block until done.

    Returns ImageGenResult with the public URL of the generated image.
    Raises RuntimeError on failure (missing credentials, no images, etc.).
    """
    try:
        import higgsfield_client  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "higgsfield-client not installed. Run: "
            "pip install higgsfield-client --break-system-packages"
        ) from e

    if not os.environ.get("HIGGSFIELD_API_KEY"):
        raise RuntimeError("HIGGSFIELD_API_KEY environment variable not set.")

    arguments: dict[str, Any] = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
    }
    if resolution:
        arguments["resolution"] = resolution
    if extra_args:
        arguments.update(extra_args)

    print(f"[image_gen] submitting model={model} aspect={aspect_ratio}")
    started = time.time()

    try:
        result = higgsfield_client.subscribe(model, arguments=arguments)
    except Exception as e:
        elapsed = time.time() - started
        raise RuntimeError(
            f"Higgsfield generation failed after {elapsed:.1f}s: {type(e).__name__}: {e}"
        ) from e

    elapsed = time.time() - started
    images = result.get("images") if isinstance(result, dict) else None
    if not images:
        raise RuntimeError(
            f"Higgsfield returned no images. Raw response: {result}"
        )

    first = images[0]
    url = first.get("url") if isinstance(first, dict) else None
    if not url:
        raise RuntimeError(f"No URL in first image entry: {first}")

    request_id = result.get("request_id", "") if isinstance(result, dict) else ""
    print(f"[image_gen] done in {elapsed:.1f}s url={url[:80]}...")

    return ImageGenResult(
        url=url,
        model=model,
        request_id=request_id,
        elapsed_seconds=elapsed,
        raw=result if isinstance(result, dict) else {},
    )
PYEOF
echo "[install]   workers/image_gen.py ($(wc -l < workers/image_gen.py) lines)"

echo "[install] writing lib/slack.py..."
cat > lib/slack.py <<'PYEOF'
"""Slack helpers — failure alerts and long-form content drafts.

Reads SLACK_WEBHOOK_URL from environment. Graceful no-op if unset.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Optional

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
_SECTION_CHAR_LIMIT = 2800  # Slack section blocks cap at 3000; leave headroom.


def _post(payload: dict[str, Any]) -> bool:
    if not SLACK_WEBHOOK_URL:
        print("[slack] SLACK_WEBHOOK_URL not set, skipping post")
        return False
    try:
        req = urllib.request.Request(
            SLACK_WEBHOOK_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = 200 <= resp.status < 300
            if not ok:
                print(f"[slack] non-2xx status: {resp.status}")
            return ok
    except Exception as e:
        print(f"[slack] post failed: {e}")
        return False


def notify_failure(
    source: str,
    error: str,
    *,
    context: Optional[dict[str, Any]] = None,
) -> bool:
    """Post a failure alert to Slack."""
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"❌ {source} failed"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"```{error[:_SECTION_CHAR_LIMIT]}```"}},
    ]
    if context:
        ctx_text = "\n".join(f"*{k}:* {v}" for k, v in context.items())
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": ctx_text[:_SECTION_CHAR_LIMIT]}})
    return _post({"blocks": blocks})


def post_draft(
    title: str,
    summary_lines: list[str],
    body: str,
    *,
    metadata: Optional[dict[str, Any]] = None,
    image_url: Optional[str] = None,
    image_alt: str = "Cover image",
) -> bool:
    """Post a long-form draft (newsletter issue, blog, etc.) to Slack for review.

    Chunks the body into multiple section blocks to handle Slack's 3000-char limit.
    Includes an image block if image_url is provided.
    """
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📝 {title}"}},
    ]

    if image_url:
        blocks.append({
            "type": "image",
            "image_url": image_url,
            "alt_text": image_alt[:1990],
        })

    if summary_lines:
        summary_text = "\n".join(f"• {line}" for line in summary_lines)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": summary_text[:_SECTION_CHAR_LIMIT]}})
    if metadata:
        meta_text = "\n".join(f"*{k}:* {v}" for k, v in metadata.items() if v is not None)
        if meta_text:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": meta_text[:_SECTION_CHAR_LIMIT]}})
    blocks.append({"type": "divider"})

    body_chunks = [body[i:i + _SECTION_CHAR_LIMIT] for i in range(0, len(body), _SECTION_CHAR_LIMIT)]
    for chunk in body_chunks[:40]:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})

    return _post({"blocks": blocks})
PYEOF
echo "[install]   lib/slack.py ($(wc -l < lib/slack.py) lines)"

echo "[install] writing workers/run.py (v2 with state-lookup bug fix)..."
cat > workers/run.py <<'PYEOF'
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
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any, Optional

import psycopg

from config import DATABASE_URL
from lib.slack import notify_failure, post_draft
from workers.skill_runner import invoke_skill


def _fetch_state(conn: psycopg.Connection, target_issue: Optional[int]) -> dict[str, Any]:
    """Build context for the draft call.

    If target_issue is given (explicit), look up issue (target_issue - 1) as the topic-
    binding teaser, and filter recent_issues to numbers < target_issue.

    If target_issue is None (auto-detect), use the top issue in the DB to compute the
    next number, and use that top issue's teaser as the topic binding.
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
        cur = conn.execute(
            """
            select number, character_name, character_year, pillar, topic_summary, until_next_teaser
            from issues order by number desc limit 1
            """
        )
        top = cur.fetchone()
        if not top:
            return {
                "target_issue_number": 1,
                "previous_teaser": None,
                "previous_teaser_source": None,
                "recent_issues": [],
            }
        target_issue = top[0] + 1
        previous_teaser = top[5]
        previous_teaser_source = top[0]

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

    conn.execute(
        """
        insert into issues (
            number, subject_line, subject_line_48h, preview_text,
            character_name, character_year, character_location, pillar,
            topic_summary, page_slug,
            cover_image_prompt, cover_image_url,
            long_form_body, email_teaser_body,
            until_next_teaser, previous_issues_referenced,
            read_time_min, word_count_long_form, word_count_email_teaser,
            drafted_at, status, run_id
        ) values (
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s,
            %s, %s,
            %s, %s,
            %s, %s,
            %s, %s, %s,
            now(), 'draft', %s
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
            issue_data.get("cover_image_prompt"),
            cover_image_url,
            long_form,
            email_teaser,
            issue_data.get("until_next_teaser"),
            issue_data.get("previous_issues_referenced") or [],
            issue_data.get("read_time_min"),
            long_form_wc,
            teaser_wc,
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
PYEOF
echo "[install]   workers/run.py ($(wc -l < workers/run.py) lines)"

echo "[install] ensuring higgsfield-client is installed..."
pip install higgsfield-client --break-system-packages --quiet 2>&1 | tail -3 || true

# Quick syntax checks
echo "[install] syntax checks..."
python -c "import ast; ast.parse(open('workers/image_gen.py').read()); print('  workers/image_gen.py OK')"
python -c "import ast; ast.parse(open('lib/slack.py').read()); print('  lib/slack.py OK')"
python -c "import ast; ast.parse(open('workers/run.py').read()); print('  workers/run.py OK')"

echo ""
echo "[install] DONE."
echo ""
echo "Next: redraft Issue 15 with the bug fix + image generation:"
echo ""
echo "  python -m workers.run --skill hive_mind --issue 15 --allow-overwrite"
echo ""
echo "Expected behavior:"
echo "  1. Logs which issue's teaser is used (should say: \"Issue 14's teaser: 'the thing about dreams...'\")"
echo "  2. Drafts ~4,000 words on Crick-ish dream-forgetting territory (~140s, ~\$0.11)"
echo "  3. Saves draft to issues table (overwrites the bad Issue 15)"
echo "  4. Generates cover image via Higgsfield (~30-60s, costs Higgsfield credits)"
echo "  5. Posts to Slack with cover image attached at the top"
echo ""
echo "If you want to skip image gen on first try:  add --no-image"
echo "If the default Higgsfield model is wrong:    add --image-model google/imagen-4-ultra/text-to-image"
