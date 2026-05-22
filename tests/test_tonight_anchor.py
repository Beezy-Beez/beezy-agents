"""Pure unit tests for Tonight's Anchor validator + kill rule.

No Klaviyo, Shopify, or DB calls — everything mocked.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from lib import tonight_anchor_validator as V
from workers import tonight_anchor as W


_ET = ZoneInfo("America/New_York")


# ─── Builders for a "valid baseline" campaign dict ────────────────────────
def _send_iso(dt_et: datetime) -> str:
    return dt_et.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _good_campaign(
    *,
    issue_number: int = 1,
    send_at_et: datetime | None = None,
    subject: str = "Tonight: a small thing you can try in the next 12 hours",
    included: list[str] | None = None,
    excluded: list[str] | None = None,
    name: str | None = None,
    utm_content: str = "tonights_anchor_001_engaged_otb",
) -> dict:
    send_at_et = send_at_et or datetime(2026, 5, 22, 20, 30, tzinfo=_ET)  # Fri 8:30 PM
    included = included if included is not None else [V.TEST_AUDIENCE_ID]
    excluded = excluded if excluded is not None else sorted(V.UNIVERSAL_EXCLUSIONS)
    name = name or "Engaged One-Time Buyers | Tonight's Anchor — Issue 1 (Breath + Honey)"
    return {
        "name": name,
        "audiences": {"included": included, "excluded": excluded},
        "send_strategy": {
            "method": "static",
            "options_static": {
                "datetime": _send_iso(send_at_et),
                "is_local": False,
                "send_past_recipients_immediately": False,
            },
        },
        "send_options": {"use_smart_sending": False},
        "tracking_options": {
            "add_tracking_params": True,
            "custom_tracking_params": [
                {"name": "utm_source",   "type": "static",  "value": "Klaviyo"},
                {"name": "utm_medium",   "type": "static",  "value": "campaign"},
                {"name": "utm_campaign", "type": "dynamic", "value": "campaign_name"},
                {"name": "utm_content",  "type": "static",  "value": utm_content},
            ],
        },
        "campaign-messages": {"data": [{
            "type": "campaign-message",
            "attributes": {
                "definition": {
                    "channel": "email",
                    "content": {
                        "subject":      subject,
                        "preview_text": "A quiet 12-hour offer",
                    },
                },
            },
        }]},
    }


_CTA_HTML = (
    "<a href=\"https://trybeezybeez.com/discount/ANCHOR20?redirect=/collections/tag\">"
    "Use $20 off — code ANCHOR20</a>"
)
# Count the visible word tokens the validator will see from the CTA (it
# strips HTML before counting). Keeping this in sync via runtime computation
# means the body filler is sized exactly so the total equals `words`.
_CTA_WORDS = len(V._strip_html(_CTA_HTML).split())


def _good_html(*, words: int = 220) -> str:
    """Render a body whose stripped word count is EXACTLY `words`.

    Validator check 8 counts all visible tokens; the body filler is sized
    so body_filler + CTA == words.
    """
    filler_n = max(0, words - _CTA_WORDS)
    body = " ".join(["honey"] * filler_n)
    return f"<html><body><p>{body}</p>{_CTA_HTML}</body></html>"


def _good_discount(send_at_et: datetime | None = None, code: str = "ANCHOR20") -> dict:
    send_at_et = send_at_et or datetime(2026, 5, 22, 20, 30, tzinfo=_ET)
    send_utc = send_at_et.astimezone(timezone.utc)
    return {
        "code":     code,
        "startsAt": send_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endsAt":   (send_utc + timedelta(hours=12, minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ─── Sanity: the baseline passes ──────────────────────────────────────────
def test_baseline_passes_all_eleven_checks():
    passed, reasons = V.validate(_good_campaign(), _good_html(), _good_discount(), issue_number=1)
    assert passed, f"baseline should pass but failed: {reasons}"
    assert reasons == []


# ─── Validator checks 1..11 ───────────────────────────────────────────────
def test_validator_check_1_test_phase_audience():
    # Wrong audience during test phase (issue ≤ 4)
    bad = _good_campaign(included=["RvtHdn"])
    passed, reasons = V.validate(bad, _good_html(), _good_discount(), issue_number=1)
    assert not passed
    assert any("check 1" in r for r in reasons)

    # After test phase (issue 5+), any audience allowed
    bad_after = _good_campaign(included=["RvtHdn", "TSpNFi"])
    passed_after, reasons_after = V.validate(bad_after, _good_html(), _good_discount(), issue_number=5)
    assert all("check 1" not in r for r in reasons_after), reasons_after


def test_validator_check_2_exclusions_present():
    # Drop one exclusion ID
    short = sorted(V.UNIVERSAL_EXCLUSIONS - {"TfWQTx"})
    bad = _good_campaign(excluded=short)
    passed, reasons = V.validate(bad, _good_html(), _good_discount(), issue_number=1)
    assert not passed
    assert any("check 2" in r and "TfWQTx" in r for r in reasons)


def test_validator_check_3_send_strategy_static_and_not_local():
    bad = _good_campaign()
    bad["send_strategy"]["method"] = "throttled"
    bad["send_strategy"]["options_static"]["is_local"] = True
    passed, reasons = V.validate(bad, _good_html(), _good_discount(), issue_number=1)
    assert not passed
    assert any("check 3" in r and "static" in r for r in reasons)
    assert any("check 3" in r and "is_local" in r.lower() for r in reasons)


def test_validator_check_4_send_window_day_and_time():
    # Wrong day (Monday)
    mon = datetime(2026, 5, 25, 20, 30, tzinfo=_ET)  # 2026-05-25 is a Monday
    bad_day = _good_campaign(send_at_et=mon)
    _, r_day = V.validate(bad_day, _good_html(), _good_discount(send_at_et=mon), issue_number=1)
    assert any("check 4" in r and "Monday" in r for r in r_day)

    # Right day, wrong time (19:30 ET)
    fri_early = datetime(2026, 5, 22, 19, 30, tzinfo=_ET)
    bad_time = _good_campaign(send_at_et=fri_early)
    _, r_time = V.validate(bad_time, _good_html(), _good_discount(send_at_et=fri_early), issue_number=1)
    assert any("check 4" in r and "20:00" in r for r in r_time)

    # Edge of window — exactly 21:00 ET is allowed
    fri_edge = datetime(2026, 5, 22, 21, 0, tzinfo=_ET)
    edge = _good_campaign(send_at_et=fri_edge)
    passed_edge, r_edge = V.validate(edge, _good_html(), _good_discount(send_at_et=fri_edge), issue_number=1)
    assert all("check 4" not in r for r in r_edge), r_edge


def test_validator_check_5_smart_sending_off():
    bad = _good_campaign()
    bad["send_options"]["use_smart_sending"] = True
    passed, reasons = V.validate(bad, _good_html(), _good_discount(), issue_number=1)
    assert not passed
    assert any("check 5" in r for r in reasons)


def test_validator_check_6_utm_params():
    bad = _good_campaign()
    # Drop utm_content
    bad["tracking_options"]["custom_tracking_params"] = [
        p for p in bad["tracking_options"]["custom_tracking_params"]
        if p["name"] != "utm_content"
    ]
    passed, reasons = V.validate(bad, _good_html(), _good_discount(), issue_number=1)
    assert not passed
    assert any("check 6" in r and "utm_content" in r for r in reasons)


def test_validator_check_7_no_images():
    html_with_img = (
        "<html><body><p>" + ("honey " * 220) + "</p>"
        "<img src=\"https://x/y.png\" alt=\"x\">"
        "</body></html>"
    )
    passed, reasons = V.validate(_good_campaign(), html_with_img, _good_discount(), issue_number=1)
    assert not passed
    assert any("check 7" in r for r in reasons)


def test_validator_check_8_word_count_180_to_320():
    # 179 words → fail
    passed_lo, r_lo = V.validate(_good_campaign(), _good_html(words=179), _good_discount(), issue_number=1)
    assert not passed_lo
    assert any("check 8" in r and "179" in r for r in r_lo)

    # 321 words → fail
    passed_hi, r_hi = V.validate(_good_campaign(), _good_html(words=321), _good_discount(), issue_number=1)
    assert not passed_hi
    assert any("check 8" in r and "321" in r for r in r_hi)

    # 180 and 320 → pass (edges inclusive)
    passed_edge_lo, r_edge_lo = V.validate(_good_campaign(), _good_html(words=180), _good_discount(), issue_number=1)
    passed_edge_hi, r_edge_hi = V.validate(_good_campaign(), _good_html(words=320), _good_discount(), issue_number=1)
    assert all("check 8" not in r for r in r_edge_lo), r_edge_lo
    assert all("check 8" not in r for r in r_edge_hi), r_edge_hi


def test_validator_check_9_subject_pattern():
    bad_subjects = [
        "Tonight!",                                              # wrong prefix punctuation
        "Tonight: a small thing you can try.",                   # trailing period
        "Tonight: hey {{ first_name }}",                         # personalization in subject
        "Tonight: 🌙 a quiet offer",                              # emoji
        "Reminder: a small thing tonight",                       # no Tonight: prefix
    ]
    for s in bad_subjects:
        c = _good_campaign(subject=s)
        passed, reasons = V.validate(c, _good_html(), _good_discount(), issue_number=1)
        assert not passed, f"subject should have failed: {s!r}"
        assert any("check 9" in r for r in reasons), f"missing check 9 for: {s!r}"


def test_validator_check_10_discount_window_and_timing():
    send_at_et = datetime(2026, 5, 22, 20, 30, tzinfo=_ET)
    send_utc = send_at_et.astimezone(timezone.utc)

    # startsAt AFTER send time → fail
    late_start = {
        "code":     "ANCHOR20",
        "startsAt": (send_utc + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endsAt":   (send_utc + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _, r1 = V.validate(_good_campaign(send_at_et=send_at_et), _good_html(), late_start, issue_number=1)
    assert any("check 10" in r and "startsAt" in r for r in r1)

    # Window > 13h → fail
    long_window = {
        "code":     "ANCHOR20",
        "startsAt": send_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endsAt":   (send_utc + timedelta(hours=14)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _, r2 = V.validate(_good_campaign(send_at_et=send_at_et), _good_html(), long_window, issue_number=1)
    assert any("check 10" in r and "12.5" in r for r in r2)

    # Bad code shape → fail
    bad_code = _good_discount(send_at_et=send_at_et)
    bad_code["code"] = "ANCHOR-NIGHT"
    _, r3 = V.validate(_good_campaign(send_at_et=send_at_et), _good_html(), bad_code, issue_number=1)
    assert any("check 10" in r and "ANCHOR" in r for r in r3)

    # Issue-suffix variant — ANCHOR20_I2 must pass the regex
    suffixed = _good_discount(send_at_et=send_at_et, code="ANCHOR20_I2")
    passed_sfx, r_sfx = V.validate(_good_campaign(send_at_et=send_at_et), _good_html(), suffixed, issue_number=2)
    assert all("check 10" not in r for r in r_sfx), r_sfx

    # Missing discount entirely → fail
    _, r4 = V.validate(_good_campaign(send_at_et=send_at_et), _good_html(), None, issue_number=1)
    assert any("check 10" in r and "no Shopify discount" in r for r in r4)


def test_validator_check_11_campaign_name_pattern():
    bad = _good_campaign(name="Tonight's Anchor Issue 1 Breath + Honey")  # missing | and ()
    passed, reasons = V.validate(bad, _good_html(), _good_discount(), issue_number=1)
    assert not passed
    assert any("check 11" in r for r in reasons)


# ─── Kill rule ────────────────────────────────────────────────────────────
def test_kill_check_does_not_fire_with_three_completed_sends(monkeypatch):
    """3 sends with $0.10 RPR — does NOT kill; the test phase runs the full 4."""
    monkeypatch.setattr(W, "completed_send_count", lambda conn: 3)
    monkeypatch.setattr(W, "aggregate_rpr",        lambda conn: 0.10)
    kill, reason = W.kill_check(conn=None)
    assert kill is False
    assert reason is None


def test_kill_check_fires_at_four_sends_below_threshold(monkeypatch):
    monkeypatch.setattr(W, "completed_send_count", lambda conn: 4)
    monkeypatch.setattr(W, "aggregate_rpr",        lambda conn: 0.30)
    kill, reason = W.kill_check(conn=None)
    assert kill is True
    assert reason is not None
    assert "$0.30" in reason
    assert "$0.40" in reason


def test_kill_check_does_not_fire_at_four_sends_above_threshold(monkeypatch):
    monkeypatch.setattr(W, "completed_send_count", lambda conn: 4)
    monkeypatch.setattr(W, "aggregate_rpr",        lambda conn: 0.42)
    kill, reason = W.kill_check(conn=None)
    assert kill is False
    assert reason is None


# ─── Cross-module invariant: worker constants match validator constants ───
def test_worker_and_validator_constants_match():
    """The validator enforces the contract; the worker generates campaigns
    against it. If these diverge the worker can silently produce campaigns
    the validator will reject — catch it at test time."""
    assert W.TEST_AUDIENCE_ID == V.TEST_AUDIENCE_ID
    assert set(W.EXCLUDED_SEGMENTS) == set(V.UNIVERSAL_EXCLUSIONS)
    assert W.TEST_PHASE_SEND_COUNT == V.TEST_PHASE_LIMIT
