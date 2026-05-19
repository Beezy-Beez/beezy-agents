"""
workers/episode_deployer.py — deploy a pre-produced sleep audio episode.

Called by the orchestrator for sleep_audio calendar slots.

Two modes:
  PRE-PRODUCED  slot["notes"] contains JSON episode metadata (title, buzzsprout_url,
                cover_image_url, etc.) — full pipeline runs here.
  GENERATE      slot["notes"] is absent or empty — delegates to
                sleep_audio_producer.run_sleep_audio_slot() (script generation flow).

Pre-produced pipeline (when notes metadata is present):
  1. Parse episode metadata from slot["notes"]
  2. Create Shopify page (isPublished=True) using episode page template
  3. Update hub index pages via lib.index_updater
  4. Build two email HTML variants via lib.email_builder_episode
  5. Create Klaviyo DRAFT campaigns: Email A (Engaged Customers excl Active Seal)
     and Email B (Active Seal) using confirmed REST sequence
  6. Save episode row to episodes DB table
  7. Post Slack notification to #beezy-agents
  8. Return {"campaign_id": camp_a_id} for orchestrator to store in calendar_executions

Slot metadata keys (in slot["notes"] as JSON string):
    title               str   — episode title
    episode_type        str   — sleep_story | guided_meditation | affirmation_meditation
                                 | morning_meditation | soundscape
    buzzsprout_url      str   — canonical Buzzsprout URL (also used as page CTA)
    buzzsprout_embed_url str  — embed player URL (optional; embedded in page)
    hero_image_url      str   — cover image URL (Higgsfield CDN or similar)
    description_short   str   — short description for email hook (1–2 sentences)
    description_long    str   — longer description for page body
    script_text         str   — full narration script (stored in page body)
    duration_minutes    int   — episode length in minutes
    suggested_send_date str   — ISO date for campaign naming (YYYY-MM-DD)
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any

import psycopg

from config import DATABASE_URL
from lib.slack import post_draft, notify_failure


def _buzzsprout_embed_src(raw_url: str, player: str = "large") -> str:
    """Convert any Buzzsprout URL to the iframe embed player URL.

    Handles MP3 download URLs (https://www.buzzsprout.com/{pod}/{ep_id}-slug.mp3)
    and already-correct player URLs. Returns empty string if raw_url is empty.
    """
    if not raw_url:
        return ""
    m = re.search(r"buzzsprout\.com/(\d+)/episodes/(\d+)", raw_url)
    if m:
        base = f"https://www.buzzsprout.com/{m.group(1)}/{m.group(2)}"
    else:
        base = raw_url.split("?")[0]
    param = f"client_source={player}_player"
    return f"{base}?{param}&iframe=true"


# ── Audience IDs ──────────────────────────────────────────────────────────────

_ENGAGED_CUSTOMERS = "RvtHdn"
_ACTIVE_SEAL       = "UBFUcH"
_FROM_EMAIL        = os.environ.get("KLAVIYO_FROM_EMAIL", "help@trybeezybeez.com")
_FROM_LABEL        = "Beezy Beez"
_SHOPIFY_DOMAIN    = "https://trybeezybeez.com"

_EPISODE_LABELS = {
    "sleep_story":            "Sleep Story",
    "guided_meditation":      "Guided Meditation",
    "affirmation_meditation": "Affirmation Meditation",
    "morning_meditation":     "Morning Meditation",
    "soundscape":             "Sleep Soundscape",
}

# episode_type → hub handles to update (mirrors hub_updater._EPISODE_HUBS exactly).
# sleep-science-hub is intentionally absent: its content is statically curated;
# sentinel injection there would append below the bottom opt-in (wrong).
_HUB_MAP: dict[str, list[str]] = {
    "sleep_story":            [],
    "soundscape":             [],
    "guided_meditation":      [],
    "affirmation_meditation": [],
    "morning_meditation":     ["morning-wellness-hub"],
}

# page_type hint for index_updater per episode_type
_PAGE_TYPE: dict[str, str] = {
    "sleep_story":            "sleep_story",
    "soundscape":             "sleep_story",
    "guided_meditation":      "meditation",
    "affirmation_meditation": "meditation",
    "morning_meditation":     "morning_meditation",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slug(title: str) -> str:
    return "episode-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


# ── Sleep story page template (copied from the-bridge-of-incidents) ───────────

_STORY_CSS = """<style>
.sleep-story-page { background: #faf6ee; padding: 50px 0 70px 0; font-family: 'Lato', Helvetica, Arial, sans-serif; }
.sleep-story-page .container { max-width: 760px; margin: 0 auto; padding: 0 22px; }
.sleep-story-page .eyebrow { font-size: 12px; letter-spacing: 1.5px; text-transform: uppercase; color: #6b5947; font-weight: 600; margin: 0 0 14px 0; }
.sleep-story-page h1 { font-family: 'Cormorant Garamond', Georgia, serif; font-size: 52px; line-height: 1.05; font-weight: 500; font-style: italic; color: #87401C; margin: 0 0 14px 0; letter-spacing: -0.5px; }
.sleep-story-page .lead { font-size: 19px; line-height: 1.5; color: #2a1f15; margin: 0 0 32px 0; font-weight: 400; }
.sleep-story-page .hero-image { display: block; width: 100%; max-width: 760px; height: auto; border-radius: 4px; margin: 0 0 36px 0; }
.sleep-story-page .audio-wrap { background: #f3ead7; border: 1px solid #e8dfd0; border-radius: 4px; padding: 20px; margin: 0 0 36px 0; }
.sleep-story-page .audio-wrap iframe { display: block; width: 100%; border: 0; min-height: 200px; }
.sleep-story-page .prose p { font-size: 17px; line-height: 1.7; color: #2a1f15; margin: 0 0 18px 0; }
.sleep-story-page .prose p em { font-style: italic; color: #87401C; }
.sleep-story-page .divider { height: 1px; background: #e8dfd0; margin: 48px 0 48px 0; }
.sleep-story-page h2 { font-family: 'Cormorant Garamond', Georgia, serif; font-size: 34px; line-height: 1.15; font-weight: 500; font-style: italic; color: #87401C; margin: 0 0 8px 0; }
.sleep-story-page .section-sub { font-size: 15px; color: #6b5947; margin: 0 0 28px 0; }
.sleep-story-page .products-grid { display: flex; gap: 18px; margin: 0 0 12px 0; flex-wrap: wrap; }
.sleep-story-page .product-card { flex: 1; min-width: 200px; background: #ffffff; border: 1px solid #e8dfd0; border-radius: 4px; padding: 18px; text-align: center; }
.sleep-story-page .product-card img { display: block; width: 100%; max-width: 180px; height: auto; margin: 0 auto 14px auto; }
.sleep-story-page .product-card .name { font-family: 'Cormorant Garamond', Georgia, serif; font-size: 20px; font-style: italic; color: #2a1f15; margin: 0 0 4px 0; line-height: 1.25; }
.sleep-story-page .product-card .price { font-size: 14px; color: #6b5947; margin: 0 0 14px 0; }
.sleep-story-page .product-card .btn { display: inline-block; background: #87401C; color: #ffffff; padding: 10px 22px; font-size: 13px; font-weight: 600; letter-spacing: 0.5px; border-radius: 2px; text-decoration: none; text-transform: uppercase; }
.sleep-story-page .more-content-card { background: #f3ead7; border: 1px solid #e8dfd0; border-radius: 4px; padding: 36px; text-align: center; margin: 16px 0 0 0; }
.sleep-story-page .more-content-card h3 { font-family: 'Cormorant Garamond', Georgia, serif; font-size: 28px; font-style: italic; color: #87401C; margin: 0 0 12px 0; }
.sleep-story-page .more-content-card p { font-size: 16px; color: #2a1f15; margin: 0 0 22px 0; }
.sleep-story-page .more-content-card .btn-large { display: inline-block; background: #87401C; color: #ffffff; padding: 14px 32px; font-size: 14px; font-weight: 600; letter-spacing: 0.8px; border-radius: 2px; text-decoration: none; text-transform: uppercase; }
.sleep-story-page .script-block { background: #ffffff; border: 1px solid #e8dfd0; border-radius: 4px; padding: 32px 36px; }
.sleep-story-page .script-block .prose p { font-size: 16px; line-height: 1.8; }
@media screen and (max-width: 600px) {
  .sleep-story-page h1 { font-size: 36px !important; }
  .sleep-story-page .lead { font-size: 17px !important; }
  .sleep-story-page h2 { font-size: 26px !important; }
  .sleep-story-page .products-grid { flex-direction: column; }
  .sleep-story-page .product-card { min-width: 100%; }
  .sleep-story-page .script-block { padding: 22px 20px; }
}
</style>"""

# Fixed product grid — same 3 products as the live template
_STORY_PRODUCTS = """      <div class="product-card">
        <a href="/products/cinnamon-cbn-honey" style="text-decoration: none; color: inherit;">
          <img src="https://cdn.shopify.com/s/files/1/0616/0616/6777/files/Honey_CBN_Cinnamon__FOR_WEB_IMAGE2.png?v=1733859657" alt="Cinnamon CBN Sleep Honey">
          <p class="name">Cinnamon CBN Sleep Honey</p>
          <p class="price">$64.95</p>
          <span class="btn">Shop now</span>
        </a>
      </div>

      <div class="product-card">
        <a href="/products/botanical-extract-lotion" style="text-decoration: none; color: inherit;">
          <img src="https://cdn.shopify.com/s/files/1/0616/0616/6777/files/beezy-beez-lotion.webp?v=1699816159" alt="Botanical Extract Lotion">
          <p class="name">Botanical Extract Lotion</p>
          <p class="price">$59.95</p>
          <span class="btn">Shop now</span>
        </a>
      </div>

      <div class="product-card">
        <a href="/products/caramel-flavor-honey" style="text-decoration: none; color: inherit;">
          <img src="https://cdn.shopify.com/s/files/1/0616/0616/6777/files/CaramelHoney-WhiteBackground_43a30059-100e-4c98-864d-08a711a3c1fd.jpg?v=1715785132" alt="Caramel Botanical Extract Honey">
          <p class="name">Caramel Botanical Honey</p>
          <p class="price">From $49</p>
          <span class="btn">Shop now</span>
        </a>
      </div>"""


def _page_html_story(meta: dict[str, Any], page_url: str = "") -> str:
    """Sleep story page — exact structure of the-bridge-of-incidents."""
    title       = meta.get("title", "")
    duration    = meta.get("duration_minutes")
    desc_short  = (meta.get("description_short") or "").strip()
    desc_long   = (meta.get("description_long") or desc_short).strip()
    script_text = (meta.get("script_text") or "").strip()
    embed_raw   = meta.get("buzzsprout_embed_url") or meta.get("buzzsprout_url") or ""
    cover_image = (meta.get("hero_image_url") or meta.get("cover_image_url") or "").strip()

    dur_str = f"approximately {duration} minutes" if duration else "approximately 25 minutes"
    lead    = f"{dur_str.capitalize()}. {desc_short}" if desc_short else f"{dur_str.capitalize()}. Tonight's new story from the Sleep Better Podcast."

    # Hero image
    hero_html = (
        f'    <img class="hero-image" src="{cover_image}" alt="{title}">\n'
        if cover_image else ""
    )

    # Audio embed or placeholder
    embed_src = _buzzsprout_embed_src(embed_raw, player="large")
    if embed_src:
        audio_html = (
            f'      <iframe src="{embed_src}" loading="lazy" frameborder="0" '
            f'scrolling="no" title="Beezy Beez Sleep Story: {title}"></iframe>'
        )
    else:
        audio_html = (
            '      <p style="font-size:16px;color:#6b5947;font-style:italic;">'
            "Audio coming shortly — bookmark this page or return from your email link.</p>"
        )

    # Prose paragraphs from description_long
    paras = [p.strip() for p in desc_long.split("\n\n") if p.strip()]
    if not paras:
        paras = [desc_short] if desc_short else [""]
    prose_html = "\n\n".join(f"      <p>{p}</p>" for p in paras)

    # "Read the Full Story" section — between prose and product grid
    if script_text:
        s_paras = [p.strip() for p in script_text.split("\n\n") if p.strip()]
        if not s_paras:
            s_paras = [p.strip() for p in script_text.split("\n") if p.strip()]
        s_body = "\n".join(f"        <p>{p}</p>" for p in s_paras)
        script_section = (
            '    <div class="divider"></div>\n\n'
            '    <h2>Read the Full Story</h2>\n'
            '    <p class="section-sub">The full narration — read along or return to the audio above.</p>\n\n'
            '    <div class="script-block">\n'
            '      <div class="prose">\n'
            + s_body + "\n"
            + '      </div>\n'
            + '    </div>\n\n'
        )
    else:
        script_section = ""

    return (
        _STORY_CSS + "\n\n"
        '<div class="sleep-story-page">\n'
        '  <div class="container">\n\n'
        '    <p class="eyebrow">Sleep Better Podcast · A Beezy Beez story</p>\n\n'
        f'    <h1>{title}</h1>\n\n'
        f'    <p class="lead">{lead}</p>\n\n'
        + hero_html
        + '    <div class="audio-wrap">\n'
        + audio_html + "\n"
        + '    </div>\n\n'
        + '    <div class="prose">\n'
        + prose_html + "\n"
        + '    </div>\n\n'
        + script_section
        + '    <div class="divider"></div>\n\n'
        + '    <h2>Pair this story with our sleep stack</h2>\n'
        + '    <p class="section-sub">The story takes you under. The stack keeps you there.</p>\n\n'
        + '    <div class="products-grid">\n'
        + _STORY_PRODUCTS + "\n"
        + '    </div>\n\n'
        + '    <div class="divider"></div>\n\n'
        + '    <div class="more-content-card">\n'
        + '      <h3>More sleep content from Beezy Beez</h3>\n'
        + '      <p>Sleep science writing, guided meditations, and more stories like this one — all in our editorial hub.</p>\n'
        + '      <a href="https://trybeezybeez.com/pages/sleep-science-hub" class="btn-large">Click Here for More Sleep Content</a>\n'
        + '    </div>\n\n'
        + '  </div>\n'
        + '</div>\n'
    )


# ── Meditation/soundscape page template (copied from sleep-meditation-track-1) ─

_EPIS_CSS = (
    "<style>"
    ".epis-page{--rust:#87401C;--rust-darker:#4f2611;--cream:#faf6ee;--cream-warm:#f3ead7;"
    "--ink:#2a1f15;--muted:#6b5947;--rule:#e8dfd0;background:var(--cream);color:var(--ink);"
    "font-family:Lato,-apple-system,BlinkMacSystemFont,sans-serif;font-size:17px;line-height:1.7;"
    "margin:0 auto;text-align:left}"
    ".epis-page *{box-sizing:border-box}.epis-page a{color:inherit;text-decoration:none}"
    ".epis-page img,.epis-page iframe{max-width:100%;display:block}"
    ".epis-wrap{max-width:760px;margin:0 auto;padding:32px 24px 96px}"
    ".epis-crumb{font-size:13px;color:var(--rust);letter-spacing:.5px;text-transform:uppercase;margin-bottom:32px}"
    ".epis-crumb a{color:var(--rust);border-bottom:1px solid transparent}"
    ".epis-crumb a:hover{border-bottom-color:var(--rust)}"
    ".epis-crumb .sep{margin:0 8px;opacity:.5}"
    ".epis-hero{display:flex!important;flex-direction:column!important;align-items:center!important;"
    "text-align:center;padding:0 0 36px;border-bottom:1px solid var(--rule);margin-bottom:36px}"
    ".epis-hero>*{max-width:100%}"
    ".epis-eyebrow{font-size:12px;color:var(--rust);letter-spacing:1.6px;text-transform:uppercase;"
    "font-weight:700;margin:0 auto 14px;text-align:center}"
    ".epis-h1{font-family:Cormorant Garamond,Georgia,serif;font-size:clamp(34px,5.2vw,52px);"
    "font-weight:600;line-height:1.1;color:var(--rust-darker);letter-spacing:-.4px;margin:0 auto 18px;text-align:center}"
    ".epis-dek{font-family:Cormorant Garamond,Georgia,serif;font-style:italic;font-size:21px;"
    "color:var(--muted);line-height:1.45;max-width:580px;margin:0 auto;text-align:center}"
    ".epis-audio{background:#fff;border:1px solid var(--rule);border-radius:14px;padding:28px;margin-bottom:40px}"
    ".epis-audio-label{font-size:12px;color:var(--rust);letter-spacing:1.6px;text-transform:uppercase;"
    "font-weight:700;margin:0 0 16px}"
    ".epis-audio iframe{border-radius:8px;background:var(--cream)}"
    ".epis-audio-fallback{margin:16px 0 0;font-size:14px;color:var(--muted)}"
    ".epis-audio-fallback a{color:var(--rust);border-bottom:1px solid rgba(135,64,28,.3)}"
    ".epis-section{margin:0 0 40px}"
    ".epis-h2{font-family:Cormorant Garamond,Georgia,serif;font-size:28px;font-weight:600;"
    "color:var(--rust-darker);line-height:1.2;margin:0 0 16px;letter-spacing:-.2px}"
    ".epis-section p{margin:0 0 14px}"
    ".epis-transcript{background:#fff;border:1px solid var(--rule);border-radius:14px;padding:32px;margin-bottom:40px}"
    ".epis-transcript-meta{font-size:13px;color:var(--muted);font-style:italic;margin:0 0 22px;"
    "padding-bottom:18px;border-bottom:1px solid var(--rule)}"
    ".epis-transcript-body p{margin:0 0 16px;color:var(--ink)}"
    ".epis-transcript-body p:last-child{margin-bottom:0}"
    ".epis-context{background:var(--cream-warm);border-radius:14px;padding:32px;margin-bottom:32px}"
    ".epis-context p{margin:0 0 12px}.epis-context p:last-child{margin-bottom:0}"
    ".epis-context a{color:var(--rust);border-bottom:1px solid rgba(135,64,28,.4)}"
    ".epis-back{display:flex;flex-direction:column;align-items:center;margin:0;"
    "padding-top:24px;border-top:1px solid var(--rule);font-size:14px;color:var(--muted)}"
    ".epis-back a{color:var(--rust);border-bottom:1px solid rgba(135,64,28,.3)}"
    "@media (max-width:600px){.epis-wrap{padding:24px 18px 72px}"
    ".epis-transcript,.epis-context,.epis-audio{padding:22px}}"
    ".epis-newsletter{background:linear-gradient(135deg,#f5ede0,#faf6ee);border:1px solid #d9c5a8;"
    "border-radius:12px;padding:40px 32px;text-align:center;max-width:100%;margin:40px 0}"
    ".epis-newsletter h3{font-family:Cormorant Garamond,Georgia,serif;font-size:26px;font-weight:600;"
    "color:var(--rust-darker);margin:0 0 10px}"
    ".epis-newsletter p{font-size:15px;color:#5a4a3a;margin:0 0 20px;line-height:1.6}"
    ".epis-newsletter-form{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}"
    ".epis-newsletter-form input[type=email]{flex:1;min-width:200px;max-width:300px;padding:12px 16px;"
    "border:1px solid #d9c5a8;border-radius:6px;font-family:Lato,sans-serif;font-size:15px;"
    "background:#fff;color:var(--ink)}"
    ".epis-newsletter-form button{padding:12px 24px;background:var(--rust);color:var(--cream);"
    "border:none;border-radius:6px;font-family:Lato,sans-serif;font-weight:700;font-size:13px;"
    "letter-spacing:.06em;text-transform:uppercase;cursor:pointer}"
    ".epis-section p,.epis-transcript-body p,.epis-context p,.epis-page p{font-size:17px!important;line-height:1.7!important}"
    "</style>"
)

_EPIS_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&family=Lato:wght@400;700&display=swap" rel="stylesheet">'
)

_EPIS_PRODUCT_CTA = """<div style="background:linear-gradient(135deg,#8b4513,#a0522d,#6b3410);padding:40px 30px;border-radius:12px;margin:40px 0;text-align:center;">
<h3 style="font-family:Cormorant Garamond,Georgia,serif;font-size:26px;font-weight:600;color:#fffdf7;margin:0 0 15px;font-style:italic;">Built to Support Your Body's Natural Rhythm</h3>
<p style="font-size:16px;line-height:1.65;color:#fffdf7;margin:0 0 25px;opacity:.92;">Beezy Beez Botanical Extract Sleep Honey is designed to support the wind-down phase of your circadian cycle — when your body wants to drop into rest, but stress or overstimulation gets in the way. Clean ingredients. Trusted by 8,500+ five-star customers.</p>
<a href="https://trybeezybeez.com/products/honey-sub" style="display:inline-block;padding:14px 32px;font-size:14px;background-color:#f0c75e;color:#2c2417;text-decoration:none;border-radius:6px;font-weight:700;letter-spacing:1px;text-transform:uppercase;">Try Sleep Honey →</a>
</div>"""

# Newsletter form and archive link replaced by lib.hm_gate.build_gate_episode()
# which uses a cookie (hm_subscriber) and shows the full issue library to subscribers.

# Breadcrumb config per episode_type: (hub_label, hub_url, crumb_label)
_CRUMB_CONFIG: dict[str, tuple[str, str, str]] = {
    "soundscape":             ("Sleep Science", f"{_SHOPIFY_DOMAIN}/pages/sleep-science-hub", "Sleep Soundscape"),
    "guided_meditation":      ("Meditation Library", f"{_SHOPIFY_DOMAIN}/pages/meditation-library", "Guided Sleep Meditation"),
    "affirmation_meditation": ("Meditation Library", f"{_SHOPIFY_DOMAIN}/pages/meditation-library", "Affirmation Meditation"),
    "morning_meditation":     ("Morning Wellness", f"{_SHOPIFY_DOMAIN}/pages/morning-wellness-hub", "Guided Morning Meditation"),
}

_BACK_CONFIG: dict[str, tuple[str, str]] = {
    "soundscape":             ("the Sleep Science Hub", f"{_SHOPIFY_DOMAIN}/pages/sleep-science-hub"),
    "guided_meditation":      ("the Meditation Library", f"{_SHOPIFY_DOMAIN}/pages/meditation-library"),
    "affirmation_meditation": ("the Meditation Library", f"{_SHOPIFY_DOMAIN}/pages/meditation-library"),
    "morning_meditation":     ("the Morning Wellness Hub", f"{_SHOPIFY_DOMAIN}/pages/morning-wellness-hub"),
}

_ABOUT_LABEL: dict[str, str] = {
    "soundscape":             "soundscape",
    "guided_meditation":      "meditation",
    "affirmation_meditation": "meditation",
    "morning_meditation":     "meditation",
}

_TRANSCRIPT_META: dict[str, str] = {
    "soundscape":             "Notes on this soundscape.",
    "guided_meditation":      "Full transcript of this guided meditation, lightly edited for readability.",
    "affirmation_meditation": "Full transcript of this affirmation meditation, lightly edited for readability.",
    "morning_meditation":     "Full transcript of this morning meditation, lightly edited for readability.",
}


def _page_html_meditation(meta: dict[str, Any], page_url: str = "") -> str:
    """Meditation/soundscape page — exact structure of sleep-meditation-track-1."""
    import json as _json

    title        = meta.get("title", "")
    episode_type = meta.get("episode_type", "guided_meditation")
    duration     = meta.get("duration_minutes")
    desc_short   = (meta.get("description_short") or "").strip()
    desc_long    = (meta.get("description_long") or desc_short).strip()
    embed_raw    = meta.get("buzzsprout_embed_url") or meta.get("buzzsprout_url") or ""
    script_text  = (meta.get("script_text") or "").strip()

    label         = _EPISODE_LABELS.get(episode_type, episode_type.replace("_", " ").title())
    dur_str       = f" · {duration} min" if duration else ""
    # Eyebrow: "Sleep Better Podcast · <type label> · <duration>" — NOT the title
    eyebrow       = f"Sleep Better Podcast · {label}{dur_str}"

    hub_label, hub_url, crumb_label = _CRUMB_CONFIG.get(
        episode_type,
        ("Sleep Science", f"{_SHOPIFY_DOMAIN}/pages/sleep-science-hub", label),
    )
    back_label, back_url = _BACK_CONFIG.get(
        episode_type,
        ("the Sleep Science Hub", f"{_SHOPIFY_DOMAIN}/pages/sleep-science-hub"),
    )
    transcript_meta = _TRANSCRIPT_META.get(episode_type, "Full transcript, lightly edited for readability.")
    about_label = _ABOUT_LABEL.get(episode_type, label.lower())

    # Buzzsprout embed — small_player params as on the live page
    embed_src = _buzzsprout_embed_src(embed_raw, player="small")

    iframe_title = f"Sleep Better Podcast - {label} - {title}"
    _ep_match = re.search(r"/episodes/(\d+)", embed_raw)
    mp3_url = (
        f"https://www.buzzsprout.com/2292260/episodes/{_ep_match.group(1)}.mp3"
        if _ep_match else ""
    )

    # About paragraphs
    about_paras = [p.strip() for p in desc_long.split("\n\n") if p.strip()]
    about_html  = "\n".join(f"<p>{p}</p>" for p in about_paras) if about_paras else f"<p>{desc_short}</p>"

    # Transcript paragraphs
    if script_text:
        t_paras = [p.strip() for p in script_text.split("\n\n") if p.strip()]
        if len(t_paras) <= 1:
            t_paras = [p.strip() for p in script_text.split("\n") if p.strip()]
        transcript_html = "\n".join(f"<p>{p}</p>" for p in t_paras)
    else:
        transcript_html = "<p>Transcript coming soon.</p>"

    # JSON-LD
    dur_iso = f"PT{duration}M" if duration else ""
    jsonld_episode = _json.dumps({
        "@context": "https://schema.org",
        "@type": "PodcastEpisode",
        "name": title,
        "description": desc_short or desc_long,
        "url": page_url,
        "duration": dur_iso,
        "inLanguage": "en-US",
        "associatedMedia": {
            "@type": "AudioObject",
            "contentUrl": mp3_url,
            "encodingFormat": "audio/mpeg",
        } if mp3_url else {},
        "partOfSeries": {
            "@type": "PodcastSeries",
            "name": "Sleep Better Podcast",
            "url": "https://deepbearsleep.com",
            "publisher": {"@type": "Organization", "name": "Beezy Beez", "url": _SHOPIFY_DOMAIN},
        },
        "publisher": {
            "@type": "Organization",
            "name": "Beezy Beez",
            "url": _SHOPIFY_DOMAIN,
            "logo": {"@type": "ImageObject",
                     "url": "https://cdn05.zipify.com/SgNn6ZTj7JLGAGruNGAZ7U0KjAY=/fit-in/3840x0/a9688067ccf748cc883f028b3e876c98/beezy-beez-logo.webp"},
        },
    }, ensure_ascii=False)
    jsonld_breadcrumb = _json.dumps({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{_SHOPIFY_DOMAIN}/"},
            {"@type": "ListItem", "position": 2, "name": hub_label, "item": hub_url},
            {"@type": "ListItem", "position": 3, "name": title, "item": page_url},
        ],
    }, ensure_ascii=False)

    parts = [
        _EPIS_CSS,
        _EPIS_FONTS,
        '<article class="epis-page"><div class="epis-wrap">',
        f'<nav class="epis-crumb" aria-label="Breadcrumb">'
        f'<a href="{_SHOPIFY_DOMAIN}/">Home</a>'
        f' <span class="sep">›</span> '
        f'<a href="{hub_url}">{hub_label}</a>'
        f' <span class="sep">›</span> {crumb_label}</nav>',
        f'<div class="epis-hero" role="banner">'
        f'<p class="epis-eyebrow">{eyebrow}</p>'
        f'<h1 class="epis-h1">{title}</h1>'
        f'<p class="epis-dek">{desc_short}</p>'
        f'</div>',
        f'<section class="epis-audio"><p class="epis-audio-label">Listen</p>'
        + (
            f'<iframe src="{embed_src}" loading="lazy" width="100%" height="200" '
            f'frameborder="0" scrolling="no" title="{iframe_title}"></iframe>'
            if embed_src else
            '<p style="font-size:16px;color:#6b5947;font-style:italic;">Audio coming soon — bookmark this page or return from your email link.</p>'
        )
        + f'<p class="epis-audio-fallback">Player not loading? '
          f'<a href="https://www.buzzsprout.com/2292260" target="_blank" rel="noopener">Listen on Buzzsprout</a>, '
          f'<a href="https://podcasts.apple.com/podcast/id1722583143" target="_blank" rel="noopener">Apple Podcasts</a>, or '
          f'<a href="https://open.spotify.com/show/45Y1QPOOMiAhAWjAREkTkZ" target="_blank" rel="noopener">Spotify</a>.</p>'
          f'</section>',
        f'<section class="epis-section"><h2 class="epis-h2">About this {about_label}</h2>'
        f'{about_html}</section>',
        f'<section class="epis-transcript" aria-labelledby="epis-transcript-h">'
        f'<h2 id="epis-transcript-h" class="epis-h2">Transcript</h2>'
        f'<p class="epis-transcript-meta">{transcript_meta}</p>'
        f'<div class="epis-transcript-body">{transcript_html}</div></section>',
        f'<section class="epis-context"><h2 class="epis-h2">About Beezy Beez</h2>'
        f'<p>This {label.lower()} comes from the Sleep Better Podcast, produced by <strong>Beezy Beez</strong> — '
        f'a small wellness brand making botanical extract honey for women navigating sleep changes after 50.</p>'
        f'<p>If a teaspoon of honey before bed is part of your wind-down, our '
        f'<a href="{_SHOPIFY_DOMAIN}/products/honey-sub">Botanical Extract Infused Honey</a> '
        f'is what we make for exactly that moment.</p></section>',
        _EPIS_PRODUCT_CTA,
    ]

    # Cookie-gated subscribe section: form (no cookie) or full issue library (cookie set)
    from lib.hm_gate import build_gate_episode
    parts.append(build_gate_episode())

    parts += [
        f'<p class="epis-back">← <a href="{back_url}">Back to {back_label}</a></p>',
        '</div></article>',
        f'<script type="application/ld+json">{jsonld_episode}</script>',
        f'<script type="application/ld+json">{jsonld_breadcrumb}</script>',
    ]
    return "\n".join(parts)


def _build_page_html(meta: dict[str, Any], page_url: str = "") -> str:
    """Dispatch to the correct page template based on episode_type."""
    if meta.get("episode_type", "sleep_story") == "sleep_story":
        return _page_html_story(meta, page_url)
    return _page_html_meditation(meta, page_url)


def _create_klaviyo_draft(
    html: str,
    name: str,
    subject: str,
    segment_ids: list[str],
    excluded_ids: list[str] | None = None,
) -> str:
    """Create template → campaign → assign template. Returns campaign_id."""
    from agents.klaviyo_deployer import create_template, create_campaign, assign_template
    tpl_id = create_template(html, name)
    camp_id, msg_id = create_campaign(
        name=name,
        subject=subject,
        from_email=_FROM_EMAIL,
        from_label=_FROM_LABEL,
        segment_ids=segment_ids,
        excluded_ids=excluded_ids,
    )
    if msg_id:
        assign_template(msg_id, tpl_id)
    return camp_id


def _parse_meta(slot: dict[str, Any]) -> dict[str, Any] | None:
    """Extract episode metadata from slot["notes"]. Returns None if absent/invalid."""
    raw = slot.get("notes")
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw if raw.get("title") else None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) and parsed.get("title") else None
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _save_episode(meta: dict[str, Any], page_url: str,
                  camp_a_id: str, camp_b_id: str) -> None:
    """Upsert episode row to episodes table."""
    episode_id = meta.get("episode_id") or f"ep_{uuid.uuid4().hex[:10]}"
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute(
                """
                INSERT INTO episodes
                    (episode_id, title, episode_type, buzzsprout_url, shopify_page_url,
                     cover_image_url, duration_minutes, suggested_send_date,
                     klaviyo_campaign_id_a, klaviyo_campaign_id_b)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (episode_id) DO UPDATE SET
                    klaviyo_campaign_id_a = EXCLUDED.klaviyo_campaign_id_a,
                    klaviyo_campaign_id_b = EXCLUDED.klaviyo_campaign_id_b,
                    shopify_page_url      = EXCLUDED.shopify_page_url,
                    deployed_at           = NOW()
                """,
                (
                    episode_id,
                    meta.get("title"),
                    meta.get("episode_type", "sleep_story"),
                    meta.get("buzzsprout_url"),
                    page_url,
                    meta.get("hero_image_url") or meta.get("cover_image_url"),
                    meta.get("duration_minutes"),
                    meta.get("suggested_send_date"),
                    camp_a_id,
                    camp_b_id,
                ),
            )
            conn.commit()
        print(f"[episode_deployer] Episode saved to DB: {episode_id}")
    except Exception as exc:
        print(f"[episode_deployer] DB save failed (non-fatal): {exc}")


# ── Cover image generation ────────────────────────────────────────────────────

# Hard exclusions sent with every Higgsfield call.
_NEGATIVE_PROMPT = (
    "kitchen, cooking, food, honey jar, honey product, yellow sweater, "
    "warm artificial light, fluorescent light, orange dominant, amber dominant, "
    "indoor cooking, product photography, stock photo, yellow lighting, orange background"
)

# Maps story title keywords → a specific cinematic landscape description.
# Checked against both the episode title and description_short (lowercase).
_SCENE_MAP = {
    "bridge":     "ancient stone bridge twilight misty river moonlight",
    "lighthouse": "lighthouse dusk coastal cliffs silver sea moonlight",
    "garden":     "moonlit garden silver dew roses soft mist",
    "forest":     "ancient forest moonlight silver light trees morning mist",
    "cottage":    "stone cottage twilight moonlight garden path silver",
    "ocean":      "calm ocean twilight silver moonlight peaceful horizon",
    "river":      "slow river dusk moonlight silver willows reflections",
    "mountain":   "mountain meadow twilight moonlight silver mist peaks",
    "lake":       "still lake dusk moonlight silver reflection reeds",
    "train":      "empty train platform night soft blue lights mist",
    "field":      "open field twilight moonlight silver grass breeze",
    "beach":      "quiet beach twilight silver moonlight calm waves",
    "valley":     "valley dusk moonlight silver mist gentle hills",
    "path":       "moonlit forest path silver light trees mist",
    "road":       "empty road dusk moonlight silver trees quiet",
    "meadow":     "open meadow twilight moonlight silver mist flowers",
    "rain":       "rain on still water ripples silver blue moonlight",
    "snow":       "snow landscape blue moonlight silver silence",
    "sea":        "calm sea twilight silver moonlight distant horizon",
    "cliff":      "coastal cliffs dusk silver moonlight sea below",
    "harbor":     "harbor dusk moonlight silver water boats still",
    "farm":       "farmhouse dusk moonlight silver hills peaceful",
    "village":    "old village evening moonlight silver stone paths",
    "inn":        "stone inn evening moonlight soft silver light",
    "cave":       "cave entrance moonlight silver mist forest outside",
    "island":     "small island twilight moonlight silver sea reflections",
    "desert":     "desert night blue moonlight silver sand dunes stars",
    "castle":     "ancient castle twilight moonlight silver stone mist",
    "tower":      "stone tower twilight moonlight silver mist horizon",
    "chapel":     "stone chapel evening moonlight silver path mist",
    "abbey":      "stone abbey moonlight silver mist trees evening",
    "shore":      "rocky shore twilight silver moonlight waves mist",
    "bay":        "calm bay twilight moonlight silver water reflections",
    "cove":       "sheltered cove twilight moonlight silver sea mist",
}

# Fallback when no scene keyword is found in title or description.
_GENERIC_LANDSCAPE = "moonlit meadow silver mist blue twilight gentle fog"


def _is_product_image(url: str) -> bool:
    """Return True if the URL looks like a Shopify product image (not a generated cover)."""
    return bool(url and "/products/" in url)


def _episode_image_prompt(meta: dict[str, Any]) -> str:
    """Build a Higgsfield image prompt (≤12 words) from episode context.

    Palette: soft blues, silver moonlight, muted creams, gentle fog.
    NO warm/golden/amber/candlelight as dominant tones.
    Person (when included): Caucasian woman early 50s.
    Style: cinematic, painterly, dreamlike.
    Sleep stories: use scene derived from title/description — NOT a person by default.
    """
    episode_type = meta.get("episode_type", "sleep_story")
    title = meta.get("title", "").lower()
    desc  = (meta.get("description_short") or "").strip().lower()

    if episode_type == "morning_meditation":
        return "Caucasian woman early 50s dawn window pale blue calm cinematic painterly"
    if episode_type in ("guided_meditation", "affirmation_meditation"):
        return "Caucasian woman early 50s moonlit bedroom serene pale blue drifting cinematic"
    if episode_type == "soundscape":
        return "twilight forest moonlight silver mist still water reflections cinematic painterly"

    # sleep_story: derive scene from title + description keywords — no person by default
    for kw, scene in _SCENE_MAP.items():
        if kw in title or kw in desc:
            return f"{scene} cinematic painterly dreamlike"

    # No recognizable scene keyword — use beautiful landscape fallback
    return f"{_GENERIC_LANDSCAPE} cinematic painterly dreamlike"


def _generate_episode_image(meta: dict[str, Any]) -> str:
    """Generate a type-appropriate cover image via Higgsfield and upload to Shopify CDN.

    Returns CDN URL, or '' on failure (non-fatal — caller logs and continues).
    """
    try:
        from workers.image_gen import generate_cover
        from workers.shopify_publisher import upload_image_to_shopify

        prompt = _episode_image_prompt(meta)
        title  = meta.get("title", "episode")
        print(f"[episode_deployer] Generating cover image: {prompt!r}")
        print(f"[episode_deployer] Negative prompt: {_NEGATIVE_PROMPT!r}")

        result = generate_cover(prompt, negative_prompt=_NEGATIVE_PROMPT)
        cdn    = upload_image_to_shopify(result.url, alt=title)
        url    = cdn["url"]
        print(f"[episode_deployer] Cover image on CDN: {url[:80]}...")
        return url
    except Exception as exc:
        print(f"[episode_deployer] Cover image generation failed (non-fatal): {exc}")
        return ""


# ── Public API ────────────────────────────────────────────────────────────────

def run(slot: dict[str, Any]) -> dict[str, Any] | str:
    """
    Main orchestrator entry point for sleep_audio slots.

    If slot["notes"] contains valid episode metadata (JSON with "title"), runs the
    full pre-produced deployment pipeline and returns {"campaign_id": camp_a_id}.

    Otherwise delegates to sleep_audio_producer.run_sleep_audio_slot(slot)
    (generate-from-scratch two-phase flow) and returns its status string.
    """
    meta = _parse_meta(slot)
    if not meta:
        print("[episode_deployer] No episode metadata in slot — delegating to sleep_audio_producer")
        from workers.sleep_audio_producer import run_sleep_audio_slot
        return run_sleep_audio_slot(slot)

    return _deploy_pre_produced(slot, meta)


def _deploy_pre_produced(slot: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Full deployment pipeline for a pre-produced episode."""
    title        = meta["title"]
    episode_type = meta.get("episode_type", "sleep_story")
    label        = _EPISODE_LABELS.get(episode_type, episode_type.replace("_", " ").title())
    slot_date    = slot.get("date", date.today().isoformat())
    send_date    = meta.get("suggested_send_date") or slot_date
    page_slug    = _slug(title)
    page_url     = f"{_SHOPIFY_DOMAIN}/pages/{page_slug}"

    print(f"[episode_deployer] Deploying '{title}' ({label}) — {slot_date}")

    # ── 0. Cover image (generate if missing or a Shopify product image) ───────
    current_img = (meta.get("hero_image_url") or meta.get("cover_image_url") or "").strip()
    if not current_img or _is_product_image(current_img):
        cover_url = _generate_episode_image(meta)
        if cover_url:
            meta = {**meta, "hero_image_url": cover_url, "cover_image_url": cover_url}

    # ── 1. Shopify page (isPublished=True) ────────────────────────────────
    try:
        from workers.shopify_publisher import create_page
        body_html = _build_page_html(meta, page_url=page_url)
        page_result = create_page(
            title=title,
            body_html=body_html,
            handle=page_slug,
            seo_description=(meta.get("description_short") or "")[:155] or None,
            is_published=True,
        )
        page_url = page_result["url"]
        print(f"[episode_deployer] Page created: {page_url}")
    except Exception as exc:
        exc_str = str(exc)
        if "TAKEN" in exc_str or "already been taken" in exc_str.lower():
            # Page exists from a prior run — predicted URL is correct, reuse silently
            print(f"[episode_deployer] Page already exists at {page_url} — reusing")
        else:
            print(f"[episode_deployer] Page creation failed (continuing with predicted URL): {exc}")
            notify_failure("episode_deployer/page", str(exc))

    # ── 2. Update hub index pages ─────────────────────────────────────────
    try:
        from workers.hub_updater import _episode_card
        from lib.index_updater import update_index_page
        card_meta = {**meta, "shopify_page_url": page_url,
                     "cover_image_url": meta.get("hero_image_url") or meta.get("cover_image_url") or ""}
        card_html   = _episode_card(card_meta)
        page_type   = _PAGE_TYPE.get(episode_type, "sleep_story")
        hub_handles = _HUB_MAP.get(episode_type, ["sleep-science-hub"])
        hub_results = {h: update_index_page(h, card_html, page_type) for h in hub_handles}
        print(f"[episode_deployer] Hub updates: {hub_results}")
    except Exception as exc:
        print(f"[episode_deployer] Hub update failed (non-fatal): {exc}")
        hub_results = {}

    # Also call hub_updater.add_episode_to_hubs for DB-backed full rebuild
    try:
        from workers.hub_updater import add_episode_to_hubs
        add_episode_to_hubs({**meta, "shopify_page_url": page_url})
    except Exception as exc:
        print(f"[episode_deployer] add_episode_to_hubs failed (non-fatal): {exc}")

    # ── 3. Build email HTML ───────────────────────────────────────────────
    email_meta = {**meta, "shopify_page_url": page_url,
                  "cover_image_url": meta.get("hero_image_url") or meta.get("cover_image_url") or ""}
    try:
        from lib.email_builder_episode import build_episode_emails
        email_a_html, email_b_html = build_episode_emails(email_meta, page_url)
    except Exception as exc:
        raise RuntimeError(f"Email HTML build failed: {exc}") from exc

    # ── 4. Klaviyo DRAFT campaigns ────────────────────────────────────────
    camp_name_base = f"{title} | {send_date}"

    # Subject lines — curiosity variant first
    _SUBJECT_A = {
        "sleep_story":            f"Tonight: {title}",
        "soundscape":             f"Something new for tonight — {title}",
        "guided_meditation":      f"5 minutes could change tonight — {title}",
        "affirmation_meditation": f"What if you woke up feeling different? — {title}",
        "morning_meditation":     f"Start tomorrow right — {title}",
    }
    _SUBJECT_B = {
        "sleep_story":            f"New sleep story for members: {title}",
        "soundscape":             f"New soundscape for members: {title}",
        "guided_meditation":      f"New guided meditation: {title}",
        "affirmation_meditation": f"New affirmation session: {title}",
        "morning_meditation":     f"New morning session: {title}",
    }
    subj_a = _SUBJECT_A.get(episode_type, f"Tonight: {title}")
    subj_b = _SUBJECT_B.get(episode_type, f"New {label}: {title}")

    print(f"[episode_deployer] Creating Klaviyo campaign A (Engaged Customers)...")
    camp_a_id = _create_klaviyo_draft(
        html=email_a_html,
        name=f"{camp_name_base} | Engaged Customers",
        subject=subj_a,
        segment_ids=[_ENGAGED_CUSTOMERS],
        excluded_ids=[_ACTIVE_SEAL],
    )
    print(f"[episode_deployer]   campaign_a: {camp_a_id}")

    print(f"[episode_deployer] Creating Klaviyo campaign B (Active Seal)...")
    camp_b_id = _create_klaviyo_draft(
        html=email_b_html,
        name=f"{camp_name_base} | Active Seal",
        subject=subj_b,
        segment_ids=[_ACTIVE_SEAL],
    )
    print(f"[episode_deployer]   campaign_b: {camp_b_id}")

    # ── 5. Save episode to DB ─────────────────────────────────────────────
    _save_episode(meta, page_url, camp_a_id, camp_b_id)

    # ── 6. Slack notification ─────────────────────────────────────────────
    admin_a = f"https://www.klaviyo.com/campaign/{camp_a_id}/wizard"
    admin_b = f"https://www.klaviyo.com/campaign/{camp_b_id}/wizard"
    post_draft(
        title=f"Episode deployed: {title}",
        summary_lines=[
            f"*Title:* {title}",
            f"*Type:* {label}",
            f"*Page:* {page_url}",
            f"*Email A (Engaged Customers):* {admin_a}",
            f"*Email B (Active Seal):* {admin_b}",
            "Ready for Boris review.",
        ],
        body=(
            f"Klaviyo A: {admin_a}\n"
            f"Klaviyo B: {admin_b}\n\n"
            f"Both campaigns are DRAFT — review subject lines, then schedule "
            f"Email A for 8:00pm ET and Email B for 8:15pm ET."
        ),
        image_url=meta.get("hero_image_url") or meta.get("cover_image_url") or None,
        image_alt=title,
    )
    print(f"[episode_deployer] Slack posted")

    return {"campaign_id": camp_a_id}
