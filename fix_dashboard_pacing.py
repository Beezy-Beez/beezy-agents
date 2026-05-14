"""Fix dashboard pacing to pull live Klaviyo data instead of DB-only."""

def patch(filepath, old, new, label):
    with open(filepath, 'r') as f:
        content = f.read()
    if old not in content:
        print(f"  SKIP {label} — pattern not found")
        return False
    content = content.replace(old, new, 1)
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"  ✓ {label}")
    return True

print("Fixing dashboard pacing to use live Klaviyo data...")

# Add httpx import
patch(
    "app/dashboard.py",
    "from fastapi.responses import HTMLResponse",
    "from fastapi.responses import HTMLResponse\nimport httpx",
    "httpx import"
)

# Replace _pacing_data with Klaviyo API version
OLD = '''def _pacing_data() -> dict:
    today = date.today()
    month_start = today.replace(day=1)
    days_elapsed = (today - month_start).days + 1
    days_in_month = 30

    with _get_conn() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(actual_revenue), 0), COUNT(*)
               FROM calendar_executions
               WHERE slot_date BETWEEN %s AND %s
               AND status IN ('dispatched','completed')""",
            (month_start, today)
        ).fetchone()

    revenue = float(row[0]) if row else 0
    campaigns = int(row[1]) if row else 0
    pct = revenue / MONTHLY_GOAL * 100
    days_left = max(days_in_month - days_elapsed, 1)
    daily_needed = (MONTHLY_GOAL - revenue) / days_left

    return {
        "revenue": revenue, "goal": MONTHLY_GOAL, "pct": pct,
        "campaigns": campaigns, "days_elapsed": days_elapsed,
        "days_left": days_left, "daily_needed": daily_needed,
    }'''

NEW = '''def _pacing_data() -> dict:
    """Pull LIVE revenue from Klaviyo campaign + flow reports for the current month."""
    today = date.today()
    month_start = today.replace(day=1)
    days_elapsed = (today - month_start).days + 1
    days_in_month = 30

    headers = {
        "Authorization": "Klaviyo-API-Key " + os.environ.get("KLAVIYO_API_KEY", ""),
        "revision": "2025-10-15",
        "Content-Type": "application/json",
    }
    start_iso = month_start.isoformat() + "T00:00:00"
    end_iso = today.isoformat() + "T23:59:59"
    campaign_rev = 0.0
    flow_rev = 0.0
    campaign_count = 0

    # Pull campaign revenue MTD
    try:
        resp = httpx.post("https://a.klaviyo.com/api/campaign-values-reports/", headers=headers, timeout=30, json={
            "data": {"type": "campaign-values-report", "attributes": {
                "statistics": ["recipients"],
                "value_statistics": ["conversion_value"],
                "timeframe": {"start": start_iso + "Z", "end": end_iso + "Z"},
                "conversion_metric_id": "X93gjq",
            }}
        })
        if resp.status_code == 200:
            results = resp.json().get("data", {}).get("attributes", {}).get("results", [])
            for r in results:
                campaign_rev += float(r.get("statistics", {}).get("conversion_value", 0))
                campaign_count += 1
    except Exception as e:
        print(f"[dashboard] campaign revenue pull failed: {e}")

    # Pull flow revenue MTD
    try:
        resp = httpx.post("https://a.klaviyo.com/api/flow-values-reports/", headers=headers, timeout=30, json={
            "data": {"type": "flow-values-report", "attributes": {
                "statistics": ["recipients"],
                "value_statistics": ["conversion_value"],
                "timeframe": {"start": start_iso + "Z", "end": end_iso + "Z"},
                "conversion_metric_id": "X93gjq",
            }}
        })
        if resp.status_code == 200:
            agg = resp.json().get("data", {}).get("attributes", {}).get("flow_aggregation", [])
            for f in agg:
                flow_rev += float(f.get("statistics", {}).get("conversion_value", 0))
    except Exception as e:
        print(f"[dashboard] flow revenue pull failed: {e}")

    revenue = campaign_rev + flow_rev
    pct = revenue / MONTHLY_GOAL * 100
    days_left = max(days_in_month - days_elapsed, 1)
    daily_needed = (MONTHLY_GOAL - revenue) / days_left

    return {
        "revenue": revenue, "goal": MONTHLY_GOAL, "pct": pct,
        "campaigns": campaign_count, "days_elapsed": days_elapsed,
        "days_left": days_left, "daily_needed": daily_needed,
        "campaign_rev": campaign_rev, "flow_rev": flow_rev,
    }'''

patch("app/dashboard.py", OLD, NEW, "pacing now pulls live Klaviyo data")

# Update the pacing stats HTML to show campaign/flow split
patch(
    "app/dashboard.py",
    '<div class="stat"><div class="stat-value">{campaigns}</div><div class="stat-label">Campaigns sent</div></div>',
    '<div class="stat"><div class="stat-value">${campaign_rev:,.0f} / ${flow_rev:,.0f}</div><div class="stat-label">Campaigns / Flows</div></div>',
    "pacing shows campaign/flow split"
)

# Add campaign_rev and flow_rev to the format call
patch(
    "app/dashboard.py",
    "daily_needed=pacing[\"daily_needed\"],",
    "daily_needed=pacing[\"daily_needed\"],\n        campaign_rev=pacing.get(\"campaign_rev\", 0),\n        flow_rev=pacing.get(\"flow_rev\", 0),",
    "campaign_rev/flow_rev in template format"
)

print("\nDone. Touch app/main.py to reload, then refresh dashboard.")
