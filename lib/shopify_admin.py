"""Minimal Shopify Admin GraphQL client.

Reads SHOPIFY_SHOP_DOMAIN and SHOPIFY_ACCESS_TOKEN from environment.
Defaults to API version 2025-10 (overridable via SHOPIFY_ADMIN_API_VERSION).
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

import httpx


def _config() -> tuple[str, str, str]:
    shop = os.environ.get("SHOPIFY_SHOP_DOMAIN")
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    if not shop or not token:
        raise RuntimeError(
            "SHOPIFY_SHOP_DOMAIN and SHOPIFY_ACCESS_TOKEN must be set in Replit Secrets."
        )
    api_version = os.environ.get("SHOPIFY_ADMIN_API_VERSION", "2025-10")
    url = f"https://{shop}/admin/api/{api_version}/graphql.json"
    return url, token, api_version


def graphql(query: str, variables: Optional[dict[str, Any]] = None,
            timeout_seconds: float = 30.0) -> dict[str, Any]:
    """Execute a GraphQL query/mutation. Returns the `data` payload.

    Raises RuntimeError on HTTP errors or top-level GraphQL errors.
    Does NOT raise on `userErrors` inside individual mutations — caller checks those.
    """
    url, token, _ = _config()
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {"query": query, "variables": variables or {}}

    with httpx.Client(timeout=timeout_seconds) as client:
        try:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body_preview = e.response.text[:500] if e.response.text else "(no body)"
            raise RuntimeError(
                f"Shopify GraphQL HTTP {e.response.status_code}: {body_preview}"
            ) from e

    data = resp.json()
    if "errors" in data and data["errors"]:
        raise RuntimeError(f"Shopify GraphQL errors: {json.dumps(data['errors'])}")
    return data.get("data") or {}
