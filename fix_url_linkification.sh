#!/usr/bin/env bash
# fix_url_linkification.sh
# Adds auto-linkification for trybeezybeez.com mentions in the page builder.
# Any plain "trybeezybeez.com" or "trybeezybeez.com/path" in body markdown will be
# rendered as a real anchor tag with href="https://trybeezybeez.com[...]".
# Existing anchor tags (from explicit [text](url) markdown) are left alone — no
# double-linkification. Then re-runs update_issue_page for Issue 15.

set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f workers/shopify_page_builder.py ]]; then
    echo "FATAL: workers/shopify_page_builder.py not found. Run fix_issue_15_format.sh first." >&2
    exit 1
fi

echo "[fix] patching _inline_format in workers/shopify_page_builder.py..."

python <<'PYEOF'
import re
from pathlib import Path

path = Path("workers/shopify_page_builder.py")
src = path.read_text()

new_inline_format = '''def _inline_format(text: str) -> str:
    """Apply inline markdown: [link](url), **bold**, *em*/_em_.
    Also auto-linkifies bare trybeezybeez.com mentions that aren't already inside
    anchor tags — defensive measure in case the writer forgot to use markdown link
    syntax. The hive-mind-newsletter skill's rule 7 requires real links, but this
    catches anything that slips through."""
    text = re.sub(
        r'\\[([^\\]]+)\\]\\(([^)]+)\\)',
        lambda m: f'<a href="{m.group(2)}" style="{STYLES["inline_link"]}">{m.group(1)}</a>',
        text,
    )
    text = re.sub(r'\\*\\*([^*]+)\\*\\*', r'<strong>\\1</strong>', text)
    text = re.sub(r'(?<![*\\w])\\*([^*\\s][^*]*?)\\*(?![*\\w])', r'<em>\\1</em>', text)
    text = re.sub(r'(?<![_\\w])_([^_\\s][^_]*?)_(?![_\\w])', r'<em>\\1</em>', text)

    # Auto-linkify bare trybeezybeez.com mentions OUTSIDE existing <a> tags.
    # Split on existing anchor tags, process only the non-anchor segments.
    parts = re.split(r'(<a\\b[^>]*>.*?</a>)', text, flags=re.IGNORECASE | re.DOTALL)
    domain_re = re.compile(r'\\b(trybeezybeez\\.com(?:/[A-Za-z0-9\\-/_]+)?)\\b')
    for i in range(len(parts)):
        if i % 2 == 0:
            parts[i] = domain_re.sub(
                lambda m: f'<a href="https://{m.group(1)}" style="{STYLES["inline_link"]}">{m.group(1)}</a>',
                parts[i],
            )
    return "".join(parts)'''

# Match the existing _inline_format function (signature line through to its return)
pattern = re.compile(
    r'def _inline_format\(text: str\) -> str:.*?return text',
    re.DOTALL,
)
m = pattern.search(src)
if not m:
    raise SystemExit("FATAL: could not locate _inline_format() to replace")

new_src = src[:m.start()] + new_inline_format + src[m.end():]
path.write_text(new_src)
print(f"  patched _inline_format() at offset {m.start()}")
PYEOF

echo "[fix]   workers/shopify_page_builder.py patched"

echo "[fix] syntax check..."
python -c "import ast; ast.parse(open('workers/shopify_page_builder.py').read()); print('  page_builder.py OK')"

echo "[fix] quick unit check on auto-linkification..."
python <<'PYEOF'
from workers.shopify_page_builder import _inline_format

# Bare domain → should be linked
out = _inline_format("The honey we personally use to support these routines — trybeezybeez.com")
assert '<a href="https://trybeezybeez.com"' in out, f"FAIL bare: {out}"
print("  bare domain   → linked OK")

# Bare path → should be linked with path preserved
out = _inline_format("Try it at trybeezybeez.com/products/honey-sub today.")
assert '<a href="https://trybeezybeez.com/products/honey-sub"' in out, f"FAIL path: {out}"
print("  bare path     → linked OK")

# Already in markdown link → should NOT be double-linkified
out = _inline_format("Read more at [our honey page](https://trybeezybeez.com/products/honey-sub).")
assert out.count("<a href=") == 1, f"FAIL existing link double-linkified: {out}"
print("  existing link → preserved OK (no double-linkification)")

# Markdown link with domain as text → should NOT be double-linkified
out = _inline_format("Find us at [trybeezybeez.com](https://trybeezybeez.com).")
assert out.count("<a href=") == 1, f"FAIL link-with-domain-text double-linkified: {out}"
print("  link with domain text → preserved OK")
PYEOF

echo ""
echo "[fix] re-rendering Issue 15 page with new builder..."
echo ""
python -m scripts.update_issue_page --issue 15
