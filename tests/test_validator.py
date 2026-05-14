"""Tests for workers/validator.py — all 17 rules (R1–R12, C1–C5).

Uses a real Postgres connection per project rules (no mocks).
Fixture data is inserted and rolled back in each test.
"""
import pytest
from datetime import date, timedelta
import uuid

from workers.validator import validate_campaign


@pytest.fixture
def conn():
    """Real Postgres connection, auto-rollback after each test."""
    from db.connection import get_conn
    with get_conn() as c:
        yield c


def _slot(audience="lapsed_30d", content_type="klaviyo_campaign", **kwargs):
    return {
        "date": date.today().isoformat(),
        "audience": audience,
        "content_type": content_type,
        "revenue_estimate": 500,
        **kwargs,
    }


def _copy(**kwargs):
    return {
        "subject": "{{ first_name }}, your sleep secret awaits",
        "preview_text": "Discover what 50+ women know",
        "body_paragraphs": ["We tested this with 400 women.", "The results were remarkable."],
        "image_prompt": "woman 50 honey tones warm editorial",
        "cta_text": "SHOP NOW",
        **kwargs,
    }


def _insert_exec(conn, audience, slot_date, content_type="klaviyo_campaign", status="dispatched"):
    """Insert a calendar_executions row."""
    eid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO calendar_executions (id, decision_id, slot_date, content_type, audience, status) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (eid, str(uuid.uuid4()), slot_date, content_type, audience, status)
    )
    conn.commit()
    return eid


# ── Happy path ────────────────────────────────────────────────────────────────

def test_pass_returns_pass_verdict(conn):
    result = validate_campaign(conn, _slot(), _copy(), "https://trybeezybeez.com/pages/bf-collection")
    assert result["pass"] is True
    assert result["verdict"] == "PASS"


# ── R1: Smart sending ≥24h ────────────────────────────────────────────────────

def test_r1_same_day_send_fails(conn):
    _insert_exec(conn, "lapsed_30d", date.today())
    result = validate_campaign(conn, _slot("lapsed_30d"), _copy(), "https://trybeezybeez.com/pages/bf-collection")
    r1 = next(r for r in result["results"] if r["rule"] == "R1")
    assert r1["pass"] is False


def test_r1_yesterday_send_passes(conn):
    _insert_exec(conn, "vip", date.today() - timedelta(days=8))
    result = validate_campaign(conn, _slot("vip"), _copy(), "https://trybeezybeez.com/pages/bf-collection")
    r1 = next(r for r in result["results"] if r["rule"] == "R1")
    assert r1["pass"] is True


# ── R2: 7-day cooldown (AUTO-FAIL) ───────────────────────────────────────────

def test_r2_sent_3_days_ago_autofail(conn):
    _insert_exec(conn, "whales", date.today() - timedelta(days=3))
    result = validate_campaign(conn, _slot("whales"), _copy(), "https://trybeezybeez.com/pages/bf-collection")
    r2 = next(r for r in result["results"] if r["rule"] == "R2")
    assert r2["pass"] is False
    assert result["pass"] is False
    assert result["verdict"] == "FAIL"
    assert any(r["rule"] == "R2" for r in result["auto_fails"])


def test_r2_sent_7_days_ago_passes(conn):
    _insert_exec(conn, "engaged_prospects", date.today() - timedelta(days=7))
    result = validate_campaign(
        conn, _slot("engaged_prospects"), _copy(),
        "https://trybeezybeez.com/pages/sleep-science"
    )
    r2 = next(r for r in result["results"] if r["rule"] == "R2")
    assert r2["pass"] is True


# ── R3: Theme 5-day gap ───────────────────────────────────────────────────────

def test_r3_same_theme_3_days_fails(conn):
    _insert_exec(conn, "lapsed_60d", date.today() - timedelta(days=3), content_type="klaviyo_campaign")
    result = validate_campaign(conn, _slot("lapsed_60d", "klaviyo_campaign"), _copy(), "https://trybeezybeez.com/pages/bf-collection")
    r3 = next(r for r in result["results"] if r["rule"] == "R3")
    assert r3["pass"] is False


# ── R4: Active Seal weekly <4 ─────────────────────────────────────────────────

def test_r4_active_seal_over_limit_fails(conn):
    for i in range(1, 5):
        _insert_exec(conn, "active_seal", date.today() - timedelta(days=i))
    result = validate_campaign(conn, _slot("active_seal"), _copy(), "https://trybeezybeez.com/pages/bf-collection")
    r4 = next(r for r in result["results"] if r["rule"] == "R4")
    assert r4["pass"] is False


# ── R6: Revenue floor ─────────────────────────────────────────────────────────

def test_r6_below_floor_fails(conn):
    result = validate_campaign(conn, _slot(revenue_estimate=100), _copy(), "https://trybeezybeez.com/pages/bf-collection")
    r6 = next(r for r in result["results"] if r["rule"] == "R6")
    assert r6["pass"] is False


def test_r6_no_estimate_skips(conn):
    slot = _slot()
    del slot["revenue_estimate"]
    result = validate_campaign(conn, slot, _copy(), "https://trybeezybeez.com/pages/bf-collection")
    r6 = next(r for r in result["results"] if r["rule"] == "R6")
    assert r6["pass"] is True  # STUB — no estimate, skip


# ── R7: Kill list ─────────────────────────────────────────────────────────────

def test_r7_active_seal_editorial_fails(conn):
    result = validate_campaign(conn, _slot("active_seal", "editorial"), _copy(), "https://trybeezybeez.com/pages/bf-collection")
    r7 = next(r for r in result["results"] if r["rule"] == "R7")
    assert r7["pass"] is False


# ── R8: Daily cadence ≤3 ─────────────────────────────────────────────────────

def test_r8_three_sends_today_fails(conn):
    for aud in ("lapsed_30d", "lapsed_60d", "lapsed_90d"):
        _insert_exec(conn, aud, date.today())
    result = validate_campaign(conn, _slot("vip"), _copy(), "https://trybeezybeez.com/pages/bf-collection")
    r8 = next(r for r in result["results"] if r["rule"] == "R8")
    assert r8["pass"] is False


# ── C1: Subject syntax (AUTO-FAIL) ───────────────────────────────────────────

def test_c1_person_firstname_autofail(conn):
    bad_copy = _copy(subject="{{ person.first_name|default:'there' }}, your sleep fix")
    result = validate_campaign(conn, _slot(), bad_copy, "https://trybeezybeez.com/pages/bf-collection")
    c1 = next(r for r in result["results"] if r["rule"] == "C1")
    assert c1["pass"] is False
    assert result["pass"] is False
    assert any(r["rule"] == "C1" for r in result["auto_fails"])


def test_c1_first_name_passes(conn):
    good_copy = _copy(subject="{{ first_name }}, your sleep fix")
    result = validate_campaign(conn, _slot(), good_copy, "https://trybeezybeez.com/pages/bf-collection")
    c1 = next(r for r in result["results"] if r["rule"] == "C1")
    assert c1["pass"] is True


# ── C2: CTA URL for customers (AUTO-FAIL) ────────────────────────────────────

def test_c2_customer_to_landing_page_fails(conn):
    result = validate_campaign(conn, _slot("vip"), _copy(), "https://trybeezybeez.com/pages/sleep-science-article")
    c2 = next(r for r in result["results"] if r["rule"] == "C2")
    assert c2["pass"] is False
    assert any(r["rule"] == "C2" for r in result["auto_fails"])


def test_c2_customer_to_bf_collection_passes(conn):
    result = validate_campaign(conn, _slot("vip"), _copy(), "https://trybeezybeez.com/pages/bf-collection")
    c2 = next(r for r in result["results"] if r["rule"] == "C2")
    assert c2["pass"] is True


def test_c2_customer_to_discount_passes(conn):
    result = validate_campaign(conn, _slot("lapsed_30d"), _copy(), "https://trybeezybeez.com/discount/SLEEP20?redirect=/pages/bf-collection")
    c2 = next(r for r in result["results"] if r["rule"] == "C2")
    assert c2["pass"] is True


# ── C3: High-value segment + discount (AUTO-FAIL) ────────────────────────────

def test_c3_vip_discount_language_fails(conn):
    disc_copy = _copy(body_paragraphs=["Get 20% off your next order today!", "This discount won't last."])
    result = validate_campaign(conn, _slot("vip"), disc_copy, "https://trybeezybeez.com/pages/bf-collection")
    c3 = next(r for r in result["results"] if r["rule"] == "C3")
    assert c3["pass"] is False
    assert any(r["rule"] == "C3" for r in result["auto_fails"])


def test_c3_lapsed_30d_discount_language_passes(conn):
    disc_copy = _copy(body_paragraphs=["Get 20% off your next order.", "You've earned this discount."])
    result = validate_campaign(conn, _slot("lapsed_30d"), disc_copy, "https://trybeezybeez.com/discount/SLEEP20?redirect=/pages/bf-collection")
    c3 = next(r for r in result["results"] if r["rule"] == "C3")
    assert c3["pass"] is True


# ── C4: Image includes humans ─────────────────────────────────────────────────

def test_c4_no_human_fails(conn):
    bad_copy = _copy(image_prompt="golden honey jar warm light close up macro shot")
    result = validate_campaign(conn, _slot(), bad_copy, "https://trybeezybeez.com/pages/bf-collection")
    c4 = next(r for r in result["results"] if r["rule"] == "C4")
    assert c4["pass"] is False


def test_c4_woman_in_prompt_passes(conn):
    good_copy = _copy(image_prompt="woman 50 holding honey jar warm morning light kitchen")
    result = validate_campaign(conn, _slot(), good_copy, "https://trybeezybeez.com/pages/bf-collection")
    c4 = next(r for r in result["results"] if r["rule"] == "C4")
    assert c4["pass"] is True


# ── C5: Collection URL (AUTO-FAIL) ───────────────────────────────────────────

def test_c5_collections_all_fails(conn):
    result = validate_campaign(conn, _slot(), _copy(), "https://trybeezybeez.com/collections/all")
    c5 = next(r for r in result["results"] if r["rule"] == "C5")
    assert c5["pass"] is False
    assert result["pass"] is False
    assert any(r["rule"] == "C5" for r in result["auto_fails"])


def test_c5_bf_collection_passes(conn):
    result = validate_campaign(conn, _slot(), _copy(), "https://trybeezybeez.com/pages/bf-collection")
    c5 = next(r for r in result["results"] if r["rule"] == "C5")
    assert c5["pass"] is True


# ── Auto-fail verdict test ────────────────────────────────────────────────────

def test_any_autofail_blocks_completely(conn):
    """Even one auto-fail rule must yield pass=False, verdict=FAIL."""
    bad_copy = _copy(subject="{{ person.first_name|default:'there' }}, test")
    result = validate_campaign(conn, _slot(), bad_copy, "https://trybeezybeez.com/pages/bf-collection")
    assert result["pass"] is False
    assert result["verdict"] == "FAIL"
