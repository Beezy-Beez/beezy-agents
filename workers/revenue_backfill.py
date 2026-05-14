"""
Revenue backfill — pulls actual performance for dispatched campaigns
after the 72h attribution window closes.

Runs daily at 9am ET (add to cron_dispatch or app/main.py cron_loop).
Finds campaigns dispatched 3+ days ago that haven't been finalized.
Pulls revenue, recipients, open rate, RPR from Klaviyo.
Updates calendar_executions with actual data.
Also updates subject_patterns in agent_state for A/B learning.

Usage:
    from workers.revenue_backfill import run_backfill
    run_backfill()
"""
from __future__ import annotations

import json
import os
import re
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


def _classify_subject(subject: str) -> str:
    """Classify a subject line as 'curiosity' or 'benefit' based on linguistic patterns."""
    s = (subject or "").lower()
    curiosity_signals = ["?", "why ", "what if", "have you", "did you", "do you", "how ", "what's"]
    benefit_signals = ["sleep through", "wake up ", "finally ", "tonight", "better sleep",
                       "rest ", "energy", "feel ", "wake feeling"]
    curiosity_score = sum(1 for sig in curiosity_signals if sig in s)
    benefit_score   = sum(1 for sig in benefit_signals if sig in s)
    return "benefit" if benefit_score > curiosity_score else "curiosity"


def _extract_subject_type_from_notes(notes: str) -> str:
    """Pull subject_type tag from calendar_executions notes field."""
    if not notes:
        return ""
    match = re.search(r"subject_type:(\w+)", notes)
    return match.group(1) if match else ""


def _update_subject_patterns(conn, finalized: list[tuple]) -> None:
    """Update agent_state['subject_patterns'] based on newly-finalized campaign performance.

    finalized: list of (audience, notes, rpr) tuples from the backfill update pass.
    Tracks avg RPR per subject type per audience. Writes winning_type when enough data.
    """
    if not finalized:
        return
    try:
        row = conn.execute("SELECT value FROM agent_state WHERE key='subject_patterns'").fetchone()
        patterns = json.loads(row[0]) if row else {}
    except Exception:
        patterns = {}

    for audience, notes, rpr in finalized:
        if not audience or rpr is None:
            continue
        stype = _extract_subject_type_from_notes(notes or "")
        if not stype:
            continue

        aud_key = (audience or "").lower().replace(" ", "_")
        if aud_key not in patterns:
            patterns[aud_key] = {"curiosity": {"count": 0, "total_rpr": 0.0},
                                  "benefit":   {"count": 0, "total_rpr": 0.0},
                                  "winning_type": None}

        bucket = patterns[aud_key].get(stype)
        if bucket is None:
            continue
        bucket["count"] += 1
        bucket["total_rpr"] += float(rpr or 0)

        # Determine winner when we have ≥3 sends per type
        c_data = patterns[aud_key]["curiosity"]
        b_data = patterns[aud_key]["benefit"]
        if c_data["count"] >= 3 and b_data["count"] >= 3:
            c_avg = c_data["total_rpr"] / c_data["count"]
            b_avg = b_data["total_rpr"] / b_data["count"]
            patterns[aud_key]["winning_type"] = "benefit" if b_avg > c_avg else "curiosity"

    try:
        conn.execute(
            "INSERT INTO agent_state (key, value, updated_at) VALUES ('subject_patterns', %s, NOW()) "
            "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
            (json.dumps(patterns),)
        )
    except Exception as e:
        print(f"[backfill] subject_patterns write error: {e}")


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
        finalized_for_patterns: list[tuple] = []  # (audience, notes, rpr)

        for row in rows:
            exec_id, campaign_id, slot_date, ct, audience = row
            print(f"[backfill]   {audience}/{ct} on {slot_date} (campaign {campaign_id[:12]}...)")

            perf = _pull_campaign_performance(campaign_id)
            if not perf:
                failed += 1
                continue

            # Read current notes before overwriting (subject_type tag lives there)
            notes_row = conn.execute(
                "SELECT notes FROM calendar_executions WHERE id=%s", (exec_id,)
            ).fetchone()
            existing_notes = (notes_row[0] or "") if notes_row else ""

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
            finalized_for_patterns.append((audience, existing_notes, perf["rpr"]))

        _update_subject_patterns(conn, finalized_for_patterns)
        conn.commit()
        summary = f"Backfilled {updated}/{len(rows)} campaigns. {failed} failed to pull."
        print(f"[backfill] {summary}")
        return summary
