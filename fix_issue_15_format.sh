#!/usr/bin/env bash
# fix_issue_15_format.sh
# Rewrites workers/shopify_page_builder.py to produce HTML matching Issue 14's template
# byte-for-byte (inline-styled Hive Mind layout: breadcrumb, eyebrow, H1, dek, cover image,
# narrative body with H2 sections and pullquotes, boxed "One Thing" callout, gold divider,
# product banner, subscribe form + Klaviyo JS, archive link, About blurb, back link).
# Adds update_page() to workers/shopify_publisher.py (pageUpdate mutation).
# Updates Issue 15's existing Shopify page in-place — same page ID, no new page created.

set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d workers ]] || [[ ! -f config.py ]]; then
    echo "FATAL: run from beezy-agents workspace root" >&2
    exit 1
fi

echo "[fix] step 1/4 — rewriting workers/shopify_page_builder.py..."
cat > workers/shopify_page_builder.py <<'PYEOF'
"""Build Hive Mind issue page body HTML matching the existing template (Issue 14 reference).

The output is a single inline-styled <div> with the canonical Hive Mind layout:
breadcrumb, eyebrow line, H1, italic dek, cover image, narrative body, boxed
"One Thing Worth..." callout, "Until next issue" line, gold divider, product banner,
subscribe form with Klaviyo Client API JS, post-subscribe archive link, About blurb,
back link to /pages/sleep-science-hub.

Body markdown is parsed with a small purpose-built parser. Supports:
  - ## H2 headings
  - Paragraphs (blank-line separated)
  - --- horizontal rules
  - > pull quotes (rendered centered/italic/large)
  - · · ·  decorative dots
  - **bold**, *italic*, _italic_, [link](url) inline
The LAST H2 section whose heading contains "One Thing" or "the One" is rendered as the
boxed callout at the end of the body.
"""
from __future__ import annotations

import re
from typing import Any


STYLES = {
    "outer":            "max-width:700px; margin:0 auto; padding:40px 20px; font-family:Georgia, 'Times New Roman', serif; color:#2c2417;",
    "breadcrumb":       "font-size:16px; color:#8b7355; margin:0 0 30px 0;",
    "breadcrumb_link":  "color:#8b7355; text-decoration:none;",
    "meta":             "font-size:16px; color:#8b7355; margin:0 0 10px 0;",
    "h1":               "font-size:32px; font-weight:bold; color:#2c2417; margin:0 0 12px 0; line-height:1.25; font-family:Georgia, serif;",
    "dek":              "font-size:20px; color:#5a4a3a; margin:0 0 30px 0; line-height:1.5; font-style:italic;",
    "cover_img":        "display:block; width:100%; height:auto; margin:0 0 35px 0; border-radius:4px;",
    "h2":               "font-size:22px; color:#2c2417; margin:0 0 18px 0; font-family:Georgia, serif;",
    "p":                "font-size:18px; line-height:1.75; color:#2c2417; margin:0 0 18px 0;",
    "p_before_hr":      "font-size:18px; line-height:1.75; color:#2c2417; margin:0 0 28px 0;",
    "p_callout_last":   "font-size:18px; line-height:1.75; color:#2c2417; margin:0 0 0 0;",
    "hr":               "border:none; border-top:1px solid #e8dcc8; margin:0 0 28px 0;",
    "pullquote":        "font-size:22px; line-height:1.5; color:#2c2417; margin:30px 0; text-align:center; font-style:italic;",
    "dots":             "font-size:24px; color:#8b7355; margin:30px 0; text-align:center; letter-spacing:12px;",
    "callout_box":      "border:1px solid #e8dcc8; border-radius:6px; padding:30px 28px; margin:0 0 35px 0; background-color:#fffdf7;",
    "gold_divider":     "border:none; border-top:2px solid #d4a847; margin:0 0 0 0;",
    "product_banner":   "background: linear-gradient(135deg, #8b4513, #a0522d, #6b3410); padding:40px 30px; border-radius:8px; margin:35px 0; text-align:center;",
    "product_h2":       "font-size:24px; color:#fffdf7; margin:0 0 15px 0; font-family:Georgia, serif; font-weight:bold; font-style:italic;",
    "product_p":        "font-size:18px; line-height:1.65; color:#fffdf7; margin:0 0 25px 0; font-family:Georgia, serif; opacity:0.9;",
    "product_btn":      "display:inline-block; padding:14px 32px; font-size:16px; font-family:Georgia, serif; background-color:#f0c75e; color:#2c2417; text-align:center; text-decoration:none; border-radius:4px; font-weight:bold; letter-spacing:1px;",
    "sub_box":          "background-color:#f5f0e8; padding:40px 30px; border-radius:8px; margin:0 0 30px 0; text-align:center;",
    "sub_h2":           "font-size:24px; color:#2c2417; margin:0 0 12px 0; font-family:Georgia, serif; font-weight:bold;",
    "sub_p":            "font-size:18px; line-height:1.75; color:#5a4a3a; margin:0 0 25px 0; font-family:Georgia, serif;",
    "about":            "font-size:18px; line-height:1.75; color:#5a4a3a; margin:0 0 25px 0;",
    "about_link":       "color:#d4a847; text-decoration:underline;",
    "back_p":           "font-size:18px; margin:0;",
    "back_link":        "color:#d4a847; text-decoration:none; font-weight:bold;",
    "inline_link":      "color:#d4a847; text-decoration:underline;",
}


def _inline_format(text: str) -> str:
    """Apply inline markdown: [link](url) → <a>, **bold** → <strong>, *em*/_em_ → <em>."""
    text = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        lambda m: f'<a href="{m.group(2)}" style="{STYLES["inline_link"]}">{m.group(1)}</a>',
        text,
    )
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<![*\w])\*([^*\s][^*]*?)\*(?![*\w])', r'<em>\1</em>', text)
    text = re.sub(r'(?<![_\w])_([^_\s][^_]*?)_(?![_\w])', r'<em>\1</em>', text)
    return text


def _parse_body(markdown: str) -> list[dict]:
    """Parse markdown into a flat list of typed blocks."""
    if not markdown:
        return []

    blocks: list[dict] = []
    current_para: list[str] = []
    current_bq: list[str] = []

    def flush_para():
        if current_para:
            text = " ".join(current_para).strip()
            if text:
                blocks.append({"type": "p", "text": text})
            current_para.clear()

    def flush_bq():
        if current_bq:
            text = " ".join(current_bq).strip()
            if text:
                blocks.append({"type": "pullquote", "text": text})
            current_bq.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.strip()

        if not line:
            flush_para()
            flush_bq()
            continue

        if line in ("---", "***", "___"):
            flush_para(); flush_bq()
            blocks.append({"type": "hr"})
            continue

        # decorative dots (variations: "· · ·", "...", "...", "* * *")
        if line in ("· · ·", "...", "* * *", "* * * *"):
            flush_para(); flush_bq()
            blocks.append({"type": "dots"})
            continue

        if line.startswith("## "):
            flush_para(); flush_bq()
            blocks.append({"type": "h2", "text": line[3:].strip()})
            continue

        if line.startswith("# "):
            # Treat as H2 if it ever appears in body (we use page_title for the page H1)
            flush_para(); flush_bq()
            blocks.append({"type": "h2", "text": line[2:].strip()})
            continue

        if line.startswith("> "):
            flush_para()
            current_bq.append(line[2:].strip())
            continue

        # Regular paragraph line
        flush_bq()
        current_para.append(line)

    flush_para()
    flush_bq()
    return blocks


def _is_callout_heading(text: str) -> bool:
    """Detect 'One Thing Worth...' style callout heading (case-insensitive)."""
    t = text.lower()
    return ("one thing" in t) or ("the one" in t and "thing" in t)


def _split_callout(blocks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split blocks into (main_body, callout) based on the last 'One Thing' H2.
    Returns (all_blocks, []) if no callout heading found.
    Strips trailing hr/dots from main_body before the callout."""
    callout_idx = None
    for i in range(len(blocks) - 1, -1, -1):
        b = blocks[i]
        if b["type"] == "h2" and _is_callout_heading(b["text"]):
            callout_idx = i
            break

    if callout_idx is None:
        return blocks, []

    main = list(blocks[:callout_idx])
    callout = list(blocks[callout_idx:])
    while main and main[-1]["type"] in ("hr", "dots"):
        main.pop()
    return main, callout


def _render_blocks(blocks: list[dict], in_callout: bool = False) -> str:
    """Render typed blocks into inline-styled HTML."""
    parts: list[str] = []
    n = len(blocks)
    for i, b in enumerate(blocks):
        t = b["type"]
        next_t = blocks[i + 1]["type"] if i + 1 < n else None
        is_last = (i == n - 1)

        if t == "p":
            if in_callout and is_last:
                style = STYLES["p_callout_last"]
            elif next_t in ("hr", "dots"):
                style = STYLES["p_before_hr"]
            else:
                style = STYLES["p"]
            parts.append(f'<p style="{style}">{_inline_format(b["text"])}</p>')
        elif t == "h2":
            parts.append(f'<h2 style="{STYLES["h2"]}">{_inline_format(b["text"])}</h2>')
        elif t == "hr":
            parts.append(f'<hr style="{STYLES["hr"]}">')
        elif t == "pullquote":
            parts.append(f'<p style="{STYLES["pullquote"]}">{_inline_format(b["text"])}</p>')
        elif t == "dots":
            parts.append(f'<p style="{STYLES["dots"]}">· · ·</p>')
    return "\n".join(parts)


SUBSCRIBE_FORM_HTML = """<form id="hive-mind-subscribe-form" style="margin:0 auto; display:inline-block;">
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
<p id="hive-mind-success" style="display:none; font-size:16px; color:#2c2417; margin:15px 0 0 0; font-family:Georgia, serif; font-weight:bold;">&check; You're in. Watch your inbox.</p>
<p id="hive-mind-error" style="display:none; font-size:16px; color:#8b4513; margin:15px 0 0 0; font-family:Georgia, serif;">Something went wrong. Please try again.</p>"""


SUBSCRIBE_SCRIPT = """<script>(function(){var SUB_KEY="bb_hivemind_sub";function showArchive(){var el=document.getElementById("hm-archive-link");if(el)el.style.display="block"}var params=new URLSearchParams(window.location.search);if(params.get("subscriber")==="true"||params.get("s")==="1"){try{localStorage.setItem(SUB_KEY,"true")}catch(_){}}try{if(localStorage.getItem(SUB_KEY)==="true")showArchive()}catch(_){}var form=document.getElementById("hive-mind-subscribe-form");if(form){form.addEventListener("submit",function(n){n.preventDefault();var t=document.getElementById("hive-mind-email").value;if(t){var e=this.querySelector("button");e.textContent="Subscribing...";e.disabled=!0;fetch("https://a.klaviyo.com/client/subscriptions/?company_id=W8SW8k",{method:"POST",headers:{"Content-Type":"application/json",revision:"2024-10-15"},body:JSON.stringify({data:{type:"subscription",attributes:{custom_source:"Hive Mind Issue Page",profile:{data:{type:"profile",attributes:{email:t}}}},relationships:{list:{data:{type:"list",id:"Y6VSre"}}}}})}).then(function(i){if(i.ok||i.status===202){try{localStorage.setItem(SUB_KEY,"true")}catch(_){}document.getElementById("hive-mind-subscribe-form").style.display="none";document.getElementById("hive-mind-success").style.display="block";showArchive()}else{document.getElementById("hive-mind-error").style.display="block";e.textContent="Subscribe";e.disabled=!1}}).catch(function(){document.getElementById("hive-mind-error").style.display="block";e.textContent="Subscribe";e.disabled=!1})}})}})();</script>"""


def build_page_html(issue: dict[str, Any]) -> str:
    """Build the inline-styled Hive Mind issue page body HTML.

    Required fields on `issue`:
        number, page_title, page_dek, page_breadcrumb_label,
        long_form_body, until_next_teaser, read_time_min,
        shopify_image_url (or cover_image_url as fallback)
    """
    n = int(issue["number"])
    issue_num_padded = f"{n:03d}"
    breadcrumb = (issue.get("page_breadcrumb_label") or "").strip()
    page_title = (issue.get("page_title") or "").strip()
    page_dek = (issue.get("page_dek") or "").strip()
    cover_url = issue.get("shopify_image_url") or issue.get("cover_image_url") or ""
    read_time = issue.get("read_time_min") or 5
    teaser = (issue.get("until_next_teaser") or "").strip()
    body_md = issue.get("long_form_body") or ""

    blocks = _parse_body(body_md)
    main_blocks, callout_blocks = _split_callout(blocks)

    main_html = _render_blocks(main_blocks, in_callout=False)
    callout_html = _render_blocks(callout_blocks, in_callout=True) if callout_blocks else ""

    alt_text = f"The Hive Mind Issue {issue_num_padded} — {breadcrumb}"
    teaser_html = _inline_format(teaser)

    parts: list[str] = []
    parts.append(f'<div style="{STYLES["outer"]}">')

    # Breadcrumb
    parts.append(
        f'<p style="{STYLES["breadcrumb"]}">'
        f'<a href="https://trybeezybeez.com" style="{STYLES["breadcrumb_link"]}">Home</a> / '
        f'<a href="https://trybeezybeez.com/pages/sleep-science-hub" style="{STYLES["breadcrumb_link"]}">Sleep Science Hub</a> / '
        f'{breadcrumb}'
        f'</p>'
    )

    # Eyebrow meta line
    parts.append(
        f'<p style="{STYLES["meta"]}">'
        f'The Hive Mind · Issue {issue_num_padded} · {read_time} min read'
        f'</p>'
    )

    # H1
    parts.append(f'<h1 style="{STYLES["h1"]}">{page_title}</h1>')

    # Dek
    parts.append(f'<p style="{STYLES["dek"]}">{page_dek}</p>')

    # Cover image
    parts.append(
        f'<img src="{cover_url}" width="100%" style="{STYLES["cover_img"]}" alt="{alt_text}">'
    )

    # Main body
    if main_html:
        parts.append(main_html)

    # Decorative dots + boxed callout
    if callout_html:
        parts.append(f'<p style="{STYLES["dots"]}">· · ·</p>')
        parts.append(f'<div style="{STYLES["callout_box"]}">')
        parts.append(callout_html)
        parts.append("</div>")

    # Until next issue
    parts.append(f'<h2 style="{STYLES["h2"]}">Until next issue</h2>')
    if teaser_html:
        parts.append(f'<p style="{STYLES["p_before_hr"]}">Next: {teaser_html}</p>')

    # Gold divider
    parts.append(f'<hr style="{STYLES["gold_divider"]}">')

    # Product banner
    parts.append(f'<div style="{STYLES["product_banner"]}">')
    parts.append(
        f'<h2 style="{STYLES["product_h2"]}">Built to Support Your Body\'s Natural Rhythm</h2>'
    )
    parts.append(
        f'<p style="{STYLES["product_p"]}">'
        f'Beezy Beez Botanical Extract Sleep Honey is designed to support the wind-down phase of your circadian cycle — when your body wants to drop into rest, but stress or overstimulation gets in the way. Clean ingredients. Trusted by 8,500+ five-star customers.'
        f'</p>'
    )
    parts.append(
        f'<a href="https://trybeezybeez.com/products/honey-sub" style="{STYLES["product_btn"]}">TRY SLEEP HONEY →</a>'
    )
    parts.append("</div>")

    # Subscribe box
    parts.append(f'<div style="{STYLES["sub_box"]}">')
    parts.append(f'<h2 style="{STYLES["sub_h2"]}">Get The Hive Mind in Your Inbox</h2>')
    parts.append(
        f'<p style="{STYLES["sub_p"]}">'
        f'One sleep science deep-dive every three days. No fluff. No products pushed. Just the research and what it means for your nights.'
        f'</p>'
    )
    parts.append(SUBSCRIBE_FORM_HTML)
    parts.append("</div>")

    # Subscribe handler script
    parts.append(SUBSCRIBE_SCRIPT)

    # Archive link (revealed after subscribe)
    parts.append(
        '<div id="hm-archive-link" style="display:none; text-align:center; padding:28px 24px; margin:30px 0; background-color:#fffdf7; border:1px dashed #d4a847; border-radius:8px;">'
        '<p style="font-size:18px; color:#2c2417; margin:0 0 8px 0; font-family:Georgia, serif;"><strong>Already subscribed?</strong></p>'
        '<p style="font-size:16px; line-height:1.5; color:#5a4a3a; margin:0; font-family:Georgia, serif;">'
        '<a href="https://trybeezybeez.com/pages/the-hive-mind" style="color:#8b4513; text-decoration:underline; font-weight:bold;">Browse every Hive Mind issue →</a>'
        '</p>'
        '</div>'
    )

    # About blurb
    parts.append(
        f'<p style="{STYLES["about"]}">'
        f'<strong>About Beezy Beez.</strong> Beezy Beez crafts '
        f'<a href="https://trybeezybeez.com/products/honey-sub" style="{STYLES["about_link"]}">botanical extract honey</a> '
        f'for people navigating sleep changes after 50. The Hive Mind is the brand\'s editorial letter on the science and history of rest.'
        f'</p>'
    )

    # Back link
    parts.append(
        f'<p style="{STYLES["back_p"]}">'
        f'<a href="https://trybeezybeez.com/pages/sleep-science-hub" style="{STYLES["back_link"]}">← Back to the Sleep Science Hub</a>'
        f'</p>'
    )

    parts.append("</div>")  # close outer

    return "\n".join(parts)
PYEOF
echo "[fix]   workers/shopify_page_builder.py rewritten"

echo "[fix] step 2/4 — rewriting workers/shopify_publisher.py with update_page()..."
cat > workers/shopify_publisher.py <<'PYEOF'
"""Shopify Pages publisher for Hive Mind issues.

  upload_image_to_shopify(url, alt) — fileCreate via Admin GraphQL.
  create_page(...)                  — pageCreate (new page).
  update_page(page_id, ...)         — pageUpdate (in-place body/SEO update, preserves ID).

SEO is set via metafields (global.title_tag, global.description_tag) since
PageCreateInput / PageUpdateInput do NOT have a `seo` field in Admin API 2025-10.

Requires SHOPIFY_SHOP_DOMAIN and SHOPIFY_ACCESS_TOKEN in env.
Required app scopes: write_content, write_files.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from lib.shopify_admin import graphql


PUBLIC_HOST = "https://trybeezybeez.com"


def upload_image_to_shopify(source_url: str, alt: str = "",
                            poll_timeout_seconds: float = 90.0) -> dict[str, str]:
    if not source_url:
        raise ValueError("source_url is required")

    create_mutation = """
    mutation fileCreate($files: [FileCreateInput!]!) {
        fileCreate(files: $files) {
            files {
                id
                fileStatus
                alt
                ... on MediaImage {
                    image { url }
                }
            }
            userErrors { field message code }
        }
    }
    """
    variables = {
        "files": [{
            "originalSource": source_url,
            "contentType": "IMAGE",
            "alt": alt or "",
        }]
    }
    data = graphql(create_mutation, variables)
    result = data.get("fileCreate") or {}
    user_errors = result.get("userErrors") or []
    if user_errors:
        raise RuntimeError(f"fileCreate userErrors: {user_errors}")
    files = result.get("files") or []
    if not files:
        raise RuntimeError(f"fileCreate returned no files: {result}")

    file_obj = files[0]
    file_id = file_obj["id"]
    image_url = _extract_image_url(file_obj)
    if image_url:
        return {"id": file_id, "url": image_url}

    deadline = time.time() + poll_timeout_seconds
    while time.time() < deadline:
        time.sleep(2.0)
        file_obj = _get_file(file_id)
        status = file_obj.get("fileStatus")
        image_url = _extract_image_url(file_obj)
        if image_url:
            return {"id": file_id, "url": image_url}
        if status == "FAILED":
            raise RuntimeError(f"File ingestion FAILED for {file_id}: {file_obj}")

    raise RuntimeError(f"File ingestion did not complete in {poll_timeout_seconds}s (file_id={file_id})")


def _get_file(file_id: str) -> dict[str, Any]:
    query = """
    query getFile($id: ID!) {
        node(id: $id) {
            ... on MediaImage {
                id
                fileStatus
                alt
                image { url }
            }
        }
    }
    """
    data = graphql(query, {"id": file_id})
    return data.get("node") or {}


def _extract_image_url(file_obj: dict[str, Any]) -> Optional[str]:
    img = file_obj.get("image") or {}
    return img.get("url") if isinstance(img, dict) else None


def _build_metafields(seo_title: Optional[str], seo_description: Optional[str],
                     image_file_id: Optional[str]) -> list[dict[str, str]]:
    mf: list[dict[str, str]] = []
    if seo_title:
        mf.append({"namespace": "global", "key": "title_tag",
                   "value": seo_title, "type": "single_line_text_field"})
    if seo_description:
        mf.append({"namespace": "global", "key": "description_tag",
                   "value": seo_description, "type": "multi_line_text_field"})
    if image_file_id:
        mf.append({"namespace": "global", "key": "image",
                   "value": image_file_id, "type": "file_reference"})
    return mf


def create_page(
    title: str,
    body_html: str,
    handle: str,
    *,
    seo_title: Optional[str] = None,
    seo_description: Optional[str] = None,
    is_published: bool = False,
    image_file_id: Optional[str] = None,
    template_suffix: Optional[str] = None,
) -> dict[str, Any]:
    mutation = """
    mutation pageCreate($page: PageCreateInput!) {
        pageCreate(page: $page) {
            page {
                id
                handle
                title
                isPublished
                publishedAt
                templateSuffix
            }
            userErrors { field message code }
        }
    }
    """
    page_input: dict[str, Any] = {
        "title": title,
        "body": body_html,
        "handle": handle,
        "isPublished": is_published,
    }
    if template_suffix is not None:
        page_input["templateSuffix"] = template_suffix
    metafields = _build_metafields(seo_title, seo_description, image_file_id)
    if metafields:
        page_input["metafields"] = metafields

    data = graphql(mutation, {"page": page_input})
    result = data.get("pageCreate") or {}
    user_errors = result.get("userErrors") or []
    if user_errors:
        raise RuntimeError(f"pageCreate userErrors: {user_errors}")

    page = result.get("page") or {}
    page_handle = page.get("handle") or handle
    public_url = f"{PUBLIC_HOST}/pages/{page_handle}"

    return {
        "id": page.get("id"),
        "handle": page_handle,
        "title": page.get("title"),
        "url": public_url,
        "is_published": bool(page.get("isPublished")),
        "published_at": page.get("publishedAt"),
        "template_suffix": page.get("templateSuffix"),
    }


def update_page(
    page_id: str,
    title: str,
    body_html: str,
    *,
    seo_title: Optional[str] = None,
    seo_description: Optional[str] = None,
    image_file_id: Optional[str] = None,
    template_suffix: Optional[str] = None,
) -> dict[str, Any]:
    """Update an existing Shopify Page in-place. Preserves page ID, handle, URL.

    Does NOT toggle isPublished — set visibility separately if needed.
    """
    mutation = """
    mutation pageUpdate($id: ID!, $page: PageUpdateInput!) {
        pageUpdate(id: $id, page: $page) {
            page {
                id
                handle
                title
                isPublished
                publishedAt
                templateSuffix
            }
            userErrors { field message code }
        }
    }
    """
    page_input: dict[str, Any] = {
        "title": title,
        "body": body_html,
    }
    if template_suffix is not None:
        page_input["templateSuffix"] = template_suffix
    metafields = _build_metafields(seo_title, seo_description, image_file_id)
    if metafields:
        page_input["metafields"] = metafields

    data = graphql(mutation, {"id": page_id, "page": page_input})
    result = data.get("pageUpdate") or {}
    user_errors = result.get("userErrors") or []
    if user_errors:
        raise RuntimeError(f"pageUpdate userErrors: {user_errors}")

    page = result.get("page") or {}
    page_handle = page.get("handle")
    public_url = f"{PUBLIC_HOST}/pages/{page_handle}"

    return {
        "id": page.get("id"),
        "handle": page_handle,
        "title": page.get("title"),
        "url": public_url,
        "is_published": bool(page.get("isPublished")),
        "published_at": page.get("publishedAt"),
        "template_suffix": page.get("templateSuffix"),
    }
PYEOF
echo "[fix]   workers/shopify_publisher.py rewritten (with update_page)"

echo "[fix] step 3/4 — creating scripts/update_issue_page.py..."
cat > scripts/update_issue_page.py <<'PYEOF'
"""Rebuild and update a Hive Mind issue's Shopify page body in-place.

Preserves shopify_page_id and shopify_page_url. Does NOT toggle visibility.
Use this to fix a broken page after the builder is improved.

Usage:
    python -m scripts.update_issue_page --issue 15
    python -m scripts.update_issue_page --issue 15 --template-suffix zipifypages
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg

from config import DATABASE_URL
from lib.slack import post_draft
from workers.shopify_page_builder import build_page_html
from workers.shopify_publisher import update_page


def _fetch_issue(conn: psycopg.Connection, number: int):
    cur = conn.execute(
        """
        select number, page_title, page_dek, page_breadcrumb_label, page_slug,
               long_form_body, until_next_teaser, read_time_min,
               cover_image_url, shopify_image_id, shopify_image_url,
               shopify_page_id, shopify_page_handle, shopify_page_url, preview_text
        from issues where number = %s
        """,
        (number,),
    )
    row = cur.fetchone()
    if not row:
        return None
    cols = [d.name for d in cur.description]
    return dict(zip(cols, row))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--template-suffix", default="zipifypages",
                        help="Shopify template suffix. Default: zipifypages (matches existing pages).")
    parser.add_argument("--no-slack", action="store_true")
    args = parser.parse_args(argv)

    with psycopg.connect(DATABASE_URL) as conn:
        issue = _fetch_issue(conn, args.issue)

    if not issue:
        print(f"[update] No issue {args.issue} in DB", file=sys.stderr)
        return 1
    if not issue.get("shopify_page_id"):
        print(f"[update] Issue {args.issue} has no shopify_page_id. Run publish_page.py first.", file=sys.stderr)
        return 2

    print(f"[update] rebuilding page HTML for issue {args.issue}...")
    body_html = build_page_html(issue)
    print(f"[update]   HTML length: {len(body_html):,} chars")

    print(f"[update] pushing pageUpdate to Shopify ({issue['shopify_page_id']})...")
    try:
        page_info = update_page(
            page_id=issue["shopify_page_id"],
            title=issue["page_title"],
            body_html=body_html,
            seo_title=issue.get("page_title"),
            seo_description=issue.get("preview_text"),
            image_file_id=issue.get("shopify_image_id"),
            template_suffix=args.template_suffix,
        )
    except Exception as e:
        print(f"[update] FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 3

    print(f"[update]   page handle: {page_info['handle']}")
    print(f"[update]   public URL:  {page_info['url']}")
    print(f"[update]   template:    {page_info.get('template_suffix')}")
    print(f"[update]   isPublished: {page_info['is_published']}")

    shop = os.environ.get("SHOPIFY_SHOP_DOMAIN", "trybeezybeez.myshopify.com")
    numeric = page_info["id"].rsplit("/", 1)[-1]
    admin_url = f"https://{shop}/admin/pages/{numeric}"

    if not args.no_slack:
        post_draft(
            title=f"Hive Mind Issue {args.issue} — Page body UPDATED in-place",
            summary_lines=[
                f"*Title:* {issue['page_title']}",
                f"*Handle:* {page_info['handle']}",
                f"*Visibility:* {'Visible' if page_info.get('is_published') else 'Hidden (still draft)'}",
                f"*Body length:* {len(body_html):,} chars",
                f"*Template:* {page_info.get('template_suffix') or '(default)'}",
            ],
            body=(
                f"*Admin edit URL:* {admin_url}\n"
                f"*Public URL:* {page_info['url']}\n\n"
                "Page body regenerated to match the existing Hive Mind template "
                "(byte-for-byte equivalent to Issue 14's layout: inline-styled, "
                "boxed callout, product banner, subscribe form). "
                "Eyeball in admin; flip to Visible on publish day."
            ),
            image_url=issue.get("shopify_image_url"),
            image_alt=f"The Hive Mind Issue {args.issue}",
        )
        print("[update] posted to Slack")

    print(f"\n[update] DONE. Admin: {admin_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
PYEOF
echo "[fix]   scripts/update_issue_page.py created"

echo "[fix] step 4/4 — syntax checks and update..."
python -c "import ast; ast.parse(open('workers/shopify_page_builder.py').read()); print('  page_builder.py OK')"
python -c "import ast; ast.parse(open('workers/shopify_publisher.py').read()); print('  publisher.py OK')"
python -c "import ast; ast.parse(open('scripts/update_issue_page.py').read()); print('  update_issue_page.py OK')"

echo ""
echo "[fix] running update for Issue 15..."
echo ""
python -m scripts.update_issue_page --issue 15
