"""Tests for ingestion/shopify.py — to_performance_rows + dedup logic."""
import pytest
from datetime import datetime, timezone
from decimal import Decimal


def _make_order(order_id="ord_001", revenue="49.95", subtotal="39.95"):
    from ingestion.shopify import OrderRecord
    return OrderRecord(
        id=f"gid://shopify/Order/{order_id}",
        name=f"#{order_id}",
        created_at=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 14, 13, 0, 0, tzinfo=timezone.utc),
        financial_status="PAID",
        source_name="web",
        currency="USD",
        customer_id="gid://shopify/Customer/999",
        customer_email="test@example.com",
        line_items_count=2,
        current_total_price=Decimal(revenue),
        subtotal_price=Decimal(subtotal),
    )


_SINCE = datetime(2026, 5, 14, 0, 0, 0, tzinfo=timezone.utc)
_UNTIL = datetime(2026, 5, 15, 0, 0, 0, tzinfo=timezone.utc)


# ── to_performance_rows ───────────────────────────────────────────────────────

def test_emits_two_rows_per_order():
    from ingestion.shopify import to_performance_rows
    orders = [_make_order()]
    rows = to_performance_rows(orders, _SINCE, _UNTIL)
    assert len(rows) == 2


def test_row_metric_names():
    from ingestion.shopify import to_performance_rows
    orders = [_make_order()]
    rows = to_performance_rows(orders, _SINCE, _UNTIL)
    names = {r["metric_name"] for r in rows}
    assert names == {"order_revenue", "gross_sales"}


def test_order_revenue_is_net_price():
    from ingestion.shopify import to_performance_rows
    orders = [_make_order(revenue="49.95")]
    rows = to_performance_rows(orders, _SINCE, _UNTIL)
    rev = next(r for r in rows if r["metric_name"] == "order_revenue")
    assert rev["metric_value"] == Decimal("49.95")


def test_gross_sales_is_subtotal():
    from ingestion.shopify import to_performance_rows
    orders = [_make_order(subtotal="39.95")]
    rows = to_performance_rows(orders, _SINCE, _UNTIL)
    gross = next(r for r in rows if r["metric_name"] == "gross_sales")
    assert gross["metric_value"] == Decimal("39.95")


def test_dimensions_contain_order_id():
    from ingestion.shopify import to_performance_rows
    orders = [_make_order("12345")]
    rows = to_performance_rows(orders, _SINCE, _UNTIL)
    for r in rows:
        assert "gid://shopify/Order/12345" in r["dimensions"]["order_id"]


def test_dimensions_contain_created_at():
    from ingestion.shopify import to_performance_rows
    orders = [_make_order()]
    rows = to_performance_rows(orders, _SINCE, _UNTIL)
    for r in rows:
        assert "created_at" in r["dimensions"]
        assert "2026-05-14" in r["dimensions"]["created_at"]


def test_source_is_shopify():
    from ingestion.shopify import to_performance_rows
    orders = [_make_order()]
    rows = to_performance_rows(orders, _SINCE, _UNTIL)
    for r in rows:
        assert r["source"] == "shopify"


def test_window_start_end_set():
    from ingestion.shopify import to_performance_rows
    orders = [_make_order()]
    rows = to_performance_rows(orders, _SINCE, _UNTIL)
    for r in rows:
        assert r["window_start"] == _SINCE
        assert r["window_end"] == _UNTIL


def test_multiple_orders_emit_multiple_rows():
    from ingestion.shopify import to_performance_rows
    orders = [_make_order("1"), _make_order("2"), _make_order("3")]
    rows = to_performance_rows(orders, _SINCE, _UNTIL)
    assert len(rows) == 6


def test_zero_revenue_order():
    from ingestion.shopify import to_performance_rows
    orders = [_make_order(revenue="0.00", subtotal="0.00")]
    rows = to_performance_rows(orders, _SINCE, _UNTIL)
    rev = next(r for r in rows if r["metric_name"] == "order_revenue")
    assert rev["metric_value"] == Decimal("0.00")


# ── Dedup logic (via DB query pattern) ───────────────────────────────────────

def test_dedup_same_order_latest_wins():
    """Simulate the dedup SQL: same order_id, different updated_at — latest wins.

    We don't call the DB here; we test the to_performance_rows output structure
    that makes the SQL dedup work correctly (order_id in dimensions).
    """
    from ingestion.shopify import to_performance_rows

    # First ingest: $49.95 (order created)
    order_v1 = _make_order("refund_test", revenue="49.95")

    # Second ingest: $35.00 (partial refund applied)
    from ingestion.shopify import OrderRecord
    from datetime import timedelta
    order_v2 = OrderRecord(
        id="gid://shopify/Order/refund_test",
        name="#refund_test",
        created_at=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 14, 16, 0, 0, tzinfo=timezone.utc),  # later
        financial_status="PARTIALLY_REFUNDED",
        source_name="web",
        currency="USD",
        customer_id=None,
        customer_email=None,
        line_items_count=2,
        current_total_price=Decimal("35.00"),
        subtotal_price=Decimal("39.95"),
    )

    rows_v1 = to_performance_rows([order_v1], _SINCE, _UNTIL)
    rows_v2 = to_performance_rows([order_v2], _SINCE, _UNTIL)

    # Both rows have same order_id in dimensions
    oid_v1 = rows_v1[0]["dimensions"]["order_id"]
    oid_v2 = rows_v2[0]["dimensions"]["order_id"]
    assert oid_v1 == oid_v2  # same key — dedup SQL will pick the latest

    # v2 has the lower (refunded) revenue
    rev_v2 = next(r for r in rows_v2 if r["metric_name"] == "order_revenue")
    assert rev_v2["metric_value"] == Decimal("35.00")


# ── parse_order helper ────────────────────────────────────────────────────────

def test_parse_order_decimalizes_price():
    """_parse_order should produce Decimal amounts, not floats."""
    from ingestion.shopify import _parse_order
    node = {
        "id": "gid://shopify/Order/1",
        "name": "#1",
        "createdAt": "2026-05-14T12:00:00Z",
        "updatedAt": "2026-05-14T13:00:00Z",
        "displayFinancialStatus": "PAID",
        "sourceName": "web",
        "currencyCode": "USD",
        "customer": None,
        "currentTotalPriceSet": {"shopMoney": {"amount": "49.95", "currencyCode": "USD"}},
        "subtotalPriceSet": {"shopMoney": {"amount": "39.95"}},
        "lineItems": {"nodes": []},
    }
    record = _parse_order(node)
    assert isinstance(record.current_total_price, Decimal)
    assert record.current_total_price == Decimal("49.95")
    assert isinstance(record.subtotal_price, Decimal)
