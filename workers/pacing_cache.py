"""Refresh pacing cache in agent_state — called by cron, not dashboard."""
import httpx, os, json

def refresh_pacing_cache():
    headers = {
        "Authorization": "Klaviyo-API-Key " + os.environ.get("KLAVIYO_API_KEY", ""),
        "revision": "2025-10-15",
        "Content-Type": "application/json",
    }
    campaign_rev = 0.0
    flow_rev = 0.0
    campaign_count = 0

    try:
        resp = httpx.post("https://a.klaviyo.com/api/campaign-values-reports/", headers=headers, timeout=30, json={
            "data": {"type": "campaign-values-report", "attributes": {
                "statistics": ["recipients", "conversion_value"],
                "timeframe": {"key": "this_month"},
                "conversion_metric_id": "X93gjq",
            }}
        })
        if resp.status_code == 200:
            for r in resp.json().get("data", {}).get("attributes", {}).get("results", []):
                campaign_rev += float(r.get("statistics", {}).get("conversion_value", 0))
                campaign_count += 1
    except Exception as e:
        print(f"[pacing_cache] campaign pull failed: {e}")

    try:
        resp = httpx.post("https://a.klaviyo.com/api/flow-values-reports/", headers=headers, timeout=30, json={
            "data": {"type": "flow-values-report", "attributes": {
                "statistics": ["recipients", "conversion_value"],
                "timeframe": {"key": "this_month"},
                "conversion_metric_id": "X93gjq",
            }}
        })
        if resp.status_code == 200:
            for f in resp.json().get("data", {}).get("attributes", {}).get("flow_aggregation", []):
                flow_rev += float(f.get("statistics", {}).get("conversion_value", 0))
    except Exception as e:
        print(f"[pacing_cache] flow pull failed: {e}")

    from db.connection import get_conn
    with get_conn() as conn:
        cache = json.dumps({
            "campaign_rev": campaign_rev,
            "flow_rev": flow_rev,
            "total": campaign_rev + flow_rev,
            "campaign_count": campaign_count,
        })
        conn.execute(
            "INSERT INTO agent_state (key, value, updated_at) VALUES ('pacing_cache', %s, NOW()) ON CONFLICT (key) DO UPDATE SET value = %s, updated_at = NOW()",
            (cache, cache)
        )
        conn.commit()
    print(f"[pacing_cache] Cached: campaigns ${campaign_rev:,.2f}, flows ${flow_rev:,.2f}")
