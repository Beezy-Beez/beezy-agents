"""
Email HTML builder for sleep audio episodes.

Structure copied exactly from the Bridge of Incidents live campaigns:
  YfBXg2 — Active Seal (members-first tone, "tonight's story is up")
  RQrY6N — Engaged Customers (discovery tone, "tonight, a sleep story")

Returns (email_a_html, email_b_html) where:
  email_a → Engaged Customers excl Active Seal
  email_b → Active Seal
"""
from __future__ import annotations

_LOGO_URL = (
    "https://cdn05.zipify.com/SgNn6ZTj7JLGAGruNGAZ7U0KjAY=/"
    "fit-in/3840x0/a9688067ccf748cc883f028b3e876c98/beezy-beez-logo.webp"
)

_CSS = """body, table, td, p, a {
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale
    }
body {
    margin: 0 !important;
    padding: 0 !important;
    width: 100% !important;
    background: #faf6ee
    }
table {
    border-collapse: collapse;
    mso-table-lspace: 0;
    mso-table-rspace: 0
    }
img {
    border: 0;
    outline: none;
    text-decoration: none;
    -ms-interpolation-mode: bicubic
    }
a {
    text-decoration: none
    }
.container {
    width: 600px;
    max-width: 100%;
    margin: 0 auto;
    background: #faf6ee
    }
h1 {
    font-family: "Cormorant Garamond", Georgia, serif;
    font-size: 36px;
    line-height: 1.15;
    font-weight: 500;
    font-style: italic;
    color: #87401C;
    margin: 0
    }
p {
    font-family: Lato, Helvetica, Arial, sans-serif;
    font-size: 17px;
    line-height: 1.6;
    color: #2a1f15;
    margin: 0 0 18px 0
    }
.eyebrow {
    font-family: Lato, Helvetica, Arial, sans-serif;
    font-size: 12px;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: #6b5947;
    font-weight: 600
    }
.btn {
    display: inline-block;
    background: #87401C;
    color: #fff !important;
    padding: 14px 32px;
    font-family: Lato, Helvetica, Arial, sans-serif;
    font-size: 15px;
    font-weight: 600;
    letter-spacing: 0.5px;
    border-radius: 2px;
    text-decoration: none
    }
@media screen and (max-width: 600px) {
    .container {
        width: 100% !important
        }
    .px {
        padding-left: 20px !important;
        padding-right: 20px !important
        }
    h1 {
        font-size: 28px !important
        }
    p {
        font-size: 16px !important
        }
    }"""

# ── Per-type copy ─────────────────────────────────────────────────────────────

_H1_A = {
    "sleep_story":            "tonight, a sleep story",
    "soundscape":             "something new for tonight",
    "guided_meditation":      "tonight, a short meditation",
    "affirmation_meditation": "something to carry into sleep",
    "morning_meditation":     "start tomorrow differently",
}

_H1_B = {
    "sleep_story":            "tonight's story is up",
    "soundscape":             "tonight's soundscape is ready",
    "guided_meditation":      "your new meditation is live",
    "affirmation_meditation": "a new affirmation session",
    "morning_meditation":     "your morning session is live",
}

# Body p1 — context/intro (Email A, discovery tone)
_INTRO_A = {
    "sleep_story": (
        "We don't talk about the Sleep Better Podcast much. It's our quieter project"
        " — slow stories paced for breath, narrated softly, designed to take you under."
    ),
    "soundscape": (
        "The Sleep Better library just got a new soundscape — engineered layered audio"
        " to ease you across the threshold into sleep."
    ),
    "guided_meditation": (
        "The Sleep Better library has a new guided meditation waiting for you."
        " Intentional stillness, built for the moments when your mind won't quiet down."
    ),
    "affirmation_meditation": (
        "We've added a new affirmation meditation to the Sleep Better library"
        " — designed to replace the mental loop with something steadier."
    ),
    "morning_meditation": (
        "A new morning session just landed in the Sleep Better library."
        " Grounding, brief, designed to set the tone before the day takes over."
    ),
}

# Body p3 — CTA hook (Email A)
_CTA_A = {
    "sleep_story":            "If tonight's the night you can't quite settle, press play.",
    "soundscape":             "Press play, close your eyes, and let the sound do the rest.",
    "guided_meditation":      "If your mind is still running tonight, this is where to go.",
    "affirmation_meditation": "Press play when you're in bed. Let it replace the loop.",
    "morning_meditation":     "Set it up now. Play it when you wake up.",
}

# Body p2 — member context (Email B, Active Seal)
_MEMBER_B = {
    "sleep_story": (
        "As an Active Seal member you get every episode the moment it lands."
        " Tonight that's this one."
    ),
    "soundscape":             "Available to Active Seal members first. Press play.",
    "guided_meditation":      "As an Active Seal member you get this the moment it lands.",
    "affirmation_meditation": "As an Active Seal member you get this the moment it lands.",
    "morning_meditation":     "Available to you now, before the general send.",
}

# Body p3 — CTA hook (Email B)
_CTA_B = {
    "sleep_story":            "Press play, lay back, and let it carry you under.",
    "soundscape":             "Your library, your night.",
    "guided_meditation":      "When you're ready, press play.",
    "affirmation_meditation": "Let it carry you the rest of the way.",
    "morning_meditation":     "Start tomorrow right.",
}

_SUBTITLE_A = {
    "sleep_story":
        "Sleep Better Podcast · A slow walk into sleep · ~{dur} min",
    "soundscape":
        "Sleep Better Podcast · Sleep soundscape · ~{dur} min",
    "guided_meditation":
        "Sleep Better Podcast · Guided meditation · ~{dur} min",
    "affirmation_meditation":
        "Sleep Better Podcast · Affirmation session · ~{dur} min",
    "morning_meditation":
        "Sleep Better Podcast · Morning session · ~{dur} min",
}

_SUBTITLE_B = {
    "sleep_story":
        "Sleep Better Podcast · Tonight's new story · ~{dur} min",
    "soundscape":
        "Sleep Better Podcast · New soundscape · ~{dur} min",
    "guided_meditation":
        "Sleep Better Podcast · Member exclusive · ~{dur} min",
    "affirmation_meditation":
        "Sleep Better Podcast · Active Seal exclusive · ~{dur} min",
    "morning_meditation":
        "Sleep Better Podcast · Member exclusive · ~{dur} min",
}

_CTA_LABEL_A = {
    "sleep_story":            "Play tonight's story",
    "soundscape":             "Play tonight's soundscape",
    "guided_meditation":      "Open tonight's meditation",
    "affirmation_meditation": "Play the affirmation",
    "morning_meditation":     "Open the morning session",
}

_CTA_LABEL_B = {
    "sleep_story":            "Play tonight's story",
    "soundscape":             "Play the soundscape",
    "guided_meditation":      "Play your meditation",
    "affirmation_meditation": "Play the affirmation",
    "morning_meditation":     "Play your morning session",
}


def _render(
    *,
    preview_text: str,
    eyebrow: str,
    h1: str,
    body_p1: str,
    body_p2: str,
    body_p3: str,
    hero_url: str,
    hero_alt: str,
    page_url: str,
    card_title: str,
    card_subtitle: str,
    cta_label: str,
    footer_text: str,
) -> str:
    hero_row = ""
    if hero_url:
        hero_row = (
            '<tr><td class="px" style="padding: 24px 40px 24px 40px;">'
            '<a href="' + page_url + '" style="text-decoration: none; border: 0;">'
            '<img alt="' + hero_alt + '" src="' + hero_url + '" '
            'style="display: block; width: 100%; max-width: 520px; height: auto; '
            'border-radius: 4px; border: 0;" width="520"/>'
            "</a></td></tr>\n"
        )

    lines = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8"/>',
        '<meta content="width=device-width, initial-scale=1.0" name="viewport"/>',
        '<meta content="IE=edge" http-equiv="X-UA-Compatible"/>',
        "<title>" + h1 + "</title>",
        "<style>" + _CSS + "</style></head>",
        "<body>",
        # Preview text
        '<div style="display: none; max-height: 0; overflow: hidden; mso-hide: all; '
        'font-size: 1px; color: #faf6ee; line-height: 1px;">' + preview_text + "</div>",
        # Outer table
        '<table border="0" cellpadding="0" cellspacing="0" role="presentation" '
        'style="background: #faf6ee;" width="100%">',
        "<tr><td align=\"center\">",
        '<table border="0" cellpadding="0" cellspacing="0" class="container" '
        'role="presentation" style="width: 600px; max-width: 100%;" width="600">',
        # Logo
        '<tr><td class="px" style="padding: 36px 40px 24px 40px; text-align: left;">'
        '<img alt="Beezy Beez" src="' + _LOGO_URL + '" '
        'style="display: block; height: auto;" width="120"/></td></tr>',
        # Eyebrow
        '<tr><td class="px" style="padding: 0 40px 8px 40px;">'
        '<p class="eyebrow" style="margin: 0;">' + eyebrow + "</p></td></tr>",
        # H1
        '<tr><td class="px" style="padding: 0 40px 24px 40px;"><h1>' + h1 + "</h1></td></tr>",
        # Body
        '<tr><td class="px" style="padding: 0 40px 0 40px;">',
        "<p>Hi {{ person.first_name|default:'there' }},</p>",
        "<p>" + body_p1 + "</p>",
        "<p>" + body_p2 + "</p>",
        "<p>" + body_p3 + "</p>",
        "</td></tr>",
        # Hero image
        hero_row,
        # Episode card
        '<tr><td class="px" style="padding: 0 40px 24px 40px;">',
        '<table border="0" cellpadding="0" cellspacing="0" role="presentation" '
        'style="background: #f3ead7; border: 1px solid #e8dfd0; border-radius: 4px;" width="100%">',
        '<tr><td style="padding: 28px 24px 28px 24px; text-align: center;">',
        '<p style="font-family: \'Cormorant Garamond\', Georgia, serif; font-size: 24px; '
        'font-style: italic; color: #2a1f15; margin: 0 0 6px 0;">' + card_title + "</p>",
        '<p style="font-family: Lato, Helvetica, Arial, sans-serif; font-size: 14px; '
        'color: #6b5947; margin: 0 0 20px 0;">' + card_subtitle + "</p>",
        '<a class="btn" href="' + page_url + '" style="display: inline-block; '
        'background: #87401C; color: #ffffff; padding: 14px 32px; '
        'font-family: Lato, Helvetica, Arial, sans-serif; font-size: 15px; '
        'font-weight: 600; letter-spacing: 0.5px; border-radius: 2px; '
        'text-decoration: none;">' + cta_label + "</a>",
        "</td></tr>",
        "</table>",
        "</td></tr>",
        # Sign-off
        '<tr><td class="px" style="padding: 8px 40px 0 40px;">'
        "<p>Sleep well.</p>"
        '<p style="margin-bottom: 24px;">— Alan</p></td></tr>',
        # Divider
        '<tr><td class="px" style="padding: 0 40px;">'
        '<div style="background: #e8dfd0; height: 1px; line-height: 1px; font-size: 1px;"> </div>'
        "</td></tr>",
        # Footer
        '<tr><td class="px" style="padding: 24px 40px 40px 40px;">'
        '<p style="font-family: Lato, Helvetica, Arial, sans-serif; font-size: 12px; '
        "color: #6b5947; line-height: 1.5; margin: 0;\">" + footer_text + "<br/>"
        "{% unsubscribe 'Unsubscribe' %}  ·  "
        '<a href="https://trybeezybeez.com" style="color: #87401C; '
        'text-decoration: underline;">trybeezybeez.com</a></p></td></tr>',
        "</table>",
        "</td></tr>",
        "</table>",
        "</body>",
        "</html>",
    ]
    return "\n".join(lines)


def build_episode_emails(metadata: dict, page_url: str) -> tuple[str, str]:
    """Build Email A (Engaged Customers) and Email B (Active Seal).

    Structure matches Bridge of Incidents live campaigns exactly.
    Returns (email_a_html, email_b_html).
    """
    title      = metadata.get("title", "")
    ep_type    = metadata.get("episode_type", "sleep_story")
    duration   = int(metadata.get("duration_minutes") or 25)
    desc_short = (metadata.get("description_short") or "").strip()
    cover_url  = (
        metadata.get("cover_image_url") or metadata.get("hero_image_url") or ""
    ).strip()

    # Body p2 — episode description (same structure as the live templates)
    ep_desc_a = (
        "Tonight there's a new one: <em>" + title + "</em>. Roughly "
        + str(duration) + " minutes. " + desc_short
    )
    if ep_type == "sleep_story":
        ep_desc_b = (
            "The new Sleep Better episode dropped tonight: <em>" + title + "</em>."
            " Roughly " + str(duration) + " minutes, slow narration, the kind of story"
            " that takes you somewhere quiet and leaves you there."
        )
    else:
        ep_desc_b = (
            "The new episode is live: <em>" + title + "</em>. " + desc_short
        )

    subtitle_a = _SUBTITLE_A.get(
        ep_type, "Sleep Better Podcast · ~{dur} min"
    ).format(dur=duration)
    subtitle_b = _SUBTITLE_B.get(
        ep_type, "Sleep Better Podcast · ~{dur} min"
    ).format(dur=duration)

    email_a = _render(
        preview_text=title + " — a slow walk into sleep. We're sharing this one.",
        eyebrow="A note from Alan",
        h1=_H1_A.get(ep_type, "tonight, a sleep story"),
        body_p1=_INTRO_A.get(ep_type, _INTRO_A["sleep_story"]),
        body_p2=ep_desc_a,
        body_p3=_CTA_A.get(ep_type, _CTA_A["sleep_story"]),
        hero_url=cover_url,
        hero_alt=title,
        page_url=page_url,
        card_title=title,
        card_subtitle=subtitle_a,
        cta_label=_CTA_LABEL_A.get(ep_type, "Play tonight's story"),
        footer_text=(
            "You're receiving this because you're part of the Beezy Beez community."
        ),
    )

    email_b = _render(
        preview_text=title + " — your new story tonight. Tap to start drifting.",
        eyebrow="A note from Alan · Active Seal members",
        h1=_H1_B.get(ep_type, "tonight's story is up"),
        body_p1=ep_desc_b,
        body_p2=_MEMBER_B.get(ep_type, _MEMBER_B["sleep_story"]),
        body_p3=_CTA_B.get(ep_type, _CTA_B["sleep_story"]),
        hero_url=cover_url,
        hero_alt="Active Seal member listening to tonight's " + ep_type.replace("_", " "),
        page_url=page_url,
        card_title=title,
        card_subtitle=subtitle_b,
        cta_label=_CTA_LABEL_B.get(ep_type, "Play tonight's story"),
        footer_text="You're receiving this as an Active Seal member.",
    )

    return email_a, email_b
