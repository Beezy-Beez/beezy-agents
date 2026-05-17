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
    "h1":               "font-size:32px; font-weight:600; color:#2c2417; margin:0 0 12px 0; line-height:1.25; font-family:Georgia, serif;",
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
    """Apply inline markdown: [link](url), **bold**, *em*/_em_.
    Also auto-linkifies bare trybeezybeez.com mentions that aren't already inside
    anchor tags — defensive measure in case the writer forgot to use markdown link
    syntax. The hive-mind-newsletter skill's rule 7 requires real links, but this
    catches anything that slips through."""
    text = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        lambda m: f'<a href="{m.group(2)}" style="{STYLES["inline_link"]}">{m.group(1)}</a>',
        text,
    )
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<![*\w])\*([^*\s][^*]*?)\*(?![*\w])', r'<em>\1</em>', text)
    text = re.sub(r'(?<![_\w])_([^_\s][^_]*?)_(?![_\w])', r'<em>\1</em>', text)

    # Auto-linkify bare trybeezybeez.com mentions OUTSIDE existing <a> tags.
    # Split on existing anchor tags, process only the non-anchor segments.
    parts = re.split(r'(<a\b[^>]*>.*?</a>)', text, flags=re.IGNORECASE | re.DOTALL)
    domain_re = re.compile(r'\b(trybeezybeez\.com(?:/[A-Za-z0-9\-/_]+)?)\b')
    for i in range(len(parts)):
        if i % 2 == 0:
            parts[i] = domain_re.sub(
                lambda m: f'<a href="https://{m.group(1)}" style="{STYLES["inline_link"]}">{m.group(1)}</a>',
                parts[i],
            )
    return "".join(parts)


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


SUBSCRIBE_BOX_HTML = """<div id="hive-mind-pre-sub">
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
<h2 style="font-size:24px; color:#2c2417; margin:0 0 12px 0; font-family:Georgia, serif; font-weight:bold;">✓ You’re subscribed</h2>
<p style="font-size:18px; line-height:1.75; color:#5a4a3a; margin:0 0 25px 0; font-family:Georgia, serif;">Watch your inbox for the next issue. Meanwhile, you have full access to every issue we’ve ever sent.</p>
<a href="https://trybeezybeez.com/pages/the-hive-mind" style="display:inline-block; padding:14px 32px; font-size:16px; font-family:Georgia, serif; background-color:#8b4513; color:#fffdf7; text-decoration:none; border-radius:4px; font-weight:bold; letter-spacing:1px;">BROWSE THE ARCHIVE →</a>
</div>"""


SUBSCRIBE_SCRIPT = """<script>(function(){var SUB_KEY="bb_hivemind_sub";function showSubscribed(){var pre=document.getElementById("hive-mind-pre-sub");var post=document.getElementById("hive-mind-post-sub");if(pre)pre.style.display="none";if(post)post.style.display="block"}var params=new URLSearchParams(window.location.search);if(params.get("subscriber")==="true"||params.get("s")==="1"){try{localStorage.setItem(SUB_KEY,"true")}catch(_){}}try{if(localStorage.getItem(SUB_KEY)==="true")showSubscribed()}catch(_){}var form=document.getElementById("hive-mind-subscribe-form");if(form){form.addEventListener("submit",function(n){n.preventDefault();var t=document.getElementById("hive-mind-email").value;if(t){var e=this.querySelector("button");e.textContent="Subscribing...";e.disabled=!0;fetch("https://a.klaviyo.com/client/subscriptions/?company_id=W8SW8k",{method:"POST",headers:{"Content-Type":"application/json",revision:"2024-10-15"},body:JSON.stringify({data:{type:"subscription",attributes:{custom_source:"Hive Mind Issue Page",profile:{data:{type:"profile",attributes:{email:t}}}},relationships:{list:{data:{type:"list",id:"Y6VSre"}}}}})}).then(function(i){if(i.ok||i.status===202){try{localStorage.setItem(SUB_KEY,"true")}catch(_){}showSubscribed()}else{document.getElementById("hive-mind-error").style.display="block";e.textContent="Subscribe";e.disabled=!1}}).catch(function(){document.getElementById("hive-mind-error").style.display="block";e.textContent="Subscribe";e.disabled=!1})}})}})();</script>"""


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

    # Use div instead of h1 to avoid Shopify theme CSS overriding inline styles and
    # cascading bold/large rendering into body paragraphs.
    parts.append(f'<div role="heading" aria-level="1" style="{STYLES["h1"]}">{page_title}</div>')

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

    # Subscribe box (two-state: pre-sub form + post-sub archive CTA)
    parts.append(f'<div style="{STYLES["sub_box"]}">')
    parts.append(SUBSCRIBE_BOX_HTML)
    parts.append("</div>")

    # Subscribe handler script (toggles via bb_hivemind_sub localStorage flag)
    parts.append(SUBSCRIBE_SCRIPT)

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
