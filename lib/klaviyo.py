"""
lib/klaviyo.py
Minimal Klaviyo REST helpers.

Single responsibility: profile lookup + property updates.
Used by workers that need to write back to a customer's Klaviyo profile.
"""

import os
import requests


KLAVIYO_API_BASE = "https://a.klaviyo.com"
KLAVIYO_REVISION = "2024-10-15"


def _headers() -> dict:
    return {
        "Authorization": f"Klaviyo-API-Key {os.environ['KLAVIYO_API_KEY']}",
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
        "revision": KLAVIYO_REVISION,
    }


def get_profile_id_by_email(email: str) -> str | None:
    """Return the first profile id matching `email`, or None if none found."""
    resp = requests.get(
        f"{KLAVIYO_API_BASE}/api/profiles",
        params={"filter": f'equals(email,"{email}")'},
        headers=_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    if not data:
        return None
    return data[0]["id"]


def update_profile_properties(profile_id: str, properties: dict) -> None:
    """PATCH custom properties onto a Klaviyo profile."""
    resp = requests.patch(
        f"{KLAVIYO_API_BASE}/api/profiles/{profile_id}/",
        json={
            "data": {
                "type": "profile",
                "id": profile_id,
                "attributes": {"properties": properties},
            }
        },
        headers=_headers(),
        timeout=15,
    )
    resp.raise_for_status()
