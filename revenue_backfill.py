"""
Revenue backfill — pulls actual performance for dispatched campaigns
after the 72h attribution window closes.

Runs daily at 9am ET (add to cron_dispatch or app/main.py cron_loop).
Finds campaigns dispatched 3+ days ago that haven't been finalized.
Pulls revenue, recipients, open rate, RPR from Klaviyo.
Updates calendar_executions with actual data.

Usage:
    from workers.revenue_backfill import run_backfill
    run_backfill()
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import httpx

CONVERSION_METRIC_ID = "X93gjq"  # Placed Order


def _klaviyo_headers() -> dict:
    return {
        "Authorization": "Klaviyo-API-Key " + os.environ.get("KLAVIYO_API_KEY", ""),
        "revision": "2025-10-15",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _pull_campaign_performance(campaign_id: str) -> dict | None:
    """
    Pull performance for a single campaign from Klaviyo Reporting API.
    Returns {revenue, recipients, open_rate, rpr} or None on failure.
    """
    url = "https://a.klaviyo.com/api/campaign-values-reports/"
    payload = {
        "data": {
            "type": "campaign-values-report",
            "attributes": {
                "statistics": ["recipients", "open_rate", "click_rate", "conversion_rate"],
                "timeframe": {"key": "last_30_days"},
                "conversion_metric_id": CONVERSION_METRIC_ID,
                "filter": f"equals(campaign_id,\"{campaign_id}\")",
            }
        }
    }

    # Use the simpler campaign report endpoint with filter
    report_url = "https://a.klaviyo.com/api/campaign-values-reports/"
    report_payload = {
        "data": {
            "type": "campaign-values-report",
            "attributes": {
                "statistics": ["recipients", "open_rate", "click_rate", "conversion_rate"],
                "value_statistics": ["revenue_per_recipient", "conversion_value"],
                "timeframe": {"key": "last_30_days"},
                "conversion_metric_id": CONVERSION_METRIC_ID,
                "filter": f"equals(campaign_id,\"{campaign_id}\")",
            }
        }
    }

    try:
        resp = httpx.post(report_url, headers=_klaviyo_headers(), json=report_payload, timeout=30)
        if resp.status_code != 200:
            print(f"[backfill] Klaviyo report failed for {campaign_id}: {resp.status_code}")
            return None

        results = resp.json().get("data", {}).get("attributes", {}).get("results", [])
        if not results:
            print(f"[backfill] No results for campaign {campaign_id}")
            return None

        stats = results[0].get("statistics", {})
        return {
            "revenue": stats.get("conversion_value", 0),
            "recipients": stats.get("recipients", 0),
            "open_rate": stats.get("open_rate", 0),
            "rpr": stats.get("revenue_per_recipient", 0),
            "click_rate": stats.get("click_rate", 0),
        }
    except Exception as e:
        print(f"[backfill] Error pulling campaign {campaign_id}: {e}")
        return None


def run_backfill() -> str:
    """
    Main backfill function. Call from cron.

    Finds calendar_executions where:
    - status = 'dispatched' or 'completed'
    - slot_date <= 3 days ago (72h attribution window)
    - is_preliminary = true (not yet finalized)
    - klaviyo_campaign_id is not null

    Pulls actual performance from Klaviyo and updates the row.
    """
    from db.connection import get_conn

    cutoff = date.today() - timedelta(days=3)

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, klaviyo_campaign_id, slot_date, content_type, audience
               FROM calendar_executions
               WHERE slot_date <= %s
                 AND status IN ('dispatched', 'completed')
                 AND (is_preliminary = true OR is_preliminary IS NULL)
                 AND klaviyo_campaign_id IS NOT NULL""",
            (cutoff,)
        ).fetchall()

        if not rows:
            print("[backfill] No campaigns to backfill.")
            return "nothing_to_backfill"

        print(f"[backfill] Found {len(rows)} campaigns to backfill...")

        updated = 0
        failed = 0
        for row in rows:
            exec_id, campaign_id, slot_date, ct, audience = row
            print(f"[backfill]   {audience}/{ct} on {slot_date} (campaign {campaign_id[:12]}...)")

            perf = _pull_campaign_performance(campaign_id)
            if not perf:
                failed += 1
                continue

            conn.execute(
                """UPDATE calendar_executions
                   SET actual_revenue = %s,
                       recipients = %s,
                       actual_rpr = %s,
                       is_preliminary = false,
                       finalized_at = NOW(),
                       status = 'completed',
                       notes = COALESCE(notes, '') || ' | backfill: $' || %s::text || ' rev, ' || %s::text || ' recip, ' || %s::text || ' rpr'
                   WHERE id = %s""",
                (perf["revenue"], perf["recipients"], perf["rpr"],
                 round(perf["revenue"], 2), perf["recipients"], round(perf["rpr"], 4),
                 exec_id)
            )
            updated += 1

        conn.commit()
        summary = f"Backfilled {updated}/{len(rows)} campaigns. {failed} failed to pull."
        print(f"[backfill] {summary}")
        return summary
