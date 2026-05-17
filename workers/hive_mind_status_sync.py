"""Hive Mind issue status sync — marks issues 'published' when Klaviyo campaign is Sent.

Runs at 9:10pm daily. For each issue with status='scheduled' and a klaviyo_campaign_id,
checks Klaviyo for campaign status. If the campaign shows 'Sent', marks the issue
status='published' and sets published_at to the Klaviyo send time.

After any updates, refreshes the SSH featured box so sleep-science-hub shows the
latest actually-sent issue (not just the latest draft).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

KLAVIYO_API_KEY = os.environ.get("KLAVIYO_API_KEY", "")
_HEADERS = {
    "Authorization": f"Klaviyo-API-Key {KLAVIYO_API_KEY}",
    "revision": "2025-10-15",
    "Content-Type": "application/json",
}


def _get_campaign_status(campaign_id: str) -> tuple[str, str | None]:
    """Return (status, send_time_iso) for a Klaviyo campaign.

    send_time_iso is None if the campaign hasn't been sent yet.
    """
    try:
        resp = httpx.get(
            f"https://a.klaviyo.com/api/campaigns/{campaign_id}/",
            headers=_HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"[status_sync] Klaviyo campaign {campaign_id} returned {resp.status_code}")
            return "", None
        data = resp.json().get("data", {})
        attrs = data.get("attributes", {})
        status = attrs.get("status", "")
        # scheduled_at is the send time for sent campaigns
        send_time = attrs.get("scheduled_at") or attrs.get("send_time")
        return status, send_time
    except Exception as exc:
        print(f"[status_sync] error checking campaign {campaign_id}: {exc}")
        return "", None


def sync_sent_campaigns() -> int:
    """Check all scheduled Hive Mind issues; mark sent ones as published.

    Returns the number of issues newly marked published.
    """
    if not KLAVIYO_API_KEY:
        print("[status_sync] KLAVIYO_API_KEY not set — skipping")
        return 0

    try:
        from db.connection import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT number, klaviyo_campaign_id
                   FROM issues
                   WHERE status = 'scheduled'
                     AND klaviyo_campaign_id IS NOT NULL"""
            ).fetchall()
    except Exception as exc:
        print(f"[status_sync] DB query failed: {exc}")
        return 0

    if not rows:
        print("[status_sync] No scheduled issues with campaign IDs — nothing to check")
        return 0

    updated = 0
    for number, campaign_id in rows:
        status, send_time = _get_campaign_status(campaign_id)
        if status.lower() != "sent":
            continue

        # Parse send time — fall back to NOW() if not returned
        if send_time:
            try:
                published_at = datetime.fromisoformat(send_time.replace("Z", "+00:00"))
            except ValueError:
                published_at = datetime.now(timezone.utc)
        else:
            published_at = datetime.now(timezone.utc)

        try:
            from db.connection import get_conn
            with get_conn() as conn:
                conn.execute(
                    """UPDATE issues
                       SET status = 'published', published_at = %s
                       WHERE number = %s""",
                    (published_at, number),
                )
                conn.commit()
            print(f"[status_sync] Issue {number:03d} marked published (sent {published_at.date()})")
            updated += 1
        except Exception as exc:
            print(f"[status_sync] DB update failed for Issue {number}: {exc}")

    if updated:
        print(f"[status_sync] {updated} issue(s) newly published — refreshing SSH featured box")
        try:
            from workers.hub_updater import refresh_ssh_featured_issue
            result = refresh_ssh_featured_issue()
            print(f"[status_sync] SSH featured box: {result}")
        except Exception as exc:
            print(f"[status_sync] SSH refresh failed: {exc}")
    else:
        print("[status_sync] No issues transitioned to Sent — SSH featured box unchanged")

    return updated
