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
            results = resp.json().get("data", {}).get("attributes", {}).get("results", [])
            print(f"[pacing_cache] campaign results: {len(results)} rows")
            for r in results:
                val = float(r.get("statistics", {}).get("conversion_value", 0))
                campaign_rev += val
                campaign_count += 1
                if val > 0:
                    print(f"[pacing_cache]   campaign {r.get('id','?')}: ${val:,.2f}")
        else:
            print(f"[pacing_cache] campaign pull HTTP {resp.status_code}: {resp.text[:200]}")
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
            results = resp.json().get("data", {}).get("attributes", {}).get("results", [])
            print(f"[pacing_cache] flow results: {len(results)} rows")
            for r in results:
                val = float(r.get("statistics", {}).get("conversion_value", 0))
                flow_rev += val
                if val > 0:
                    print(f"[pacing_cache]   flow {r.get('id','?')}: ${val:,.2f}")
        else:
            print(f"[pacing_cache] flow pull HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"[pacing_cache] flow pull failed: {e}")

    from db.connection import get_conn
    from datetime import datetime, timezone
    with get_conn() as conn:
        cache = json.dumps({
            "campaign_rev": campaign_rev,
            "flow_rev": flow_rev,
            "total": campaign_rev + flow_rev,
            "campaign_count": campaign_count,
            "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        })
        conn.execute(
            "INSERT INTO agent_state (key, value, updated_at) VALUES ('pacing_cache', %s, NOW()) ON CONFLICT (key) DO UPDATE SET value = %s, updated_at = NOW()",
            (cache, cache)
        )
        conn.commit()
    print(f"[pacing_cache] Cached: campaigns ${campaign_rev:,.2f}, flows ${flow_rev:,.2f}")
