"""Shopify performance ingestion via Admin GraphQL API.

Pulls orders for a (since, until) window and converts each into 1-2
`performance` rows: `order_revenue` (currentTotalPriceSet — net, post-refund)
and `gross_sales` (subtotalPriceSet — pre-discount/shipping/tax).

Honors:
  - Cost-based throttle (errors[].extensions.code == 'THROTTLED' / MAX_COST_EXCEEDED)
  - HTTP 429 with Retry-After
  - Cursor-based pagination (orders connection, sorted by updated_at)

The orders connection is filtered by `updated_at` so that refunds, cancellations,
and other post-creation mutations re-emit the order in a later window. Downstream
consumers should dedupe by `dimensions->>'order_id'` and take the latest row.

This module does NOT touch the database. `ingestion.sync` owns the transaction.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterator

import requests

import config

logger = logging.getLogger(__name__)

# Bump this when Shopify ships a new stable release we want to adopt.
# Versions live for 12 months; release cadence is quarterly (Jan/Apr/Jul/Oct).
SHOPIFY_API_VERSION = "2025-10"

ORDERS_PAGE_SIZE = 250  # max for the orders connection
LINE_ITEMS_PAGE_SIZE = 250  # cap on line_items_count (>250 line items per order is rare)
HTTP_TIMEOUT_SECONDS = 30
MAX_RETRIES = 5

ORDERS_QUERY = """
query ShopifyOrders($cursor: String, $query: String!) {
  orders(first: %d, after: $cursor, query: $query, sortKey: UPDATED_AT) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      id
      name
      createdAt
      updatedAt
      displayFinancialStatus
      sourceName
      currencyCode
      currentTotalPriceSet {
        shopMoney { amount currencyCode }
      }
      subtotalPriceSet {
        shopMoney { amount }
      }
      customer {
        id
        email
      }
      lineItems(first: %d) {
        nodes { id }
      }
    }
  }
}
""" % (ORDERS_PAGE_SIZE, LINE_ITEMS_PAGE_SIZE)


@dataclass(frozen=True)
class OrderRecord:
    id: str  # Shopify GID, e.g. "gid://shopify/Order/4567890123"
    name: str  # e.g. "#1001"
    created_at: datetime
    updated_at: datetime
    financial_status: str | None
    source_name: str | None
    currency: str
    customer_id: str | None
    customer_email: str | None
    line_items_count: int
    current_total_price: Decimal  # net, post-refund (currentTotalPriceSet)
    subtotal_price: Decimal  # pre-discount/shipping/tax (subtotalPriceSet)


class ShopifyAPIError(RuntimeError):
    pass


def _endpoint() -> str:
    domain = config.SHOPIFY_SHOP_DOMAIN
    if not domain:
        raise RuntimeError("SHOPIFY_SHOP_DOMAIN is not set in env / Replit Secrets")
    if not config.SHOPIFY_ACCESS_TOKEN:
        raise RuntimeError("SHOPIFY_ACCESS_TOKEN is not set in env / Replit Secrets")
    # Tolerate either "mystore.myshopify.com" or "https://mystore.myshopify.com".
    domain = domain.replace("https://", "").replace("http://", "").rstrip("/")
    return f"https://{domain}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"


def _headers() -> dict[str, str]:
    return {
        "X-Shopify-Access-Token": config.SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _iso_utc(dt: datetime) -> str:
    """Shopify accepts ISO 8601 with 'Z' suffix in its search syntax."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_query_filter(since: datetime, until: datetime) -> str:
    return f"updated_at:>='{_iso_utc(since)}' updated_at:<'{_iso_utc(until)}'"


def _post_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """POST a GraphQL request, retrying on 429 and on cost-throttle errors."""
    url = _endpoint()
    headers = _headers()
    payload = {"query": query, "variables": variables}

    for attempt in range(1, MAX_RETRIES + 1):
        resp = requests.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)

        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", "2"))
            logger.warning("Shopify 429 — sleeping %.1fs (attempt %d/%d)", retry_after, attempt, MAX_RETRIES)
            time.sleep(retry_after)
            continue

        if resp.status_code >= 500:
            backoff = min(2 ** attempt, 30)
            logger.warning("Shopify %d — sleeping %.1fs (attempt %d/%d)", resp.status_code, backoff, attempt, MAX_RETRIES)
            time.sleep(backoff)
            continue

        if resp.status_code != 200:
            raise ShopifyAPIError(f"Shopify HTTP {resp.status_code}: {resp.text[:500]}")

        body = resp.json()
        errors = body.get("errors") or []
        throttled = any(
            (err.get("extensions") or {}).get("code") in ("THROTTLED", "MAX_COST_EXCEEDED")
            for err in errors
        )
        if throttled:
            cost = (body.get("extensions") or {}).get("cost") or {}
            throttle = cost.get("throttleStatus") or {}
            available = float(throttle.get("currentlyAvailable", 0) or 0)
            restore_rate = float(throttle.get("restoreRate", 50) or 50)
            requested = float(cost.get("requestedQueryCost", 100) or 100)
            sleep_s = max(2.0, (requested - available) / max(restore_rate, 1.0))
            logger.warning("Shopify THROTTLED — sleeping %.1fs (attempt %d/%d)", sleep_s, attempt, MAX_RETRIES)
            time.sleep(sleep_s)
            continue

        if errors:
            raise ShopifyAPIError(f"Shopify GraphQL errors: {errors}")

        return body["data"]

    raise ShopifyAPIError(f"Shopify request exhausted {MAX_RETRIES} retries")


def _parse_order(node: dict[str, Any]) -> OrderRecord:
    customer = node.get("customer") or {}
    total_money = ((node.get("currentTotalPriceSet") or {}).get("shopMoney") or {})
    subtotal_money = ((node.get("subtotalPriceSet") or {}).get("shopMoney") or {})
    line_items = ((node.get("lineItems") or {}).get("nodes") or [])

    return OrderRecord(
        id=node["id"],
        name=node["name"],
        created_at=datetime.fromisoformat(node["createdAt"].replace("Z", "+00:00")),
        updated_at=datetime.fromisoformat(node["updatedAt"].replace("Z", "+00:00")),
        financial_status=node.get("displayFinancialStatus"),
        source_name=node.get("sourceName"),
        currency=node.get("currencyCode") or total_money.get("currencyCode") or "",
        customer_id=customer.get("id"),
        customer_email=customer.get("email"),
        line_items_count=len(line_items),
        current_total_price=Decimal(total_money.get("amount") or "0"),
        subtotal_price=Decimal(subtotal_money.get("amount") or "0"),
    )


def _iter_orders(since: datetime, until: datetime) -> Iterator[OrderRecord]:
    cursor: str | None = None
    query_filter = _build_query_filter(since, until)
    page = 0
    while True:
        page += 1
        data = _post_graphql(ORDERS_QUERY, {"cursor": cursor, "query": query_filter})
        orders_conn = data["orders"]
        nodes = orders_conn["nodes"]
        logger.info("Shopify page %d: %d orders", page, len(nodes))
        for node in nodes:
            yield _parse_order(node)
        page_info = orders_conn["pageInfo"]
        if not page_info["hasNextPage"]:
            return
        cursor = page_info["endCursor"]


def pull_orders(since: datetime, until: datetime) -> list[OrderRecord]:
    """Pull every order updated in [since, until). Pages until exhausted."""
    return list(_iter_orders(since, until))


def to_performance_rows(
    orders: list[OrderRecord],
    since: datetime,
    until: datetime,
) -> list[dict[str, Any]]:
    """Emit 2 performance rows per order: order_revenue + gross_sales.

    Rows are dicts ready for INSERT into the `performance` table (run_id is omitted —
    Shopify ingestion is org-wide, not tied to any specific content run).
    """
    rows: list[dict[str, Any]] = []
    for o in orders:
        dimensions = {
            "order_id": o.id,
            "order_name": o.name,
            "created_at": o.created_at.isoformat(),  # used by pacing brain to scope revenue to a goal period
            "customer_id": o.customer_id,
            "customer_email": o.customer_email,
            "source_name": o.source_name,
            "currency": o.currency,
            "financial_status": o.financial_status,
            "line_items_count": o.line_items_count,
        }
        rows.append({
            "source": "shopify",
            "metric_name": "order_revenue",
            "metric_value": o.current_total_price,
            "dimensions": dimensions,
            "window_start": since,
            "window_end": until,
        })
        rows.append({
            "source": "shopify",
            "metric_name": "gross_sales",
            "metric_value": o.subtotal_price,
            "dimensions": dimensions,
            "window_start": since,
            "window_end": until,
        })
    return rows
