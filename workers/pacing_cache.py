"""Refresh pacing cache in agent_state — called by cron, not dashboard."""
import httpx, os, json, time
from config import KLAVIYO_REVISION

_MAX_RETRIES = 5
_REPORT_PACING = 1.5  # seconds between the two values-report calls


def _klaviyo_headers() -> dict:
    return {
        "Authorization": "Klaviyo-API-Key " + os.environ.get("KLAVIYO_API_KEY", ""),
        "revision": KLAVIYO_REVISION,
        "Content-Type": "application/json",
    }


def _post_report(url: str, payload: dict) -> list:
    """POST a Klaviyo values-report with 429/5xx retry. Returns results list or []."""
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = httpx.post(url, headers=_klaviyo_headers(), json=payload, timeout=30)
        except Exception as e:
            backoff = min(2 ** attempt, 30)
            print(f"[pacing_cache] request error (attempt {attempt}/{_MAX_RETRIES}): {e} — retrying in {backoff}s")
            time.sleep(backoff)
            continue

        if resp.status_code == 429:
            wait = float(resp.headers.get("Retry-After", "2"))
            print(f"[pacing_cache] 429 rate-limited — sleeping {wait}s (attempt {attempt}/{_MAX_RETRIES})")
            time.sleep(wait)
            continue

        if resp.status_code >= 500:
            backoff = min(2 ** attempt, 30)
            print(f"[pacing_cache] HTTP {resp.status_code} (attempt {attempt}/{_MAX_RETRIES}) — retrying in {backoff}s")
            time.sleep(backoff)
            continue

        if resp.status_code != 200:
            print(f"[pacing_cache] HTTP {resp.status_code}: {resp.text[:200]}")
            return []

        return resp.json().get("data", {}).get("attributes", {}).get("results", [])

    print(f"[pacing_cache] exhausted {_MAX_RETRIES} retries — returning empty")
    return []


def _write_cache(cache: str, retries: int = 3) -> None:
    """Write pacing cache to agent_state with retry on Neon connection errors."""
    from db.connection import get_conn
    from datetime import datetime, timezone

    for attempt in range(1, retries + 1):
        try:
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO agent_state (key, value, updated_at) VALUES ('pacing_cache', %s, NOW()) "
                    "ON CONFLICT (key) DO UPDATE SET value = %s, updated_at = NOW()",
                    (cache, cache),
                )
                conn.commit()
            return
        except Exception as e:
            backoff = min(2 ** attempt, 15)
            print(f"[pacing_cache] Neon write error (attempt {attempt}/{retries}): {e} — retrying in {backoff}s")
            if attempt < retries:
                time.sleep(backoff)

    print("[pacing_cache] WARN: cache write failed after all retries — dashboard will show stale data")


def refresh_pacing_cache():
    campaign_payload = {
        "data": {"type": "campaign-values-report", "attributes": {
            "statistics": ["recipients", "conversion_value"],
            "timeframe": {"key": "this_month"},
            "conversion_metric_id": "X93gjq",
        }}
    }
    flow_payload = {
        "data": {"type": "flow-values-report", "attributes": {
            "statistics": ["recipients", "conversion_value"],
            "timeframe": {"key": "this_month"},
            "conversion_metric_id": "X93gjq",
        }}
    }

    campaign_rev = 0.0
    flow_rev = 0.0
    campaign_count = 0

    results = _post_report("https://a.klaviyo.com/api/campaign-values-reports/", campaign_payload)
    print(f"[pacing_cache] campaign results: {len(results)} rows")
    for r in results:
        val = float(r.get("statistics", {}).get("conversion_value", 0))
        campaign_rev += val
        campaign_count += 1
        if val > 0:
            print(f"[pacing_cache]   campaign {r.get('id', '?')}: ${val:,.2f}")

    time.sleep(_REPORT_PACING)

    results = _post_report("https://a.klaviyo.com/api/flow-values-reports/", flow_payload)
    print(f"[pacing_cache] flow results: {len(results)} rows")
    for r in results:
        val = float(r.get("statistics", {}).get("conversion_value", 0))
        flow_rev += val
        if val > 0:
            print(f"[pacing_cache]   flow {r.get('id', '?')}: ${val:,.2f}")

    from datetime import datetime, timezone
    cache = json.dumps({
        "campaign_rev": campaign_rev,
        "flow_rev": flow_rev,
        "total": campaign_rev + flow_rev,
        "campaign_count": campaign_count,
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    })

    _write_cache(cache)
    print(f"[pacing_cache] Cached: campaigns ${campaign_rev:,.2f}, flows ${flow_rev:,.2f}")
