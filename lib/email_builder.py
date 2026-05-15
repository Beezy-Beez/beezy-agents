"""
Email HTML builder for Hive Mind issues.

Structure matches the Issue 014 reference template exactly:
  - CSS .hidden-preheader class (Klaviyo-safe)
  - First paragraph of email_teaser_body → H1 narrative hook (extracted, not repeated in body)
  - Read-time: "🌙 A X-minute read — written for tonight."
  - Cover image: full width, zero side padding
  - Remaining teaser paragraphs as body (short — 4 paras + pullquote + cliffhanger)
  - Sign-off: two lines ("See you on the page." / "— The Hive Mind")
  - CTA: uppercase "Continue Reading →", 18px 42px, font-size 18px
  - Below-CTA: "The full story takes X minutes. We left the rest on the page."
  - Editorial library: "→ Sleep Science Hub · → Morning Wellness Hub" (single line)
  - About blurb: exact reference copy
  - Footer: address + {% unsubscribe 'Unsubscribe from The Hive Mind' %}

H1 sourcing:
  The FIRST paragraph of email_teaser_body is extracted as the H1 narrative hook
  and removed from the body flow. Write it as the opening scene-setting sentence.
  Example: "In 1966, a young researcher at Yale was about to disprove a century
  of medical advice."

Read-time:
  Computed from long_form_body word count (falls back to email_teaser_body).
  Displayed as a static label — not computed from the teaser.

CANONICAL SPEC: /mnt/skills/user/hive-mind-page-template/SKILL.md
"""
from __future__ import annotations

import math
import re

SHOPIFY_DOMAIN   = "https://trybeezybeez.com"
SLEEP_HUB_URL    = f"{SHOPIFY_DOMAIN}/pages/sleep-science-hub"
WELLNESS_HUB_URL = f"{SHOPIFY_DOMAIN}/pages/morning-wellness-hub"
PRODUCT_URL      = f"{SHOPIFY_DOMAIN}/products/honey-sub"
FROM_EMAIL       = "help@trybeezybeez.com"

_WPM = 200


def _read_time_minutes(text: str) -> int:
    return max(1, math.ceil(len(text.split()) / _WPM))


def _inline_format(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*]+?)\*", r"<em>\1</em>", text)
    return text


def _split_hook_and_body(body_md: str) -> tuple[str, str]:
    """
    Split email_teaser_body into (h1_hook, remaining_body_md).
    The first non-empty paragraph becomes the H1; the rest is the body.
    Strips any CTA marker lines.
    """
    paras = [p.strip() for p in body_md.strip().split("\n\n") if p.strip()]
    # Drop CTA marker lines
    paras = [p for p in paras if not (
        re.match(r"^\*?\*?Continue reading", p, re.IGNORECASE) or
        "Continue reading on the page" in p
    )]
    if not paras:
        return "", ""
    hook = _inline_format(paras[0].replace("\n", " "))
    # Cap at 3 body paragraphs — email should tease, not tell the whole story.
    remaining = "\n\n".join(paras[1:4])
    return hook, remaining


def _build_body_html(body_md: str) -> str:
    """Convert body markdown to email-safe HTML paragraphs."""
    parts = []
    for para in body_md.strip().split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if re.match(r"^\*?\*?Continue reading", para, re.IGNORECASE) or \
           "Continue reading on the page" in para:
            continue
        if para.startswith("## "):
            text = _inline_format(para[3:].strip())
            parts.append(
                f'<p style="margin:0 0 22px 0; font-size:22px; line-height:1.5; '
                f'color:#2c2417; font-family:Georgia, serif; font-weight:bold;">{text}</p>'
            )
        elif para.startswith("> "):
            text = _inline_format(para[2:].strip())
            parts.append(
                f'<p style="margin:0 0 36px 0; font-size:22px; line-height:1.5; '
                f'color:#2c2417; font-family:Georgia, serif; font-style:italic;">{text}</p>'
            )
        else:
            text = _inline_format(para.replace("\n", " "))
            parts.append(
                f'<p style="margin:0 0 22px 0; font-size:18px; line-height:1.75; '
                f'color:#2c2417; font-family:Georgia, serif;">{text}</p>'
            )
    return "\n".join(parts)


def build_email_html(issue: dict, shopify_domain: str = SHOPIFY_DOMAIN) -> str:
    """
    Build full email HTML for a Hive Mind issue, matching the Issue 014 reference.

    Required keys:
        number, subject_line, page_slug, email_teaser_body
    Optional keys:
        preview_text      — hidden preheader (falls back to subject_line)
        cover_image_url   — full-width cover image
        long_form_body    — for accurate read-time (falls back to email_teaser_body)
        page_dek          — unused; H1 comes from first para of email_teaser_body
    """
    issue_num   = int(issue.get("number") or 0)
    subject     = (issue.get("subject_line") or "").strip()
    page_slug   = (issue.get("page_slug") or "").strip()
    body_md     = (issue.get("email_teaser_body") or "").strip()
    preview_txt = (issue.get("preview_text") or subject).strip()
    cover_url   = (issue.get("cover_image_url") or "").strip()
    full_body   = (issue.get("long_form_body") or body_md).strip()

    h1_hook, rest    = _split_hook_and_body(body_md)
    teaser_read_mins = _read_time_minutes(rest)
    full_read_mins   = _read_time_minutes(full_body)
    body_html        = _build_body_html(rest)
    issue_label      = f"The Hive Mind · Issue {issue_num:03d}"

    cta_url = (
        f"{shopify_domain}/pages/{page_slug}"
        f"?s=1"
        f"&utm_source=klaviyo"
        f"&utm_medium=email"
        f"&utm_campaign=hive-mind-{issue_num:03d}"
        f"&utm_content=teaser"
    )

    cover_block = ""
    if cover_url:
        cover_block = f"""<tr>
<td style="padding: 0 0 28px 0;">
<img alt="{issue_label}" src="{cover_url}" style="width:100%; max-width:600px; height:auto; display:block;" width="600"/>
</td>
</tr>"""

    unsub = "{%" + " unsubscribe 'Unsubscribe from The Hive Mind' " + "%}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1" name="viewport"/>
<title>{issue_label}</title>
<style>
body {{margin:0;padding:0;background:#faf6ee;font-family:Georgia,"Times New Roman",serif;color:#2c2417;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%}}
table {{border-collapse:collapse}}
img {{display:block;border:0;outline:none;text-decoration:none;-ms-interpolation-mode:bicubic}}
a {{color:#8b4513;text-decoration:underline}}
.hidden-preheader {{display:none !important;visibility:hidden;mso-hide:all;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden}}
@media screen and (max-width:600px) {{
  .container {{width:100% !important;max-width:100% !important}}
  .px-mobile {{padding-left:22px !important;padding-right:22px !important}}
  h1.headline {{font-size:28px !important;line-height:1.2 !important}}
  .cta-button {{font-size:17px !important;padding:18px 32px !important}}
}}
</style>
</head>
<body>
<div class="hidden-preheader">{preview_txt}</div>
<table align="center" border="0" cellpadding="0" cellspacing="0" role="presentation" style="background:#faf6ee;" width="100%">
<tr>
<td align="center" style="padding: 32px 12px 48px 12px;">
<table border="0" cellpadding="0" cellspacing="0" class="container" role="presentation" style="width:600px; max-width:600px; background:#fffdf7; border-radius:8px;" width="600">
<tr>
<td class="px-mobile" style="padding: 40px 40px 0 40px;">
<p style="margin:0; font-size:14px; letter-spacing:1.5px; text-transform:uppercase; color:#8b7355; font-family:Georgia, serif;">{issue_label}</p>
</td>
</tr>
<tr>
<td class="px-mobile" style="padding: 14px 40px 8px 40px;">
<h1 class="headline" style="margin:0; font-size:34px; line-height:1.18; font-weight:bold; color:#2c2417; font-family:Georgia, serif;">{h1_hook}</h1>
</td>
</tr>
<tr>
<td class="px-mobile" style="padding: 12px 40px 28px 40px;">
<p style="margin:0; font-size:16px; color:#8b7355; font-family:Georgia, serif; font-style:italic;">&#127769; A {teaser_read_mins}-minute read &mdash; written for tonight.</p>
</td>
</tr>
{cover_block}
<tr>
<td class="px-mobile" style="padding: 0 40px;">
{body_html}
</td>
</tr>
<tr>
<td align="center" class="px-mobile" style="padding: 0 40px 12px 40px;">
<table border="0" cellpadding="0" cellspacing="0" role="presentation">
<tr>
<td align="center" bgcolor="#8b4513" style="border-radius:4px;">
<a class="cta-button" href="{cta_url}" style="display:inline-block; padding:18px 42px; font-family:Georgia, serif; font-size:18px; font-weight:bold; letter-spacing:1px; color:#fffdf7; background:#8b4513; text-decoration:none; border-radius:4px; text-transform:uppercase;" target="_blank">Continue Reading &rarr;</a>
</td>
</tr>
</table>
</td>
</tr>
<tr>
<td align="center" class="px-mobile" style="padding: 14px 40px 36px 40px;">
<p style="margin:0; font-size:16px; color:#8b7355; font-family:Georgia, serif; font-style:italic;">The full story takes {full_read_mins} minutes. We left the rest on the page.</p>
</td>
</tr>
<tr>
<td class="px-mobile" style="padding: 0 40px 36px 40px;">
<p style="margin:0 0 6px 0; font-size:18px; line-height:1.75; color:#2c2417; font-family:Georgia, serif;">See you on the page.</p>
<p style="margin:0; font-size:18px; line-height:1.75; color:#2c2417; font-family:Georgia, serif;">&mdash; <em>The Hive Mind</em></p>
</td>
</tr>
<tr>
<td class="px-mobile" style="padding: 0 40px;">
<hr style="border:none; border-top:1px solid #e8dcc8; margin:0;"/>
</td>
</tr>
<tr>
<td class="px-mobile" style="padding: 28px 40px 24px 40px;">
<p style="margin:0 0 12px 0; font-size:14px; letter-spacing:1.5px; text-transform:uppercase; color:#8b7355; font-family:Georgia, serif;">Explore our editorial library</p>
<p style="margin:0 0 8px 0; font-size:16px; line-height:1.75; color:#2c2417; font-family:Georgia, serif;">
<a href="{SLEEP_HUB_URL}" style="color:#8b4513; text-decoration:none; font-weight:bold;">&rarr; Sleep Science Hub</a><span style="color:#8b7355;"> &middot; </span><a href="{WELLNESS_HUB_URL}" style="color:#8b4513; text-decoration:none; font-weight:bold;">&rarr; Morning Wellness Hub</a>
</p>
</td>
</tr>
<tr>
<td class="px-mobile" style="padding: 4px 40px 36px 40px;">
<p style="margin:0; font-size:16px; line-height:1.75; color:#5a4a3a; font-family:Georgia, serif;">
<strong>Beezy Beez</strong> crafts <a href="{PRODUCT_URL}" style="color:#8b4513; text-decoration:none;">botanical extract honey</a> for people navigating sleep changes after 50. The Hive Mind is our editorial letter on the science of rest.
</p>
</td>
</tr>
</table>
<table border="0" cellpadding="0" cellspacing="0" role="presentation" style="width:600px; max-width:600px;" width="600">
<tr>
<td align="center" class="px-mobile" style="padding: 24px 40px 8px 40px;">
<p style="margin:0; font-size:14px; line-height:1.6; color:#8b7355; font-family:Georgia, serif;">
Beezy Beez Honey &middot; {FROM_EMAIL}<br/><a href="{shopify_domain}" style="color:#8b7355;">{shopify_domain.replace("https://", "")}</a>
</p>
</td>
</tr>
<tr>
<td align="center" style="padding: 8px 40px 24px 40px;">
<p style="margin:0; font-size:12px; line-height:1.6; color:#a89880; font-family:Georgia, serif;">{unsub}</p>
</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>"""
