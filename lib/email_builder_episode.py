"""
Email HTML builder for sleep audio episodes.

Produces two variants from the same metadata:
  Email A — Engaged Customers (discovery tone, excl. Active Seal)
  Email B — Active Seal members (exclusive/members-only tone)

Returns (email_a_html, email_b_html) as a tuple.

Metadata keys used:
  title, episode_type, duration_minutes, cover_image_url,
  shopify_page_url, buzzsprout_url
"""
from __future__ import annotations

SHOPIFY_DOMAIN = "https://trybeezybeez.com"
PRODUCT_URL    = f"{SHOPIFY_DOMAIN}/pages/bf-collection"
FROM_EMAIL     = "help@trybeezybeez.com"

_EPISODE_LABELS = {
    "sleep_story":            "Sleep Story",
    "guided_meditation":      "Guided Meditation",
    "affirmation_meditation": "Affirmation Meditation",
    "morning_meditation":     "Morning Meditation",
    "soundscape":             "Sleep Soundscape",
}

_EPISODE_HUB = {
    "sleep_story":            ("Sleep Science Hub",    f"{SHOPIFY_DOMAIN}/pages/sleep-science-hub"),
    "soundscape":             ("Sleep Science Hub",    f"{SHOPIFY_DOMAIN}/pages/sleep-science-hub"),
    "guided_meditation":      ("Meditation Library",   f"{SHOPIFY_DOMAIN}/pages/meditation-library"),
    "affirmation_meditation": ("Meditation Library",   f"{SHOPIFY_DOMAIN}/pages/meditation-library"),
    "morning_meditation":     ("Morning Wellness Hub", f"{SHOPIFY_DOMAIN}/pages/morning-wellness-hub"),
}

# Short hook copy keyed by episode_type — discovery tone (Email A)
_HOOK_A = {
    "sleep_story": (
        "Tonight we added a new sleep story to the library. "
        "Put on headphones, close your eyes, and let it carry you."
    ),
    "soundscape": (
        "A new soundscape is ready — crafted to mask the noise that keeps you awake "
        "and settle your nervous system before sleep."
    ),
    "guided_meditation": (
        "A new guided meditation is waiting for you. "
        "Twenty minutes of intentional stillness, built for the moments when your mind won't quiet down."
    ),
    "affirmation_meditation": (
        "We've added a new affirmation meditation to the library — "
        "designed to replace the mental loop with something steadier."
    ),
    "morning_meditation": (
        "Start tomorrow differently. A new morning meditation is ready — "
        "grounding, brief, and designed to set the tone before the day takes over."
    ),
}

# Short hook copy keyed by episode_type — members-only tone (Email B)
_HOOK_B = {
    "sleep_story": (
        "Your Hive Club library just grew. A new sleep story is live — "
        "available first to active members, before the wider send."
    ),
    "soundscape": (
        "Members only — a new soundscape dropped tonight. "
        "Engineered layered audio to ease you across the threshold into sleep."
    ),
    "guided_meditation": (
        "New for Hive Club: a guided meditation session is live in your library. "
        "Members get first access before the general send."
    ),
    "affirmation_meditation": (
        "A new affirmation meditation is live for Hive Club members. "
        "Your library grows with every episode — this one's available to you first."
    ),
    "morning_meditation": (
        "Your Hive Club morning routine just got an addition. "
        "New meditation — available to members now, general release tomorrow."
    ),
}

_CSS = """
body{margin:0;padding:0;background:#faf6ee;font-family:Georgia,"Times New Roman",serif;
color:#2c2417;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%}
table{border-collapse:collapse}
img{display:block;border:0;outline:none;text-decoration:none;-ms-interpolation-mode:bicubic}
a{color:#8b4513;text-decoration:underline}
.hidden-preheader{display:none !important;visibility:hidden;mso-hide:all;font-size:1px;
line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden}
@media screen and (max-width:600px){
  .container{width:100% !important;max-width:100% !important}
  .px-mobile{padding-left:22px !important;padding-right:22px !important}
  h1.headline{font-size:26px !important;line-height:1.2 !important}
  .cta-button{font-size:17px !important;padding:18px 32px !important}
}
"""


def _cover_block(img_url: str, label: str) -> str:
    if not img_url:
        return ""
    return (
        f'<tr><td style="padding:0 0 28px 0;">'
        f'<img alt="{label}" src="{img_url}" '
        f'style="width:100%;max-width:600px;height:auto;display:block;" width="600"/>'
        f'</td></tr>'
    )


def _build_html(
    *,
    title: str,
    episode_type: str,
    duration_minutes: int | None,
    cover_url: str,
    page_url: str,
    hook_text: str,
    signoff_line1: str,
    signoff_line2: str,
    preheader: str,
) -> str:
    label      = _EPISODE_LABELS.get(episode_type, episode_type.replace("_", " ").title())
    hub_name, hub_url = _EPISODE_HUB.get(episode_type, ("Sleep Science Hub", f"{SHOPIFY_DOMAIN}/pages/sleep-science-hub"))

    meta_parts = [label]
    if duration_minutes:
        meta_parts.append(f"{duration_minutes} min")
    meta_line = " · ".join(meta_parts)

    cover   = _cover_block(cover_url, label)
    unsub   = "{%" + " unsubscribe %}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1" name="viewport"/>
<title>{title}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="hidden-preheader">{preheader}</div>
<table align="center" border="0" cellpadding="0" cellspacing="0" role="presentation" style="background:#faf6ee;" width="100%">
<tr>
<td align="center" style="padding:32px 12px 48px 12px;">
<table border="0" cellpadding="0" cellspacing="0" class="container" role="presentation" style="width:600px;max-width:600px;background:#fffdf7;border-radius:8px;" width="600">

<tr>
<td class="px-mobile" style="padding:40px 40px 0 40px;">
<p style="margin:0;font-size:13px;letter-spacing:1.5px;text-transform:uppercase;color:#8b7355;font-family:Georgia,serif;">{meta_line}</p>
</td>
</tr>

<tr>
<td class="px-mobile" style="padding:14px 40px 28px 40px;">
<h1 class="headline" style="margin:0;font-size:30px;line-height:1.2;font-weight:bold;color:#2c2417;font-family:Georgia,serif;">{title}</h1>
</td>
</tr>

{cover}

<tr>
<td class="px-mobile" style="padding:0 40px 28px 40px;">
<p style="margin:0;font-size:18px;line-height:1.75;color:#2c2417;font-family:Georgia,serif;">{hook_text}</p>
</td>
</tr>

<tr>
<td align="center" class="px-mobile" style="padding:0 40px 12px 40px;">
<table border="0" cellpadding="0" cellspacing="0" role="presentation">
<tr>
<td align="center" bgcolor="#8b4513" style="border-radius:4px;">
<a class="cta-button" href="{page_url}" style="display:inline-block;padding:18px 42px;font-family:Georgia,serif;font-size:18px;font-weight:bold;letter-spacing:1px;color:#fffdf7;background:#8b4513;text-decoration:none;border-radius:4px;text-transform:uppercase;" target="_blank">Listen Now &rarr;</a>
</td>
</tr>
</table>
</td>
</tr>

<tr>
<td class="px-mobile" style="padding:28px 40px 36px 40px;">
<p style="margin:0 0 6px 0;font-size:18px;line-height:1.75;color:#2c2417;font-family:Georgia,serif;">{signoff_line1}</p>
<p style="margin:0;font-size:18px;line-height:1.75;color:#2c2417;font-family:Georgia,serif;">{signoff_line2}</p>
</td>
</tr>

<tr>
<td class="px-mobile" style="padding:0 40px;">
<hr style="border:none;border-top:1px solid #e8dcc8;margin:0;"/>
</td>
</tr>

<tr>
<td class="px-mobile" style="padding:28px 40px 24px 40px;">
<p style="margin:0 0 8px 0;font-size:14px;letter-spacing:1.5px;text-transform:uppercase;color:#8b7355;font-family:Georgia,serif;">Our audio library</p>
<p style="margin:0;font-size:16px;line-height:1.75;color:#2c2417;font-family:Georgia,serif;">
<a href="{hub_url}" style="color:#8b4513;text-decoration:none;font-weight:bold;">&rarr; {hub_name}</a>
</p>
</td>
</tr>

<tr>
<td class="px-mobile" style="padding:4px 40px 36px 40px;">
<p style="margin:0;font-size:16px;line-height:1.75;color:#5a4a3a;font-family:Georgia,serif;">
<strong>Beezy Beez</strong> crafts <a href="{PRODUCT_URL}" style="color:#8b4513;text-decoration:none;">botanical extract honey</a> for people navigating sleep changes after 50. Our audio library is made for the same moments — when rest doesn't come easily.
</p>
</td>
</tr>

</table>
<table border="0" cellpadding="0" cellspacing="0" role="presentation" style="width:600px;max-width:600px;" width="600">
<tr>
<td align="center" class="px-mobile" style="padding:24px 40px 8px 40px;">
<p style="margin:0;font-size:14px;line-height:1.6;color:#8b7355;font-family:Georgia,serif;">
Beezy Beez Honey &middot; {FROM_EMAIL}<br/>
<a href="{SHOPIFY_DOMAIN}" style="color:#8b7355;">{SHOPIFY_DOMAIN.replace("https://", "")}</a>
</p>
</td>
</tr>
<tr>
<td align="center" style="padding:8px 40px 24px 40px;">
<p style="margin:0;font-size:12px;line-height:1.6;color:#a89880;font-family:Georgia,serif;">{unsub}</p>
</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>"""


def build_episode_emails(metadata: dict, page_url: str) -> tuple[str, str]:
    """
    Build Email A (Engaged Customers) and Email B (Active Seal) HTML for a sleep audio episode.

    Returns (email_a_html, email_b_html).
    """
    title        = (metadata.get("title") or "New Episode").strip()
    episode_type = metadata.get("episode_type") or "sleep_story"
    duration     = metadata.get("duration_minutes")
    cover_url    = (
        metadata.get("cover_image_url")
        or metadata.get("thumbnail_url")
        or metadata.get("image_url")
        or ""
    ).strip()
    url          = (page_url or metadata.get("shopify_page_url") or metadata.get("buzzsprout_url") or "#").strip()

    label = _EPISODE_LABELS.get(episode_type, episode_type.replace("_", " ").title())

    hook_a = _HOOK_A.get(episode_type, _HOOK_A["sleep_story"])
    hook_b = _HOOK_B.get(episode_type, _HOOK_B["sleep_story"])

    dur_str = f" ({duration} min)" if duration else ""

    email_a = _build_html(
        title=title,
        episode_type=episode_type,
        duration_minutes=duration,
        cover_url=cover_url,
        page_url=url,
        hook_text=hook_a,
        signoff_line1="Sweet dreams,",
        signoff_line2="&mdash; <em>Beezy Beez</em>",
        preheader=f"New {label.lower()}: {title}{dur_str} — tap to listen.",
    )

    email_b = _build_html(
        title=title,
        episode_type=episode_type,
        duration_minutes=duration,
        cover_url=cover_url,
        page_url=url,
        hook_text=hook_b,
        signoff_line1="For Hive Club members,",
        signoff_line2="&mdash; <em>Beezy Beez</em>",
        preheader=f"Members first — {title}{dur_str} is live in your library.",
    )

    return email_a, email_b
