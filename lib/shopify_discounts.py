"""Shopify discount creation for Tonight's Anchor sends.

`create_anchor_discount` creates a fixed-amount Shopify discount code via
`discountCodeBasicCreate`, scoped to a single collection. Idempotent: if a
code with the same name already exists, the existing one is fetched and
returned instead of raising.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from lib.shopify_admin import graphql


_CREATE_MUTATION = """
mutation discountCodeBasicCreate($input: DiscountCodeBasicInput!) {
  discountCodeBasicCreate(basicCodeDiscount: $input) {
    codeDiscountNode {
      id
      codeDiscount {
        ... on DiscountCodeBasic {
          title
          startsAt
          endsAt
          codes(first: 1) { nodes { code } }
          customerGets {
            value {
              ... on DiscountAmount {
                amount { amount currencyCode }
                appliesOnEachItem
              }
            }
          }
        }
      }
    }
    userErrors { field code message }
  }
}
"""


_LOOKUP_QUERY = """
query findDiscountByCode($code: String!) {
  codeDiscountNodeByCode(code: $code) {
    id
    codeDiscount {
      ... on DiscountCodeBasic {
        title
        startsAt
        endsAt
        codes(first: 1) { nodes { code } }
      }
    }
  }
}
"""


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lookup_existing(code: str) -> dict[str, Any] | None:
    data = graphql(_LOOKUP_QUERY, {"code": code})
    node = (data or {}).get("codeDiscountNodeByCode")
    if not node:
        return None
    cd = node.get("codeDiscount") or {}
    codes = ((cd.get("codes") or {}).get("nodes") or [{}])
    return {
        "code":      (codes[0] or {}).get("code") or code,
        "startsAt":  cd.get("startsAt"),
        "endsAt":    cd.get("endsAt"),
        "admin_gid": node.get("id"),
    }


def create_anchor_discount(
    *,
    amount: str,
    starts_at: datetime,
    ends_at: datetime,
    collection_gid: str,
    issue_number: int,
    discount_code: str | None = None,
) -> dict[str, Any]:
    """Create (or fetch) a fixed-amount Tonight's Anchor discount.

    `discount_code` defaults to f"ANCHOR{int(amount)}" (e.g. ANCHOR20). Operator
    may override (e.g. "ANCHOR20_I2"). On code collision the existing discount
    is returned unchanged — caller is responsible for verifying the existing
    window matches what they wanted.

    Returns:
        {"code": str, "startsAt": ISO, "endsAt": ISO, "admin_gid": gid|None}
    """
    code = discount_code or f"ANCHOR{int(float(amount))}"

    existing = _lookup_existing(code)
    if existing:
        return existing

    variables = {
        "input": {
            "title": f"Tonight's Anchor — Issue {issue_number}",
            "code":  code,
            "startsAt": _iso(starts_at),
            "endsAt":   _iso(ends_at),
            "customerSelection": {"all": True},
            "customerGets": {
                "value": {
                    "discountAmount": {
                        "amount": str(amount),
                        "appliesOnEachItem": False,
                    },
                },
                "items": {
                    "collections": {
                        "add": [collection_gid],
                    },
                },
            },
            "appliesOncePerCustomer": True,
            "usageLimit": None,
        },
    }
    data = graphql(_CREATE_MUTATION, variables)
    result = (data or {}).get("discountCodeBasicCreate") or {}
    errors = result.get("userErrors") or []
    if errors:
        # Race condition: another caller may have created the code between
        # the lookup and the mutation. Re-lookup before giving up.
        existing = _lookup_existing(code)
        if existing:
            return existing
        raise RuntimeError(
            f"discountCodeBasicCreate failed for {code!r}: {errors}"
        )

    node = result.get("codeDiscountNode") or {}
    cd = node.get("codeDiscount") or {}
    codes = ((cd.get("codes") or {}).get("nodes") or [{}])
    return {
        "code":      (codes[0] or {}).get("code") or code,
        "startsAt":  cd.get("startsAt") or _iso(starts_at),
        "endsAt":    cd.get("endsAt") or _iso(ends_at),
        "admin_gid": node.get("id"),
    }
