#!/usr/bin/env bash
# fix_checkmark.sh — replaces the unreliable &check; HTML entity with the literal
# ✓ character in workers/shopify_page_builder.py, then re-renders Issue 15.

set -euo pipefail
cd "$(dirname "$0")"

python <<'PYEOF'
from pathlib import Path
p = Path("workers/shopify_page_builder.py")
src = p.read_text()
new = src.replace("&check; You", "✓ You")
if new == src:
    print("WARNING: no '&check; You' substring found — already patched?")
else:
    p.write_text(new)
    print("  patched: &check; → ✓")
PYEOF

python -c "import ast; ast.parse(open('workers/shopify_page_builder.py').read()); print('  syntax OK')"

echo ""
echo "[fix] re-rendering Issue 15..."
python -m scripts.update_issue_page --issue 15
