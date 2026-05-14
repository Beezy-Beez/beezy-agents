#!/usr/bin/env bash
# install_inspect.sh — drops one small script for inspecting your existing pages.
# After running this, run the inspector itself and share the output.

set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d workers ]] || [[ ! -f config.py ]]; then
    echo "FATAL: run from beezy-agents workspace root" >&2
    exit 1
fi

mkdir -p scripts

cat > scripts/inspect_existing_pages.py <<'PYEOF'
"""Dump the raw body HTML of existing Hive Mind pages so we can match the template.

Pulls Issue 14 (alcohol), Issue 13 (dreams), /pages/sleep-science-hub, /pages/the-hive-mind.
For each: handle, title, page ID, template suffix, isPublished, body length, full body.

Usage:
    python -m scripts.inspect_existing_pages > existing_pages.txt
"""
from __future__ import annotations

import sys

from lib.shopify_admin import graphql


HANDLES = [
    "alcohol-sleep-architecture-rem-suppression",   # Issue 14 — template reference
    "dreams-rem-sleep-emotional-processing",         # Issue 13 — second comparison point
    "sleep-science-hub",                             # Index page #1
    "the-hive-mind",                                 # Index page #2
]


QUERY = """
query getPage($q: String!) {
    pages(first: 1, query: $q) {
        edges {
            node {
                id
                title
                handle
                body
                templateSuffix
                isPublished
            }
        }
    }
}
"""


def main() -> int:
    for handle in HANDLES:
        try:
            data = graphql(QUERY, {"q": f"handle:{handle}"})
        except Exception as e:
            print(f"\n========== {handle} ==========")
            print(f"QUERY FAILED: {type(e).__name__}: {e}")
            continue

        edges = (data.get("pages") or {}).get("edges") or []
        if not edges:
            print(f"\n========== {handle} ==========")
            print("NOT FOUND in shop")
            continue

        page = edges[0]["node"]
        body = page.get("body") or ""

        print(f"\n========== {page['handle']} ==========")
        print(f"Title:           {page['title']}")
        print(f"Page ID:         {page['id']}")
        print(f"Template suffix: {page.get('templateSuffix') or '(default)'}")
        print(f"isPublished:     {page['isPublished']}")
        print(f"Body length:     {len(body):,} chars")
        print(f"\n--- BODY ---")
        print(body)
        print(f"--- END BODY ({page['handle']}) ---\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
PYEOF

python -c "import ast; ast.parse(open('scripts/inspect_existing_pages.py').read()); print('  scripts/inspect_existing_pages.py OK')"

echo ""
echo "[install] DONE."
echo ""
echo "Run the inspector and pipe to a file:"
echo ""
echo "  python -m scripts.inspect_existing_pages > existing_pages.txt"
echo ""
echo "Then share existing_pages.txt with me (drag-and-drop into chat, or paste contents)."
