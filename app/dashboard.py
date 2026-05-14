"""
Beezy Agents Dashboard — served at /dashboard from the existing FastAPI app.

Single HTML page showing:
  - Revenue pacing gauge (MTD vs $150K goal)
  - Today's campaigns (status, audience, subject)
  - This week's performance
  - Upcoming 7-day calendar
  - Flow health summary
  - Recent validator blocks
  - System status

All data pulled live from Neon DB + Klaviyo.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
import httpx
import httpx

NY = ZoneInfo("America/New_York")
MONTHLY_GOAL = 150_000

router = APIRouter()


def _get_conn():
    from db.connection import get_conn
    return get_conn()


def _pacing_data() -> dict:
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
    }


def _todays_campaigns() -> list[dict]:
    today = date.today()
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT content_type, audience, topic_angle, status, notes,
                      actual_revenue, klaviyo_campaign_id
               FROM calendar_executions
               WHERE slot_date = %s
               ORDER BY content_type""",
            (today,)
        ).fetchall()
    return [
        {"type": r[0], "audience": r[1], "topic": r[2] or "", "status": r[3],
         "notes": (r[4] or "")[:80], "revenue": float(r[5] or 0),
         "campaign_id": r[6] or ""}
        for r in rows
    ]


def _upcoming_calendar() -> list[dict]:
    today = date.today()
    end = today + timedelta(days=7)
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT slot_date, content_type, audience, topic_angle, send_time_est, status
               FROM calendar_executions
               WHERE slot_date BETWEEN %s AND %s
               ORDER BY slot_date, send_time_est""",
            (today, end)
        ).fetchall()
    return [
        {"date": str(r[0]), "type": r[1], "audience": r[2],
         "topic": r[3] or "", "time": r[4] or "", "status": r[5]}
        for r in rows
    ]


def _week_performance() -> list[dict]:
    today = date.today()
    week_ago = today - timedelta(days=7)
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT audience, content_type,
                      COUNT(*) as sends,
                      COALESCE(SUM(actual_revenue), 0) as rev,
                      COALESCE(AVG(actual_rpr), 0) as avg_rpr,
                      COALESCE(SUM(recipients), 0) as recip
               FROM calendar_executions
               WHERE slot_date BETWEEN %s AND %s
                 AND status IN ('dispatched','completed')
               GROUP BY audience, content_type
               ORDER BY rev DESC""",
            (week_ago, today)
        ).fetchall()
    return [
        {"audience": r[0], "type": r[1], "sends": int(r[2]),
         "revenue": float(r[3]), "rpr": float(r[4]), "recipients": int(r[5])}
        for r in rows
    ]


def _flow_health() -> dict | None:
    """Get most recent flow health check from strategies table."""
    with _get_conn() as conn:
        row = conn.execute(
            """SELECT strategy_text FROM strategies
               WHERE component = 'flow_monitor'
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()
    if row:
        return json.loads(row[0])
    return None


def _recent_blocks() -> list[dict]:
    """Get recent validator blocks from calendar_executions."""
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT slot_date, content_type, audience, notes
               FROM calendar_executions
               WHERE status = 'failed' AND notes LIKE '%blocked%'
               ORDER BY slot_date DESC LIMIT 5"""
        ).fetchall()
    return [
        {"date": str(r[0]), "type": r[1], "audience": r[2], "reason": r[3] or ""}
        for r in rows
    ]


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="300">
<title>Beezy Agents — Dashboard</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Serif+Display&display=swap');
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:'DM Sans',sans-serif; background:#faf8f4; color:#1a1a1a; padding:24px; }}
  .header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:32px; border-bottom:2px solid #1a1a1a; padding-bottom:16px; }}
  .header h1 {{ font-family:'DM Serif Display',serif; font-size:28px; font-weight:400; }}
  .header .time {{ font-size:13px; color:#888; }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; max-width:1100px; }}
  @media(max-width:768px) {{ .grid {{ grid-template-columns:1fr; }} }}
  .card {{ background:#fff; border:1px solid #e8e4dc; border-radius:8px; padding:20px; }}
  .card-title {{ font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:0.06em; color:#8b7355; margin-bottom:12px; }}
  .pacing {{ grid-column:span 2; display:flex; align-items:center; gap:32px; }}
  @media(max-width:768px) {{ .pacing {{ grid-column:span 1; flex-direction:column; }} }}
  .gauge {{ position:relative; width:160px; height:160px; flex-shrink:0; }}
  .gauge svg {{ transform:rotate(-90deg); }}
  .gauge-text {{ position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); text-align:center; }}
  .gauge-pct {{ font-size:32px; font-weight:700; color:#8b4513; }}
  .gauge-label {{ font-size:11px; color:#888; }}
  .pacing-stats {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; flex:1; }}
  .stat {{ }}
  .stat-value {{ font-size:22px; font-weight:700; color:#1a1a1a; }}
  .stat-label {{ font-size:11px; color:#888; margin-top:2px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th {{ text-align:left; font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.04em; color:#8b7355; padding:6px 8px; border-bottom:1px solid #e8e4dc; }}
  td {{ padding:6px 8px; border-bottom:1px solid #f0ece4; }}
  .badge {{ display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; font-weight:500; }}
  .badge-ok {{ background:#eaf3de; color:#27500a; }}
  .badge-warn {{ background:#faeeda; color:#633806; }}
  .badge-fail {{ background:#fde2e2; color:#8b1a1a; }}
  .badge-dispatched {{ background:#e6f1fb; color:#0c447c; }}
  .badge-scheduled {{ background:#eaf3de; color:#27500a; }}
  .badge-blocked {{ background:#fde2e2; color:#8b1a1a; }}
  .empty {{ color:#bbb; font-style:italic; font-size:13px; padding:12px 0; }}
  .footer {{ margin-top:32px; font-size:11px; color:#aaa; text-align:center; }}
</style>
</head>
<body>
<div class="header">
  <h1>Beezy Agents</h1>
  <div class="time">{timestamp} · Auto-refreshes every 5 min</div>
</div>

<div class="grid">

<!-- PACING GAUGE -->
<div class="card pacing">
  <div class="card-title" style="position:absolute;top:20px;left:20px;">Monthly Pacing</div>
  <div class="gauge" style="margin-top:20px;">
    <svg width="160" height="160" viewBox="0 0 160 160">
      <circle cx="80" cy="80" r="70" fill="none" stroke="#f0ece4" stroke-width="12"/>
      <circle cx="80" cy="80" r="70" fill="none" stroke="{gauge_color}" stroke-width="12"
        stroke-dasharray="{dash_filled} {dash_empty}" stroke-linecap="round"/>
    </svg>
    <div class="gauge-text">
      <div class="gauge-pct">{pct:.0f}%</div>
      <div class="gauge-label">of ${goal:,}</div>
    </div>
  </div>
  <div class="pacing-stats" style="margin-top:20px;">
    <div class="stat"><div class="stat-value">${revenue:,.0f}</div><div class="stat-label">Revenue MTD</div></div>
    <div class="stat"><div class="stat-value">${campaign_rev:,.0f} / ${flow_rev:,.0f}</div><div class="stat-label">Campaigns / Flows</div></div>
    <div class="stat"><div class="stat-value">${daily_needed:,.0f}</div><div class="stat-label">Daily needed</div></div>
    <div class="stat"><div class="stat-value">{days_left}d</div><div class="stat-label">Days left</div></div>
  </div>
</div>

<!-- TODAY'S CAMPAIGNS -->
<div class="card">
  <div class="card-title">Today's Campaigns</div>
  {today_html}
</div>

<!-- WEEK PERFORMANCE -->
<div class="card">
  <div class="card-title">This Week's Performance</div>
  {week_html}
</div>

<!-- UPCOMING CALENDAR -->
<div class="card" style="grid-column:span 2;">
  <div class="card-title">Next 7 Days</div>
  {calendar_html}
</div>

<!-- FLOW HEALTH -->
<div class="card">
  <div class="card-title">Flow Health</div>
  {flow_html}
</div>

<!-- RECENT BLOCKS -->
<div class="card">
  <div class="card-title">Recent Validator Blocks</div>
  {blocks_html}
</div>

</div>

<div class="footer">Beezy Agents v2 · Auto-schedule · Validator · Learning Loop</div>
</body>
</html>"""


def _render_today(campaigns: list) -> str:
    if not campaigns:
        return '<div class="empty">No campaigns dispatched today.</div>'
    rows = ""
    for c in campaigns:
        badge_class = "badge-" + ("scheduled" if "scheduled" in (c["status"] or "") else
                                   "blocked" if "blocked" in (c["notes"] or "") else "dispatched")
        rows += f'<tr><td>{c["type"]}</td><td>{c["audience"]}</td><td>{c["topic"][:40]}</td>'
        rows += f'<td><span class="badge {badge_class}">{c["status"]}</span></td>'
        rows += f'<td>${c["revenue"]:,.0f}</td></tr>'
    return f'<table><tr><th>Type</th><th>Audience</th><th>Topic</th><th>Status</th><th>Rev</th></tr>{rows}</table>'


def _render_week(perf: list) -> str:
    if not perf:
        return '<div class="empty">No performance data this week.</div>'
    rows = ""
    for p in perf:
        rows += f'<tr><td>{p["audience"]}</td><td>{p["type"]}</td><td>{p["sends"]}</td>'
        rows += f'<td>${p["revenue"]:,.0f}</td><td>${p["rpr"]:.3f}</td></tr>'
    return f'<table><tr><th>Audience</th><th>Type</th><th>Sends</th><th>Revenue</th><th>RPR</th></tr>{rows}</table>'


def _render_calendar(slots: list) -> str:
    if not slots:
        return '<div class="empty">No upcoming slots.</div>'
    rows = ""
    last_date = ""
    for s in slots:
        d = s["date"] if s["date"] != last_date else ""
        last_date = s["date"]
        badge_class = "badge-" + ("scheduled" if s["status"] in ("dispatched","completed") else "warn")
        rows += f'<tr><td style="font-weight:{"600" if d else "400"}">{d}</td>'
        rows += f'<td>{s["type"]}</td><td>{s["audience"]}</td><td>{s["topic"][:35]}</td>'
        rows += f'<td>{s["time"]}</td><td><span class="badge {badge_class}">{s["status"] or "pending"}</span></td></tr>'
    return f'<table><tr><th>Date</th><th>Type</th><th>Audience</th><th>Topic</th><th>Time</th><th>Status</th></tr>{rows}</table>'


def _render_flows(flow_data: dict | None) -> str:
    if not flow_data:
        return '<div class="empty">No flow health data yet. Run "flow check" in Slack.</div>'
    analyses = flow_data.get("analyses", [])
    if not analyses:
        return '<div class="empty">No flow data.</div>'
    rows = ""
    for a in analyses[:8]:
        sev = a.get("severity", "ok")
        badge_class = "badge-ok" if sev == "ok" else "badge-warn" if sev == "warn" else "badge-fail"
        issues = "; ".join(a.get("issues", []))[:60]
        rows += f'<tr><td>{a["name"][:30]}</td><td>${a["revenue"]:,.0f}</td><td>${a["rpr"]:.2f}</td>'
        rows += f'<td><span class="badge {badge_class}">{sev}</span></td><td>{issues}</td></tr>'
    return f'<table><tr><th>Flow</th><th>Rev (30d)</th><th>RPR</th><th>Status</th><th>Issues</th></tr>{rows}</table>'


def _render_blocks(blocks: list) -> str:
    if not blocks:
        return '<div class="empty">No recent blocks. Validator is happy.</div>'
    rows = ""
    for b in blocks:
        rows += f'<tr><td>{b["date"]}</td><td>{b["type"]}</td><td>{b["audience"]}</td><td>{b["reason"][:60]}</td></tr>'
    return f'<table><tr><th>Date</th><th>Type</th><th>Audience</th><th>Reason</th></tr>{rows}</table>'


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    from datetime import datetime
    now = datetime.now(NY)

    try:
        pacing = _pacing_data()
    except Exception:
        pacing = {"revenue": 0, "goal": MONTHLY_GOAL, "pct": 0, "campaigns": 0,
                  "days_elapsed": 0, "days_left": 30, "daily_needed": 5000}

    try:
        today_camps = _todays_campaigns()
    except Exception:
        today_camps = []

    try:
        week_perf = _week_performance()
    except Exception:
        week_perf = []

    try:
        calendar = _upcoming_calendar()
    except Exception:
        calendar = []

    try:
        flows = _flow_health()
    except Exception:
        flows = None

    try:
        blocks = _recent_blocks()
    except Exception:
        blocks = []

    # Gauge math
    circumference = 2 * 3.14159 * 70
    filled_pct = min(pacing["pct"] / 100, 1.0)
    dash_filled = circumference * filled_pct
    dash_empty = circumference - dash_filled
    gauge_color = "#27500a" if pacing["pct"] >= 80 else "#ef9f27" if pacing["pct"] >= 50 else "#c0392b"

    html = DASHBOARD_HTML.format(
        timestamp=now.strftime("%b %d, %Y %I:%M %p ET"),
        gauge_color=gauge_color,
        dash_filled=f"{dash_filled:.1f}",
        dash_empty=f"{dash_empty:.1f}",
        pct=pacing["pct"],
        goal=pacing["goal"],
        revenue=pacing["revenue"],
        campaigns=pacing["campaigns"],
        daily_needed=pacing["daily_needed"],
        campaign_rev=pacing.get("campaign_rev", 0),
        flow_rev=pacing.get("flow_rev", 0),
        days_left=pacing["days_left"],
        today_html=_render_today(today_camps),
        week_html=_render_week(week_perf),
        calendar_html=_render_calendar(calendar),
        flow_html=_render_flows(flows),
        blocks_html=_render_blocks(blocks),
    )
    return HTMLResponse(content=html)
