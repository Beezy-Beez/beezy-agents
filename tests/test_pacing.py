"""Tests for pacing/brain.py — gap math, dedup, status thresholds.

Uses real Postgres (no mocks).
"""
import pytest
import uuid
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal


@pytest.fixture
def conn():
    from db.connection import get_conn
    with get_conn() as c:
        yield c


def _seed_goal(conn, target=150000, period_start=None, period_end=None):
    """Insert a test goal and return its id."""
    if period_start is None:
        period_start = date.today().replace(day=1)
    if period_end is None:
        # Last day of this month
        import calendar as _cal
        last = _cal.monthrange(period_start.year, period_start.month)[1]
        period_end = period_start.replace(day=last)
    gid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO goals (id, title, target_metric, target_value, period_start, period_end, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'active')",
        (gid, "Test Goal", "revenue", target, period_start, period_end)
    )
    conn.commit()
    return gid


def _seed_order(conn, order_id, revenue, order_date):
    """Insert a Shopify order_revenue performance row."""
    conn.execute(
        "INSERT INTO performance (id, source, metric_name, metric_value, dimensions, measured_at) "
        "VALUES (%s, 'shopify', 'order_revenue', %s, %s::jsonb, NOW())",
        (str(uuid.uuid4()), revenue,
         f'{{"order_id":"{order_id}","created_at":"{order_date.isoformat()}T00:00:00+00:00"}}')
    )
    conn.commit()


def _cleanup_goal(conn, goal_id):
    conn.execute("DELETE FROM goals WHERE id=%s", (goal_id,))
    conn.commit()


def _cleanup_perf(conn, order_id):
    conn.execute("DELETE FROM performance WHERE dimensions->>'order_id'=%s", (order_id,))
    conn.commit()


# ── compute_pacing_state ──────────────────────────────────────────────────────

def test_gap_pct_ahead(conn):
    gid = _seed_goal(conn, target=150000)
    oid = str(uuid.uuid4())
    # Seed revenue = $150K so we're 100% there on day 1
    _seed_order(conn, oid, 150000, date.today())
    try:
        from pacing.brain import compute_pacing_state
        state = compute_pacing_state(gid)
        assert state.gap_pct > Decimal("0")
        assert state.status == "ahead"
    finally:
        _cleanup_goal(conn, gid)
        _cleanup_perf(conn, oid)


def test_gap_pct_behind(conn):
    gid = _seed_goal(conn, target=150000)
    oid = str(uuid.uuid4())
    # Seed very small revenue = $1 on day 14 of month → behind
    order_date = date.today().replace(day=max(1, date.today().day - 1))
    _seed_order(conn, oid, 1, order_date)
    try:
        from pacing.brain import compute_pacing_state
        state = compute_pacing_state(gid)
        assert state.status == "behind"
        assert state.gap_pct < Decimal("0")
    finally:
        _cleanup_goal(conn, gid)
        _cleanup_perf(conn, oid)


def test_required_daily_rate_math(conn):
    gid = _seed_goal(conn, target=150000)
    oid = str(uuid.uuid4())
    _seed_order(conn, oid, 75000, date.today())
    try:
        from pacing.brain import compute_pacing_state
        state = compute_pacing_state(gid)
        # Remaining = 75000, days remaining = days_left
        expected = Decimal("75000") / Decimal(max(state.days_remaining, 1))
        assert abs(state.required_daily_rate - expected) < Decimal("1")
    finally:
        _cleanup_goal(conn, gid)
        _cleanup_perf(conn, oid)


# ── Dedup logic ───────────────────────────────────────────────────────────────

def test_dedup_same_order_id_takes_latest(conn):
    """Two rows for same order_id: only the latest measured_at counts."""
    gid = _seed_goal(conn, target=150000)
    oid = str(uuid.uuid4())
    today_str = date.today().isoformat() + "T00:00:00+00:00"

    # Insert two rows: old value $200, new value $150 (refund scenario)
    conn.execute(
        "INSERT INTO performance (id, source, metric_name, metric_value, dimensions, measured_at) "
        "VALUES (%s, 'shopify', 'order_revenue', 200, %s::jsonb, NOW() - INTERVAL '1 hour')",
        (str(uuid.uuid4()), f'{{"order_id":"{oid}","created_at":"{today_str}"}}')
    )
    conn.execute(
        "INSERT INTO performance (id, source, metric_name, metric_value, dimensions, measured_at) "
        "VALUES (%s, 'shopify', 'order_revenue', 150, %s::jsonb, NOW())",
        (str(uuid.uuid4()), f'{{"order_id":"{oid}","created_at":"{today_str}"}}')
    )
    conn.commit()

    try:
        from pacing.brain import compute_pacing_state
        state = compute_pacing_state(gid)
        # Should use $150 (latest), not $200 (old)
        assert state.period_to_date_value == Decimal("150.00")
    finally:
        _cleanup_goal(conn, gid)
        conn.execute("DELETE FROM performance WHERE dimensions->>'order_id'=%s", (oid,))
        conn.commit()


# ── Status classification ─────────────────────────────────────────────────────

def test_status_on_track_band():
    from pacing.brain import _classify
    assert _classify(Decimal("0")) == "on-track"
    assert _classify(Decimal("4.99")) == "on-track"
    assert _classify(Decimal("-4.99")) == "on-track"


def test_status_ahead():
    from pacing.brain import _classify
    assert _classify(Decimal("5.01")) == "ahead"
    assert _classify(Decimal("50")) == "ahead"


def test_status_behind():
    from pacing.brain import _classify
    assert _classify(Decimal("-5.01")) == "behind"
    assert _classify(Decimal("-50")) == "behind"


# ── Period boundary ───────────────────────────────────────────────────────────

def test_orders_outside_period_excluded(conn):
    """Orders from last month should not count toward this month's goal."""
    gid = _seed_goal(conn, target=150000)
    oid = str(uuid.uuid4())
    last_month = date.today().replace(day=1) - timedelta(days=1)
    _seed_order(conn, oid, 50000, last_month)
    try:
        from pacing.brain import compute_pacing_state
        state = compute_pacing_state(gid)
        assert state.period_to_date_value == Decimal("0.00")
    finally:
        _cleanup_goal(conn, gid)
        _cleanup_perf(conn, oid)
