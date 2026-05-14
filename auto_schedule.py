"""
Auto-schedule — after validator passes and Klaviyo campaign is created,
schedule it for the slot's send time. Operator never opens Klaviyo.

Usage:
    from workers.auto_schedule import schedule_campaign
    schedule_campaign(campaign_id, slot)
"""
from __future__ import annotations

import os
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import httpx

NY = ZoneInfo("America/New_York")

# Default send times by content type (EDT hours)
DEFAULT_SEND_TIMES = {
    "klaviyo_campaign": 14,   # 2pm EDT — best RPR
    "hive_mind":        20,   # 8pm EDT
    "sleep_audio":      20,   # 8pm EDT (seal at 8:15pm)
    "sniper_followup":  14,   # 2pm EDT
    "sms_campaign":     12,   # noon EDT
    "seo_blog":         10,   # 10am EDT
}


def _klaviyo_headers() -> dict:
    return {
        "Authorization": "Klaviyo-API-Key " + os.environ.get("KLAVIYO_API_KEY", ""),
        "revision": "2025-10-15",
        "Content-Type": "application/json",
    }


def _compute_send_time(slot: dict) -> str:
    """
    Compute the UTC ISO datetime for when this campaign should send.
    Uses slot's send_time_est if provided, otherwise defaults by content type.
    Returns ISO string like '2026-05-15T18:00:00+00:00'
    """
    slot_date_str = slot.get("date", date.today().isoformat())
    slot_date = date.fromisoformat(slot_date_str)

    # Parse send time from slot (e.g. "14:00" or "20:15")
    time_str = slot.get("send_time_est", "")
    if time_str and ":" in time_str:
        hour = int(time_str.split(":")[0])
        minute = int(time_str.split(":")[1])
    else:
        ct = slot.get("content_type", "klaviyo_campaign")
        hour = DEFAULT_SEND_TIMES.get(ct, 14)
        minute = 0

    # Build EDT datetime → convert to UTC
    local_dt = datetime(slot_date.year, slot_date.month, slot_date.day,
                        hour, minute, 0, tzinfo=NY)
    utc_dt = local_dt.astimezone(ZoneInfo("UTC"))

    # Safety: if the computed time is in the past, push to tomorrow same time
    now_utc = datetime.now(ZoneInfo("UTC"))
    if utc_dt <= now_utc:
        utc_dt = utc_dt + timedelta(days=1)
        print(f"[auto_schedule] Send time was in the past. Pushed to {utc_dt.isoformat()}")

    return utc_dt.strftime("%Y-%m-%dT%H:%M:%S") + "+00:00"


def _update_send_strategy(campaign_id: str, send_time_utc: str) -> bool:
    """
    PATCH the campaign's send strategy to the computed send time.
    This sets the datetime the campaign will actually send at.
    """
    url = "https://a.klaviyo.com/api/campaigns/" + campaign_id
    payload = {
        "data": {
            "type": "campaign",
            "id": campaign_id,
            "attributes": {
                "send_strategy": {
                    "method": "static",
                    "datetime": send_time_utc,
                    "options": {"is_local": False}
                }
            }
        }
    }
    resp = httpx.patch(url, headers=_klaviyo_headers(), json=payload, timeout=30)
    if resp.status_code in (200, 202):
        print(f"[auto_schedule] Send strategy set to {send_time_utc}")
        return True
    else:
        print(f"[auto_schedule] PATCH send_strategy failed: {resp.status_code} {resp.text[:200]}")
        return False


def _trigger_send(campaign_id: str) -> bool:
    """
    POST to campaign-send-jobs to schedule the campaign.
    If send_strategy datetime is in the future, Klaviyo schedules it.
    If in the past, Klaviyo sends immediately.
    """
    url = "https://a.klaviyo.com/api/campaign-send-jobs/"
    payload = {
        "data": {
            "type": "campaign-send-job",
            "id": campaign_id,
        }
    }
    resp = httpx.post(url, headers=_klaviyo_headers(), json=payload, timeout=30)
    if resp.status_code in (200, 202):
        print(f"[auto_schedule] Campaign {campaign_id} scheduled successfully")
        return True
    else:
        print(f"[auto_schedule] Schedule FAILED: {resp.status_code} {resp.text[:300]}")
        return False


def schedule_campaign(campaign_id: str, slot: dict) -> dict:
    """
    Full auto-schedule pipeline:
    1. Compute send time from slot
    2. Update campaign send strategy
    3. Trigger the send job (Klaviyo schedules if future)

    Returns {"scheduled": bool, "send_time": str, "error": str}
    """
    if not campaign_id:
        return {"scheduled": False, "send_time": "", "error": "No campaign_id"}

    send_time = _compute_send_time(slot)
    print(f"[auto_schedule] Scheduling {campaign_id} for {send_time}")

    # Step 1: Set the send time
    if not _update_send_strategy(campaign_id, send_time):
        return {"scheduled": False, "send_time": send_time,
                "error": "Failed to update send strategy"}

    # Step 2: Trigger the send
    if not _trigger_send(campaign_id):
        return {"scheduled": False, "send_time": send_time,
                "error": "Failed to trigger send job. Campaign is in Klaviyo as Draft with correct time — schedule manually."}

    return {"scheduled": True, "send_time": send_time, "error": ""}
