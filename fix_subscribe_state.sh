#!/usr/bin/env bash
# fix_subscribe_state.sh
# Replaces the single-state subscribe box with a two-state design:
#   - Pre-sub: "Get The Hive Mind in Your Inbox" + form (default)
#   - Post-sub: "✓ You're subscribed" + "BROWSE THE ARCHIVE →" button
# Toggled by the existing bb_hivemind_sub localStorage flag, so reader subscribes
# once and every Hive Mind page renders the subscribed state forever.
# Removes the now-redundant separate hm-archive-link div.

set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f workers/shopify_page_builder.py ]]; then
    echo "FATAL: workers/shopify_page_builder.py not found" >&2
    exit 1
fi

echo "[fix] patching workers/shopify_page_builder.py — three surgical replacements..."

python <<'PYEOF'
import re
from pathlib import Path

p = Path("workers/shopify_page_builder.py")
src = p.read_text()

new_box_const = '''SUBSCRIBE_BOX_HTML = """<div id="hive-mind-pre-sub">
<h2 style="font-size:24px; color:#2c2417; margin:0 0 12px 0; font-family:Georgia, serif; font-weight:bold;">Get The Hive Mind in Your Inbox</h2>
<p style="font-size:18px; line-height:1.75; color:#5a4a3a; margin:0 0 25px 0; font-family:Georgia, serif;">One sleep science deep-dive every three days. No fluff. No products pushed. Just the research and what it means for your nights.</p>
<form id="hive-mind-subscribe-form" style="margin:0 auto; display:inline-block;">
<table cellpadding="0" cellspacing="0" border="0">
<tr>
<td style="padding-right:8px;">
<input type="email" id="hive-mind-email" placeholder="your@email.com" required style="width:280px; padding:14px 18px; font-size:16px; font-family:Georgia, serif; border:1px solid #d4a847; border-radius:4px; background:#fffdf7; color:#2c2417; box-sizing:border-box;">
</td>
<td>
<button type="submit" style="padding:14px 28px; font-size:16px; font-family:Georgia, serif; background-color:#8b4513; color:#fffdf7; text-align:center; text-decoration:none; border-radius:4px; font-weight:bold; border:none; cursor:pointer;">Subscribe</button>
</td>
</tr>
</table>
</form>
<p id="hive-mind-error" style="display:none; font-size:16px; color:#8b4513; margin:15px 0 0 0; font-family:Georgia, serif;">Something went wrong. Please try again.</p>
</div>
<div id="hive-mind-post-sub" style="display:none;">
<h2 style="font-size:24px; color:#2c2417; margin:0 0 12px 0; font-family:Georgia, serif; font-weight:bold;">✓ You\u2019re subscribed</h2>
<p style="font-size:18px; line-height:1.75; color:#5a4a3a; margin:0 0 25px 0; font-family:Georgia, serif;">Watch your inbox for the next issue. Meanwhile, you have full access to every issue we\u2019ve ever sent.</p>
<a href="https://trybeezybeez.com/pages/the-hive-mind" style="display:inline-block; padding:14px 32px; font-size:16px; font-family:Georgia, serif; background-color:#8b4513; color:#fffdf7; text-decoration:none; border-radius:4px; font-weight:bold; letter-spacing:1px;">BROWSE THE ARCHIVE \u2192</a>
</div>"""'''

# Step 1: rename SUBSCRIBE_FORM_HTML to SUBSCRIBE_BOX_HTML with new two-state content
src, n1 = re.subn(
    r'SUBSCRIBE_FORM_HTML\s*=\s*"""[\s\S]*?"""',
    lambda _: new_box_const,
    src,
    count=1,
)
if n1 != 1:
    raise SystemExit(f"FAIL step 1: expected 1 replacement, got {n1}")
print(f"  step 1: SUBSCRIBE_FORM_HTML \u2192 SUBSCRIBE_BOX_HTML")

new_script = '''SUBSCRIBE_SCRIPT = """<script>(function(){var SUB_KEY="bb_hivemind_sub";function showSubscribed(){var pre=document.getElementById("hive-mind-pre-sub");var post=document.getElementById("hive-mind-post-sub");if(pre)pre.style.display="none";if(post)post.style.display="block"}var params=new URLSearchParams(window.location.search);if(params.get("subscriber")==="true"||params.get("s")==="1"){try{localStorage.setItem(SUB_KEY,"true")}catch(_){}}try{if(localStorage.getItem(SUB_KEY)==="true")showSubscribed()}catch(_){}var form=document.getElementById("hive-mind-subscribe-form");if(form){form.addEventListener("submit",function(n){n.preventDefault();var t=document.getElementById("hive-mind-email").value;if(t){var e=this.querySelector("button");e.textContent="Subscribing...";e.disabled=!0;fetch("https://a.klaviyo.com/client/subscriptions/?company_id=W8SW8k",{method:"POST",headers:{"Content-Type":"application/json",revision:"2024-10-15"},body:JSON.stringify({data:{type:"subscription",attributes:{custom_source:"Hive Mind Issue Page",profile:{data:{type:"profile",attributes:{email:t}}}},relationships:{list:{data:{type:"list",id:"Y6VSre"}}}}})}).then(function(i){if(i.ok||i.status===202){try{localStorage.setItem(SUB_KEY,"true")}catch(_){}showSubscribed()}else{document.getElementById("hive-mind-error").style.display="block";e.textContent="Subscribe";e.disabled=!1}}).catch(function(){document.getElementById("hive-mind-error").style.display="block";e.textContent="Subscribe";e.disabled=!1})}})}})();</script>"""'''

# Step 2: rewrite SUBSCRIBE_SCRIPT with showSubscribed() toggle logic
src, n2 = re.subn(
    r'SUBSCRIBE_SCRIPT\s*=\s*"""[\s\S]*?"""',
    lambda _: new_script,
    src,
    count=1,
)
if n2 != 1:
    raise SystemExit(f"FAIL step 2: expected 1 replacement, got {n2}")
print(f"  step 2: SUBSCRIBE_SCRIPT swapped for two-state toggle")

new_section = """    # Subscribe box (two-state: pre-sub form + post-sub archive CTA)
    parts.append(f'<div style="{STYLES[\"sub_box\"]}">')
    parts.append(SUBSCRIBE_BOX_HTML)
    parts.append("</div>")

    # Subscribe handler script (toggles via bb_hivemind_sub localStorage flag)
    parts.append(SUBSCRIBE_SCRIPT)

    """

# Step 3: replace the subscribe section AND archive-link div in build_page_html
# Anchors: '    # Subscribe box' start, '    # About blurb' end (kept)
src, n3 = re.subn(
    r'    # Subscribe box\n[\s\S]*?(?=    # About blurb)',
    lambda _: new_section,
    src,
    count=1,
)
if n3 != 1:
    raise SystemExit(f"FAIL step 3: expected 1 replacement, got {n3}")
print(f"  step 3: build_page_html subscribe section + archive-link div removed")

p.write_text(src)
PYEOF

echo "[fix] syntax check..."
python -c "import ast; ast.parse(open('workers/shopify_page_builder.py').read()); print('  page_builder.py OK')"

echo "[fix] unit checks..."
python <<'PYEOF'
import importlib, workers.shopify_page_builder as m
importlib.reload(m)
from workers.shopify_page_builder import build_page_html, _inline_format

# 1. Auto-linkification still works
out = _inline_format("Find us at trybeezybeez.com today.")
assert '<a href="https://trybeezybeez.com"' in out, f"FAIL linkify: {out}"
print("  auto-linkification        OK")

# 2. New two-state markers in SUBSCRIBE_BOX_HTML
assert 'id="hive-mind-pre-sub"' in m.SUBSCRIBE_BOX_HTML
assert 'id="hive-mind-post-sub"' in m.SUBSCRIBE_BOX_HTML
assert 'BROWSE THE ARCHIVE' in m.SUBSCRIBE_BOX_HTML
assert 'pages/the-hive-mind' in m.SUBSCRIBE_BOX_HTML
print("  two-state subscribe box    OK")

# 3. ✓ character (not &check;)
assert '\u2713' in m.SUBSCRIBE_BOX_HTML, "FAIL: ✓ missing in post-sub"
assert '&check;' not in m.SUBSCRIBE_BOX_HTML
print("  ✓ character                OK")

# 4. Script has showSubscribed toggle
assert 'showSubscribed' in m.SUBSCRIBE_SCRIPT
assert 'hive-mind-pre-sub' in m.SUBSCRIBE_SCRIPT
assert 'hive-mind-post-sub' in m.SUBSCRIBE_SCRIPT
assert 'bb_hivemind_sub' in m.SUBSCRIBE_SCRIPT
print("  showSubscribed() toggle    OK")

# 5. Old hm-archive-link div is gone from output
fake = {
    "number": 15,
    "page_title": "Test",
    "page_dek": "Test dek",
    "page_breadcrumb_label": "Test",
    "long_form_body": "Opening paragraph.\n\n## The One Thing\n\nDo it.",
    "until_next_teaser": "next",
    "read_time_min": 5,
    "shopify_image_url": "https://example.com/cover.jpg",
    "preview_text": "preview",
}
html = build_page_html(fake)
assert 'id="hm-archive-link"' not in html, "FAIL: old archive-link still in output"
assert 'id="hive-mind-pre-sub"' in html
assert 'id="hive-mind-post-sub"' in html
assert 'BROWSE THE ARCHIVE' in html
print("  hm-archive-link removed    OK")

print("\n  all unit checks passed")
PYEOF

echo ""
echo "[fix] re-rendering Issue 15..."
echo ""
python -m scripts.update_issue_page --issue 15
