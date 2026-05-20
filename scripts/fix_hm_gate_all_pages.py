"""
Migration: add simple archive CTA box to all Hive Mind issue pages.
The archive link includes ?s=1 so subscribers bypass the gate automatically.

Run from the Replit shell:
  cd ~/workspace
  python3 scripts/fix_hm_gate_all_pages.py

- Pages with an existing hm-gate: replaces it with the simple CTA.
- Pages with no hm-gate: appends the CTA after the product block.
- Pages already correct (?s=1 link present): skipped.
- Safe to re-run.
"""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.shopify_admin import graphql

# ── Archive CTA box ───────────────────────────────────────────────────────────
# ?s=1 on the archive link means subscribers auto-bypass the gate on arrival.

ARCHIVE_CTA = (
    '<div style="background:linear-gradient(135deg,#f5ede0,#faf6ee);'
    'border:1px solid #d9c5a8;border-radius:12px;padding:40px 32px;'
    'text-align:center;margin:0 0 35px 0;">'
    '<p style="font-size:13px;color:#87401c;letter-spacing:2px;'
    'text-transform:uppercase;font-family:Georgia,serif;font-weight:bold;'
    'margin:0 0 14px;">The Hive Mind Newsletter</p>'
    '<h3 style="font-size:26px;color:#2c2417;font-family:Georgia,serif;'
    'font-weight:bold;margin:0 0 14px;line-height:1.3;">Every issue, in one place.</h3>'
    '<p style="font-size:17px;color:#5a4a3a;font-family:Georgia,serif;'
    'margin:0 0 24px;line-height:1.65;">One sleep science deep-dive every three days'
    ' \u2014 from Issue 001 to the latest. Free to read, no sign-up required.</p>'
    '<a href="https://trybeezybeez.com/pages/the-hive-mind?s=1" '
    'style="display:inline-block;background:#8b4513;color:#fffdf7;'
    'padding:14px 32px;border-radius:4px;font-family:Georgia,serif;'
    'font-weight:bold;font-size:15px;letter-spacing:.5px;text-decoration:none;">'
    'Browse the Archive \u2192</a>'
    '</div>'
)

# Detect already-converted pages (with ?s=1)
_SENTINEL = 'href="https://trybeezybeez.com/pages/the-hive-mind?s=1"'
# Detect old version without ?s=1
_OLD_SENTINEL = 'href="https://trybeezybeez.com/pages/the-hive-mind"'

# ── Shopify helpers ────────────────────────────────────────────────────────────

_PAGE_QUERY = """
query getPage($q: String!) {
  pages(first: 1, query: $q) {
    edges { node { id title body handle } }
  }
}
"""

_PAGE_UPDATE = """
mutation pageUpdate($id: ID!, $page: PageUpdateInput!) {
  pageUpdate(id: $id, page: $page) {
    page { id handle }
    userErrors { field message }
  }
}
"""

def fetch_page(handle):
    try:
        data = graphql(_PAGE_QUERY, {"q": f"handle:{handle}"})
        edges = (data.get("pages") or {}).get("edges") or []
        return edges[0]["node"] if edges else None
    except Exception as exc:
        print(f"  [fetch] {handle} failed: {exc}")
        return None

def save_page(page_id, title, body):
    data = graphql(_PAGE_UPDATE, {"id": page_id, "page": {"title": title, "body": body}})
    errors = (data.get("pageUpdate") or {}).get("userErrors") or []
    if errors:
        raise RuntimeError(f"pageUpdate errors: {errors}")

# ── Patch logic ────────────────────────────────────────────────────────────────

_GATE_RE = re.compile(
    r'<div\s+id="hm-gate".*?</div>\s*<script>\(function\(\)\{.*?\}\)\(\);</script>',
    re.DOTALL,
)

def patch_body(body):
    """
    Returns (new_body, action):
      'replaced'  — removed hm-gate + script, inserted archive CTA
      'appended'  — appended archive CTA after product block
      'updated'   — replaced old archive CTA (without ?s=1) with new one
      'skipped'   — already correct
      'no_anchor' — no safe insertion point found
    """
    # Already correct?
    if _SENTINEL in body:
        return body, 'skipped'

    # Old version without ?s=1 — upgrade it
    if _OLD_SENTINEL in body and 'id="hm-gate"' not in body:
        old_cta_start = body.rfind('<div style="background:linear-gradient(135deg,#f5ede0')
        if old_cta_start != -1:
            # Find the closing </div>
            depth, idx = 0, old_cta_start
            cta_end = len(body)
            while idx < len(body):
                if body[idx:idx+4] == '<div':
                    depth += 1; idx += 4
                elif body[idx:idx+6] == '</div>':
                    depth -= 1
                    if depth == 0:
                        cta_end = idx + 6; break
                    idx += 6
                else:
                    idx += 1
            return body[:old_cta_start] + ARCHIVE_CTA + body[cta_end:], 'updated'

    # hm-gate widget present — replace it
    if 'id="hm-gate"' in body:
        new_body = _GATE_RE.sub(ARCHIVE_CTA, body, count=1)
        if new_body != body:
            return new_body, 'replaced'
        # Fallback: manual div tracking
        gate_start = body.find('<div id="hm-gate"')
        if gate_start == -1:
            return body, 'no_anchor'
        depth, idx, gate_end = 0, gate_start, len(body)
        while idx < len(body):
            if body[idx:idx+4] == '<div':
                depth += 1; idx += 4
            elif body[idx:idx+6] == '</div>':
                depth -= 1
                if depth == 0:
                    gate_end = idx + 6; break
                idx += 6
            else:
                idx += 1
        remainder = body[gate_end:].lstrip('\n')
        if remainder.startswith('<script>'):
            script_end = remainder.find('</script>') + 9
            gate_end = gate_end + (len(body[gate_end:]) - len(remainder)) + script_end
        return body[:gate_start] + ARCHIVE_CTA + '\n' + body[gate_end:], 'replaced'

    # No hm-gate — append after brown product CTA block
    product_marker = body.rfind('TRY SLEEP HONEY')
    if product_marker != -1:
        close = body.find('</div>', product_marker)
        if close != -1:
            insert_at = close + 6
            return body[:insert_at] + '\n' + ARCHIVE_CTA + body[insert_at:], 'appended'

    return body, 'no_anchor'


# ── Issue pages ────────────────────────────────────────────────────────────────

ISSUE_HANDLES = [
    ("001", "what-honeybees-know-about-sleep"),
    ("002", "cortisol-and-sleep-morning-sets-the-night"),
    ("003", "why-you-wake-at-3am-organ-clocks"),
    ("004", "glymphatic-system-brain-cleaning-during-sleep"),
    ("005", "90-minute-sleep-cycle-bedtime-matters"),
    ("006", "nervous-system-switch-sleep-onset"),
    ("007", "gut-brain-axis-serotonin-sleep"),
    ("008", "body-temperature-sleep-cold-room"),
    ("009", "warm-milk-honey-tryptophan-sleep-science"),
    ("010", "magnesium-deficiency-sleep-problems"),
    ("011", "blue-light-third-photoreceptor-sleep"),
    ("012", "breathing-vagus-nerve-sleep-technique"),
    ("013", "dreams-rem-sleep-emotional-processing"),
    ("014", "alcohol-sleep-architecture-rem-suppression"),
    ("015", "rem-sleep-emotional-memory-processing-dreams"),
]


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    results = {"replaced": [], "appended": [], "updated": [],
               "skipped": [], "no_anchor": [], "error": []}

    for num, handle in ISSUE_HANDLES:
        print(f"  Issue {num} /pages/{handle}", end=" ... ", flush=True)
        page = fetch_page(handle)
        if not page:
            print("NOT FOUND")
            results["error"].append(handle)
            continue

        new_body, action = patch_body(page["body"] or "")

        if action == "skipped":
            print("already done \u2713")
            results["skipped"].append(handle)
            continue

        if action == "no_anchor":
            print("WARNING: no insertion point found")
            results["no_anchor"].append(handle)
            continue

        try:
            save_page(page["id"], page["title"], new_body)
            print(f"{action.upper()} \u2713")
            results[action].append(handle)
        except Exception as exc:
            print(f"ERROR: {exc}")
            results["error"].append(handle)

    print()
    print("=" * 60)
    total = sum(len(results[k]) for k in ("replaced", "appended", "updated"))
    print(f"Done. Changed: {total}  Skipped: {len(results['skipped'])}  "
          f"Warnings: {len(results['no_anchor'])}  Errors: {len(results['error'])}")
    for action, label in [
        ("replaced", "Replaced hm-gate"),
        ("appended", "Appended CTA"),
        ("updated", "Updated link to ?s=1"),
        ("no_anchor", "No anchor \u2014 review"),
        ("error", "Errors"),
    ]:
        if results[action]:
            print(f"\n{label}:")
            for h in results[action]:
                print(f"  /pages/{h}")


if __name__ == "__main__":
    main()
