"""Audience health monitor — runs daily at 7:40am ET.

For every tracked audience:
  - Days since last send
  - 30d + 90d RPR
  - Trend (↑/↓)
  - Health flags: STALE, AT_RISK, HEALTHY

Writes to agent_state key 'audience_health'.
Posts Slack alert for STALE audiences (money on the table).
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from db.connection import get_conn

CUSTOMER_SEGMENTS = {
    "lapsed_30d", "lapsed_60d", "lapsed_60_90d", "lapsed_90d", "lapsed_90_180d",
    "lapsed_180d", "lapsed_180d_plus", "winback_180d",
    "vip", "inner_circle", "engaged_customers", "all_customers",
    "active_seal", "active_subscribers", "whales", "high_aov",
    "one_time_buyers", "otb", "cart_abandoners",
}
PROSPECT_SEGMENTS = {"engaged_prospects", "super_engaged"}
ALL_AUDIENCES = CUSTOMER_SEGMENTS | PROSPECT_SEGMENTS

# Estimated list sizes (fallback — live data preferred from Klaviyo ingestion)
FALLBACK_LIST_SIZE = {
    "active_seal": 511, "whales": 1038, "lapsed_30d": 3618, "vip": 5424,
    "engaged_customers": 13340, "one_time_buyers": 12951, "engaged_prospects": 12002,
    "super_engaged": 4447, "lapsed_60d": 4000, "lapsed_90d": 5000,
    "lapsed_180d": 8000, "inner_circle": 800, "all_customers": 20000,
}


def run_audience_health() -> list[dict]:
    today = date.today()
    ninety_ago = today - timedelta(days=90)
    thirty_ago = today - timedelta(days=30)

    with get_conn() as conn:
        # Pull all send history per audience
        rows = conn.execute(
            """SELECT audience,
                      MAX(slot_date) as last_send,
                      AVG(actual_rpr) FILTER (WHERE actual_rpr > 0 AND slot_date > %s) as rpr_90d,
                      AVG(actual_rpr) FILTER (WHERE actual_rpr > 0 AND slot_date > %s) as rpr_30d,
                      COUNT(*) FILTER (WHERE slot_date > %s AND status IN ('dispatched','completed')) as sends_90d
               FROM calendar_executions
               WHERE status IN ('dispatched','completed') AND audience IS NOT NULL
               GROUP BY audience""",
            (ninety_ago, thirty_ago, ninety_ago)
        ).fetchall()

    db_map = {r[0]: {
        "last_send": r[1],
        "rpr_90d": float(r[2] or 0),
        "rpr_30d": float(r[3] or 0),
        "sends_90d": int(r[4] or 0),
    } for r in rows}

    results = []
    stale_alerts = []

    for audience in sorted(ALL_AUDIENCES):
        data = db_map.get(audience, {})
        last_send = data.get("last_send")
        days_since = (today - last_send).days if last_send else 999
        rpr_90d = data.get("rpr_90d", 0)
        rpr_30d = data.get("rpr_30d", 0)
        sends_90d = data.get("sends_90d", 0)
        list_size = FALLBACK_LIST_SIZE.get(audience, 1000)
        estimated_send_value = rpr_90d * list_size

        # Trend
        if rpr_30d > 0 and rpr_90d > 0:
            trend = "up" if rpr_30d > rpr_90d * 1.05 else ("down" if rpr_30d < rpr_90d * 0.9 else "flat")
        else:
            trend = "unknown"

        # Health flags
        flags = []
        if days_since < 7:
            health = "RECENT"
        elif days_since < 14:
            health = "WARM"
        elif days_since >= 21 and rpr_90d >= 0.10:
            health = "STALE"
            flags.append("STALE")
            stale_alerts.append({
                "audience": audience,
                "days_since": days_since,
                "rpr_90d": rpr_90d,
                "estimated_send_value": estimated_send_value,
            })
        else:
            health = "FRESH"

        if rpr_90d >= 0.20 and rpr_30d < 0.10 and sends_90d >= 3:
            flags.append("AT_RISK")
            if health == "FRESH":
                health = "AT_RISK"

        results.append({
            "audience": audience,
            "last_send": str(last_send) if last_send else "Never",
            "days_since": days_since,
            "rpr_90d": round(rpr_90d, 4),
            "rpr_30d": round(rpr_30d, 4),
            "sends_90d": sends_90d,
            "trend": trend,
            "health": health,
            "flags": flags,
            "estimated_send_value": round(estimated_send_value, 0),
        })

    # Sort by revenue opportunity descending
    results.sort(key=lambda x: x["estimated_send_value"], reverse=True)

    # Write to agent_state
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO agent_state (key, value, updated_at) VALUES ('audience_health', %s, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                (json.dumps(results),)
            )
            conn.commit()
    except Exception as e:
        print(f"[audience_health] write error: {e}")

    # Post Slack alerts for stale high-value audiences
    if stale_alerts:
        _post_stale_alerts(stale_alerts)

    print(f"[audience_health] {len(results)} audiences analyzed, {len(stale_alerts)} STALE")
    return results


def _post_stale_alerts(stale: list[dict]) -> None:
    from lib.slack import _post
    lines = ["💤 *Audience freshness alert:*"]
    for a in stale[:5]:
        lines.append(
            f"  • *{a['audience']}* hasn't been sent to in {a['days_since']} days "
            f"(avg ${a['rpr_90d']:.3f} RPR · est. ${a['estimated_send_value']:,.0f}/send). "
            "Calendar has a gap."
        )
    _post({"blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}]})
