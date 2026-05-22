"""Tonight's Anchor format validator.

Run BEFORE assigning the template to the Klaviyo campaign. On any failure
the worker returns ``{'blocked': reasons}`` and does NOT call
``assign_template`` — the campaign exists as a bare draft with no body, and
no send occurs.

Eleven checks (numbered 1..11). Failure messages are prefixed
``"check N: ..."`` so they grep cleanly.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from zoneinfo import ZoneInfo


# ---- Constants the validator enforces (kept local to the validator so the
#      contract is self-documenting; the worker also imports its own copies
#      and the test suite asserts they match). -----------------------------
TEST_AUDIENCE_ID  = "TSpNFi"
TEST_PHASE_LIMIT  = 4

UNIVERSAL_EXCLUSIONS: frozenset[str] = frozenset({
    "TfWQTx", "RbRMPR", "UmhPWG", "WSkan5", "SQ3MuX", "TMwJHE", "YennCj",
    "RUtnZg", "T2TXFk", "ULWR2p", "UpuHSM", "VkbHQJ", "WEgpmt", "UBFUcH",
})

SEND_DAYS         = frozenset({"Tuesday", "Friday"})
WINDOW_START_MIN  = 20 * 60     # 20:00 ET
WINDOW_END_MIN    = 21 * 60     # 21:00 ET
WORD_COUNT_MIN    = 180
WORD_COUNT_MAX    = 320
DISCOUNT_MAX_HOURS = 12.5
DISCOUNT_GRACE_HOURS = 0.5

SUBJECT_PATTERN       = re.compile(r"^Tonight: [^.!?\U0001F300-\U0001FAFF{}]+$")
CAMPAIGN_NAME_PATTERN = re.compile(r"^.+ \| Tonight's Anchor — Issue \d+ \(.+\)$")
DISCOUNT_CODE_PATTERN = re.compile(r"^ANCHOR\d+(_I\d+)?$")
IMG_TAG_PATTERN       = re.compile(r"<img\s", re.IGNORECASE)


_ET = ZoneInfo("America/New_York")


class _StripHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return " ".join(self._parts)


def _strip_html(html_str: str) -> str:
    p = _StripHTML()
    p.feed(html_str)
    return p.text()


def _parse_iso(iso_str: str | None) -> datetime | None:
    if not iso_str:
        return None
    s = iso_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_to_et(iso_str: str | None) -> datetime | None:
    dt = _parse_iso(iso_str)
    if dt is None:
        return None
    return dt.astimezone(_ET)


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_ET)
    return dt.astimezone(timezone.utc)


def _extract_segment_ids(side: Any) -> list[str]:
    """audiences.included/excluded may be a flat list of IDs or a Klaviyo
    relationship-shaped {'data': [{'id': ...}, ...]}. Handle both."""
    if not side:
        return []
    if isinstance(side, list):
        return [s for s in side if isinstance(s, str)]
    if isinstance(side, dict):
        data = side.get("data") or []
        return [d.get("id") for d in data if isinstance(d, dict) and d.get("id")]
    return []


def validate(
    campaign: dict[str, Any],
    template_html: str,
    discount: dict[str, Any] | None,
    issue_number: int,
) -> tuple[bool, list[str]]:
    """Return (passed, reasons). Empty reasons → passed."""
    reasons: list[str] = []

    audiences = campaign.get("audiences") or {}
    included = set(_extract_segment_ids(audiences.get("included")))
    excluded = set(_extract_segment_ids(audiences.get("excluded")))

    # check 1 — test-phase audience
    if issue_number <= TEST_PHASE_LIMIT:
        if included != {TEST_AUDIENCE_ID}:
            reasons.append(
                f"check 1: test phase (Issue {issue_number} ≤ {TEST_PHASE_LIMIT}) "
                f"requires included == {{'{TEST_AUDIENCE_ID}'}}, got {included or '∅'}"
            )

    # check 2 — universal exclusions
    missing = UNIVERSAL_EXCLUSIONS - excluded
    if missing:
        reasons.append(f"check 2: missing universal exclusion segments: {sorted(missing)}")

    # check 3 — send strategy static, not isLocal
    # Klaviyo stores static-send config under send_strategy.options_static
    # (the throttled/sto variants live in options_throttled / options_sto).
    # Older fixtures may use a flat .options key — accept both.
    strategy = campaign.get("sendStrategy") or campaign.get("send_strategy") or {}
    method = strategy.get("method")
    if method != "static":
        reasons.append(f"check 3: sendStrategy.method must be 'static', got {method!r}")
    options_static = (
        strategy.get("options_static")
        or strategy.get("optionsStatic")
        or strategy.get("options")
        or {}
    )
    is_local = options_static.get("isLocal", options_static.get("is_local"))
    if is_local is not False:
        reasons.append(f"check 3: send_strategy.options_static.is_local must be False, got {is_local!r}")

    # check 4 — Tue/Fri, 20:00-21:00 ET window
    send_iso = options_static.get("datetime") or strategy.get("datetime")
    send_dt_et = _parse_to_et(send_iso)
    if send_dt_et is None:
        reasons.append(f"check 4: could not parse sendStrategy.datetime ({send_iso!r})")
    else:
        day_name = send_dt_et.strftime("%A")
        if day_name not in SEND_DAYS:
            reasons.append(f"check 4: send day must be in {sorted(SEND_DAYS)}, got {day_name!r}")
        mins = send_dt_et.hour * 60 + send_dt_et.minute
        if not (WINDOW_START_MIN <= mins <= WINDOW_END_MIN):
            reasons.append(
                f"check 4: send time must be 20:00–21:00 ET, "
                f"got {send_dt_et.strftime('%H:%M')} ET"
            )

    # check 5 — smart sending off
    send_options = campaign.get("sendOptions") or campaign.get("send_options") or {}
    smart = send_options.get("useSmartSending", send_options.get("use_smart_sending"))
    if smart is not False:
        reasons.append(f"check 5: useSmartSending must be False, got {smart!r}")

    # check 6 — UTM scheme
    tracking = campaign.get("trackingOptions") or campaign.get("tracking_options") or {}
    add_tp = tracking.get("addTrackingParams", tracking.get("add_tracking_params"))
    if not add_tp:
        reasons.append(f"check 6: addTrackingParams must be True, got {add_tp!r}")
    utm_params = tracking.get("customTrackingParams") or tracking.get("custom_tracking_params") or []
    utm_names = {p.get("name") for p in utm_params if isinstance(p, dict)}
    missing_utm = {"utm_source", "utm_medium", "utm_campaign", "utm_content"} - utm_names
    if missing_utm:
        reasons.append(f"check 6: missing UTM params: {sorted(missing_utm)}")

    # check 7 — no <img> in template body
    if IMG_TAG_PATTERN.search(template_html or ""):
        reasons.append("check 7: template must not contain <img> tags during test phase")

    # check 8 — word count 180-320
    body_text = _strip_html(template_html or "")
    word_count = len(body_text.split())
    if not (WORD_COUNT_MIN <= word_count <= WORD_COUNT_MAX):
        reasons.append(
            f"check 8: body word count is {word_count}, must be "
            f"{WORD_COUNT_MIN}–{WORD_COUNT_MAX}"
        )

    # check 9 — subject pattern
    subject = _extract_subject(campaign)
    if not SUBJECT_PATTERN.match(subject or ""):
        reasons.append(
            f"check 9: subject must match 'Tonight: …' with no terminal "
            f"punctuation, emoji, or personalization token. Got: {subject!r}"
        )

    # check 10 — discount present, code pattern, timing, duration
    if not discount:
        reasons.append("check 10: no Shopify discount associated with this campaign")
    else:
        code = discount.get("code") or ""
        if not DISCOUNT_CODE_PATTERN.match(code):
            reasons.append(
                f"check 10: discount code must match {DISCOUNT_CODE_PATTERN.pattern!r}, "
                f"got {code!r}"
            )
        starts_at = _parse_iso(discount.get("startsAt"))
        ends_at   = _parse_iso(discount.get("endsAt"))
        if starts_at and send_dt_et and starts_at > _to_utc(send_dt_et):
            reasons.append("check 10: discount startsAt must be ≤ campaign send time")
        if starts_at and ends_at:
            duration_hours = (ends_at - starts_at).total_seconds() / 3600.0
            if duration_hours > DISCOUNT_MAX_HOURS + DISCOUNT_GRACE_HOURS:
                reasons.append(
                    f"check 10: discount window is {duration_hours:.2f}h, "
                    f"max {DISCOUNT_MAX_HOURS}h (+{DISCOUNT_GRACE_HOURS}h grace)"
                )

    # check 11 — campaign name pattern
    name = campaign.get("name") or ""
    if not CAMPAIGN_NAME_PATTERN.match(name):
        reasons.append(f"check 11: campaign name must match pattern, got {name!r}")

    return (len(reasons) == 0, reasons)


def _extract_subject(campaign: dict[str, Any]) -> str:
    """Klaviyo's REST shape: campaign.campaign-messages.data[0].attributes.definition.content.subject
    Some callers may pass the camelCase 'campaignMessages' variant — accept both."""
    messages_container = (
        campaign.get("campaign-messages")
        or campaign.get("campaignMessages")
        or {}
    )
    data = messages_container.get("data") or []
    if not data:
        return ""
    first = data[0] or {}
    attrs = first.get("attributes") or first  # tolerate flat shape too
    definition = attrs.get("definition") or {}
    content = definition.get("content") or {}
    return content.get("subject") or ""
