#!/usr/bin/env bash
# fix_higgsfield_creds.sh
# Two changes:
#   1. workers/image_gen.py — adds an env-var shim so HIGGSFIELD_API_KEY/HIGGSFIELD_SECRET
#      are visible to the SDK under the names it expects (HF_API_KEY, HF_API_SECRET).
#   2. scripts/regen_image.py — image-only regeneration for an existing draft.
#      Reads cover_image_prompt from the issues table, calls Higgsfield, saves the URL,
#      posts the image to Slack. No redrafting, no Anthropic API cost.
#
# After install:
#   python -m scripts.regen_image --issue 15
#
# Idempotent.

set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d workers ]] || [[ ! -f config.py ]]; then
    echo "FATAL: run from beezy-agents workspace root" >&2
    exit 1
fi

mkdir -p scripts
touch scripts/__init__.py

echo "[fix] patching workers/image_gen.py with HF_* env-var shim..."
cat > workers/image_gen.py <<'PYEOF'
"""Cover image generation via Higgsfield Cloud SDK.

Reads HIGGSFIELD_API_KEY and HIGGSFIELD_SECRET from environment. Maps them
to the names the higgsfield-client SDK expects (HF_API_KEY, HF_API_SECRET).

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

# --- Credential shim ---
# The higgsfield-client SDK reads HF_KEY or HF_API_KEY+HF_API_SECRET.
# We store credentials under HIGGSFIELD_* in Replit Secrets to keep names
# consistent with our other namespacing. Map them here before the SDK imports.
if os.environ.get("HIGGSFIELD_API_KEY") and not os.environ.get("HF_API_KEY"):
    os.environ["HF_API_KEY"] = os.environ["HIGGSFIELD_API_KEY"]
if os.environ.get("HIGGSFIELD_SECRET") and not os.environ.get("HF_API_SECRET"):
    os.environ["HF_API_SECRET"] = os.environ["HIGGSFIELD_SECRET"]

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
    """Submit an image generation request and block until done."""
    try:
        import higgsfield_client  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "higgsfield-client not installed. Run: "
            "pip install higgsfield-client --break-system-packages"
        ) from e

    if not os.environ.get("HF_API_KEY"):
        raise RuntimeError(
            "HIGGSFIELD_API_KEY (mapped to HF_API_KEY) is missing in environment."
        )

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
        raise RuntimeError(f"Higgsfield returned no images. Raw response: {result}")

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
echo "[fix]   workers/image_gen.py ($(wc -l < workers/image_gen.py) lines)"

echo "[fix] writing scripts/regen_image.py..."
cat > scripts/regen_image.py <<'PYEOF'
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

from config import DATABASE_URL
from lib.slack import post_draft
from workers.image_gen import generate_cover, DEFAULT_MODEL


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--image-model", default=None)
    parser.add_argument("--no-slack", action="store_true")
    args = parser.parse_args(argv)

    with psycopg.connect(DATABASE_URL) as conn:
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

    with psycopg.connect(DATABASE_URL) as conn:
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
PYEOF
echo "[fix]   scripts/regen_image.py ($(wc -l < scripts/regen_image.py) lines)"

echo "[fix] syntax checks..."
python -c "import ast; ast.parse(open('workers/image_gen.py').read()); print('  workers/image_gen.py OK')"
python -c "import ast; ast.parse(open('scripts/regen_image.py').read()); print('  scripts/regen_image.py OK')"

echo ""
echo "[fix] DONE."
echo ""
echo "Next: regenerate the cover image for Issue 15 (no redraft, no Anthropic cost):"
echo ""
echo "  python -m scripts.regen_image --issue 15"
echo ""
echo "Takes ~30-60s. Posts the image to Slack. Updates issues.cover_image_url."
echo ""
echo "If the default model name is wrong, try:"
echo "  python -m scripts.regen_image --issue 15 --image-model google/imagen-4-ultra/text-to-image"
