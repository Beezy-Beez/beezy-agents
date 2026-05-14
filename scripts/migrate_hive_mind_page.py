"""
Migrate a Hive Mind issue page from Zipify-wrapped HTML to native Shopify.

For each page:
1. Extract the inline-styled inner div (drop Zipify outer wrappers)
2. Replace the OLD single-state subscribe box + script + archive link
   with the NEW two-state subscribe component
3. Bump font-size:17px → font-size:18px (Issues 1-12 normalize to 13/14/15 sizes)
4. pageUpdate with new body + templateSuffix: ""

CANONICAL SPEC: /mnt/skills/user/hive-mind-page-template/SKILL.md

Usage:
    python -m scripts.migrate_hive_mind_page --handle <handle> --dry-run
    python -m scripts.migrate_hive_mind_page --handle <handle>   # live
    python -m scripts.migrate_hive_mind_page --list              # list candidate pages
"""
import argparse
import re
import sys

from lib.shopify_admin import graphql


NEW_SUBSCRIBE_COMPONENT = '''<div style="background-color:#f5f0e8; padding:40px 30px; border-radius:8px; margin:0 0 30px 0; text-align:center;">
<div id="hive-mind-pre-sub">
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
<h2 style="font-size:24px; color:#2c2417; margin:0 0 12px 0; font-family:Georgia, serif; font-weight:bold;">\u2713 You\u2019re subscribed</h2>
<p style="font-size:18px; line-height:1.75; color:#5a4a3a; margin:0 0 25px 0; font-family:Georgia, serif;">Watch your inbox for the next issue. Meanwhile, you have full access to every issue we\u2019ve ever sent.</p>
<a href="https://trybeezybeez.com/pages/the-hive-mind" style="display:inline-block; padding:14px 32px; font-size:16px; font-family:Georgia, serif; background-color:#8b4513; color:#fffdf7; text-decoration:none; border-radius:4px; font-weight:bold; letter-spacing:1px;">BROWSE THE ARCHIVE \u2192</a>
</div>
</div>
<script>(function(){var SUB_KEY="bb_hivemind_sub";function showSubscribed(){var pre=document.getElementById("hive-mind-pre-sub");var post=document.getElementById("hive-mind-post-sub");if(pre)pre.style.display="none";if(post)post.style.display="block"}var params=new URLSearchParams(window.location.search);if(params.get("subscriber")==="true"||params.get("s")==="1"){try{localStorage.setItem(SUB_KEY,"true")}catch(_){}}try{if(localStorage.getItem(SUB_KEY)==="true")showSubscribed()}catch(_){}var form=document.getElementById("hive-mind-subscribe-form");if(form){form.addEventListener("submit",function(n){n.preventDefault();var t=document.getElementById("hive-mind-email").value;if(t){var e=this.querySelector("button");e.textContent="Subscribing...";e.disabled=!0;fetch("https://a.klaviyo.com/client/subscriptions/?company_id=W8SW8k",{method:"POST",headers:{"Content-Type":"application/json",revision:"2024-10-15"},body:JSON.stringify({data:{type:"subscription",attributes:{custom_source:"Hive Mind Issue Page",profile:{data:{type:"profile",attributes:{email:t}}}},relationships:{list:{data:{type:"list",id:"Y6VSre"}}}}})}).then(function(i){if(i.ok||i.status===202){try{localStorage.setItem(SUB_KEY,"true")}catch(_){}showSubscribed()}else{document.getElementById("hive-mind-error").style.display="block";e.textContent="Subscribe";e.disabled=!1}}).catch(function(){document.getElementById("hive-mind-error").style.display="block";e.textContent="Subscribe";e.disabled=!1})}})}})();</script>'''


INNER_DIV_MARKER = '<div style="max-width:700px; margin:0 auto; padding:40px 20px; font-family:Georgia, \'Times New Roman\', serif; color:#2c2417;">'


def extract_inner_div(body: str) -> str:
    """Find and extract the inline-styled inner div (full element, tags included)."""
    start_idx = body.find(INNER_DIV_MARKER)
    if start_idx == -1:
        raise ValueError(
            "Inner inline-styled div not found. This page may not be a Hive Mind issue, "
            "or its body uses a different wrapper. Inspect manually before migrating."
        )
    pos = start_idx + len(INNER_DIV_MARKER)
    depth = 1
    while depth > 0:
        next_open = body.find("<div", pos)
        next_close = body.find("</div>", pos)
        if next_close == -1:
            raise ValueError("Unbalanced divs — no matching </div> for inner wrapper.")
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + 4
        else:
            depth -= 1
            pos = next_close + 6
    return body[start_idx:pos]


def replace_subscribe_section(html: str):
    """Replace old subscribe box + script + archive link with the new two-state component.

    Returns (new_html, replaced_bool).
    """
    sub_box_start = html.find('<div style="background-color:#f5f0e8;')
    if sub_box_start == -1:
        return html, False

    # Walk through subscribe box to find its close
    opening_end = html.find(">", sub_box_start) + 1
    pos = opening_end
    depth = 1
    while depth > 0:
        next_open = html.find("<div", pos)
        next_close = html.find("</div>", pos)
        if next_close == -1:
            raise ValueError("Subscribe box not properly closed.")
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + 4
        else:
            depth -= 1
            pos = next_close + 6
    sub_box_end = pos  # right after </div> of subscribe box

    # Optional <script>...</script> immediately after
    script_re = re.compile(r"\s*<script>.*?</script>", re.DOTALL)
    script_match = script_re.match(html, sub_box_end)
    script_end = script_match.end() if script_match else sub_box_end

    # Optional <div id="hm-archive-link"...>...</div> after
    archive_re = re.compile(
        r'\s*<div\s+id="hm-archive-link"[^>]*>.*?</div>', re.DOTALL
    )
    archive_match = archive_re.match(html, script_end)
    archive_end = archive_match.end() if archive_match else script_end

    new_html = html[:sub_box_start] + NEW_SUBSCRIBE_COMPONENT + html[archive_end:]
    return new_html, True


FONT_NORMALIZATIONS = [
    # (old, new, label) — order matters; most specific patterns first
    ("font-size:17px; color:#8b7355;",
     "font-size:16px; color:#8b7355;",
     "breadcrumb/eyebrow"),
    ("font-size:17px; line-height:1.75; color:#2c2417;",
     "font-size:18px; line-height:1.75; color:#2c2417;",
     "body + callout"),
    ("font-size:17px; line-height:1.75; color:#5a4a3a;",
     "font-size:18px; line-height:1.75; color:#5a4a3a;",
     "about blurb"),
    ("font-size:17px; line-height:1.65; color:#fffdf7;",
     "font-size:18px; line-height:1.65; color:#fffdf7;",
     "product banner body"),
    ("font-size:17px; font-family:Georgia, serif; background-color:#f0c75e;",
     "font-size:16px; font-family:Georgia, serif; background-color:#f0c75e;",
     "product banner CTA button"),
    ("font-size:18px; color:#5a4a3a; margin:0 0 30px 0; line-height:1.5; font-style:italic;",
     "font-size:20px; color:#5a4a3a; margin:0 0 30px 0; line-height:1.5; font-style:italic;",
     "dek"),
    ("font-size:20px; line-height:1.5; color:#2c2417; margin:30px 0; text-align:center; font-style:italic;",
     "font-size:22px; line-height:1.5; color:#2c2417; margin:30px 0; text-align:center; font-style:italic;",
     "pullquote"),
]


def normalize_fonts(html):
    """Normalize Issue 001-012 font sizes to canonical Issue 014/015 spec.
    Returns (new_html, [(label, count, old_size, new_size), ...])."""
    report = []
    for old, new, label in FONT_NORMALIZATIONS:
        count = html.count(old)
        if count > 0:
            html = html.replace(old, new)
            old_size = old.split(";")[0].split(":")[1].strip()
            new_size = new.split(";")[0].split(":")[1].strip()
            report.append((label, count, old_size, new_size))
    return html, report


def fetch_page(handle: str = None, page_id: str = None) -> dict:
    if handle:
        query = """
        query getPage($q: String!) {
            pages(first: 1, query: $q) {
                edges { node { id title handle templateSuffix body } }
            }
        }
        """
        data = graphql(query, {"q": f"handle:{handle}"})
        edges = (data.get("pages") or {}).get("edges") or []
        if not edges:
            raise SystemExit(f"No page found with handle: {handle}")
        return edges[0]["node"]
    if page_id:
        query = """
        query getPage($id: ID!) {
            page(id: $id) { id title handle templateSuffix body }
        }
        """
        data = graphql(query, {"id": page_id})
        page = data.get("page")
        if not page:
            raise SystemExit(f"No page found with id: {page_id}")
        return page
    raise SystemExit("Must provide --handle or --page-id")


def update_page(page_id: str, body: str):
    mutation = """
    mutation updateBody($id: ID!, $input: PageUpdateInput!) {
        pageUpdate(id: $id, page: $input) {
            page { id handle templateSuffix }
            userErrors { field message }
        }
    }
    """
    result = graphql(mutation, {
        "id": page_id,
        "input": {"body": body, "templateSuffix": ""},
    })
    errors = (result.get("pageUpdate") or {}).get("userErrors") or []
    if errors:
        print("ERRORS:")
        for e in errors:
            print(f"  - field={e.get('field')} message={e.get('message')}")
        raise SystemExit(1)
    return result["pageUpdate"]["page"]


def list_hive_mind_pages():
    """List all pages with templateSuffix=zipifypages (candidates for migration)."""
    query = """
    query listZipifyPages($cursor: String) {
        pages(first: 50, query: "template_suffix:zipifypages", after: $cursor) {
            edges {
                node { id title handle templateSuffix }
                cursor
            }
            pageInfo { hasNextPage endCursor }
        }
    }
    """
    cursor = None
    rows = []
    while True:
        data = graphql(query, {"cursor": cursor})
        pages = data.get("pages") or {}
        for edge in pages.get("edges") or []:
            rows.append(edge["node"])
        info = pages.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        cursor = info.get("endCursor")
    print(f"Found {len(rows)} pages with templateSuffix='zipifypages':\n")
    for r in rows:
        print(f"  {r['handle']:60s} {r['title']}")


def migrate(handle=None, page_id=None, dry_run=False):
    page = fetch_page(handle=handle, page_id=page_id)
    print(f"Page:            {page['title']}")
    print(f"Handle:          {page['handle']}")
    print(f"ID:              {page['id']}")
    print(f"Suffix (before): {page.get('templateSuffix') or '(none)'}")
    print(f"Body (before):   {len(page['body']):,} chars")

    inner = extract_inner_div(page["body"])
    print(f"Inner extracted: {len(inner):,} chars (dropped {len(page['body']) - len(inner):,} chars of Zipify wrappers)")

    new_inner, replaced = replace_subscribe_section(inner)
    print(f"Subscribe swap:  {'yes' if replaced else 'no (no old subscribe box found)'}")

    bumped, font_report = normalize_fonts(new_inner)
    total = sum(item[1] for item in font_report)
    print(f"Font normalizations: {total} total")
    for label, count, old_size, new_size in font_report:
        print(f"  {old_size} → {new_size} ({label}): {count}x")
    print(f"Body (after):    {len(bumped):,} chars")
    print()

    if dry_run:
        print("=" * 70)
        print("DRY-RUN — no writes to Shopify.")
        print("=" * 70)
        print()
        print("--- FIRST 600 CHARS OF NEW BODY ---")
        print(bumped[:600])
        print()
        print("--- LAST 600 CHARS OF NEW BODY ---")
        print(bumped[-600:])
        print()
        print(f"To apply: python -m scripts.migrate_hive_mind_page --handle {page['handle']}")
        return

    result = update_page(page["id"], bumped)
    print(f"Updated. suffix now: {result.get('templateSuffix') or '(empty)'}")
    print(f"Live: https://trybeezybeez.com/pages/{page['handle']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--handle", help="Page handle")
    parser.add_argument("--page-id", help="Shopify page GID")
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")
    parser.add_argument("--list", action="store_true", help="List all Zipify pages")
    args = parser.parse_args()
    if args.list:
        list_hive_mind_pages()
        return
    if not args.handle and not args.page_id:
        parser.error("Must provide --handle, --page-id, or --list")
    migrate(handle=args.handle, page_id=args.page_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
