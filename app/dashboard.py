"""Beezy Agents Dashboard — /dashboard route."""
from __future__ import annotations
import json, os
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

NY = ZoneInfo("America/New_York")
MONTHLY_GOAL = 150_000
router = APIRouter()

def _conn():
    from db.connection import get_conn
    return get_conn()

def _pacing():
    today = date.today()
    ms = today.replace(day=1)
    de = (today - ms).days + 1
    dl = max(30 - de, 1)
    cr = fr = 0.0
    cc = 0
    try:
        with _conn() as c:
            r = c.execute("SELECT value FROM agent_state WHERE key='pacing_cache'").fetchone()
            if r:
                d = json.loads(r[0])
                cr, fr, cc = float(d.get("campaign_rev",0)), float(d.get("flow_rev",0)), int(d.get("campaign_count",0))
    except:
        pass
    rev = cr + fr
    return {"rev":rev,"goal":MONTHLY_GOAL,"pct":rev/MONTHLY_GOAL*100,"cc":cc,"de":de,"dl":dl,"dn":(MONTHLY_GOAL-rev)/dl,"cr":cr,"fr":fr}

def _today():
    try:
        with _conn() as c:
            rows = c.execute("SELECT content_type,audience,topic_angle,status,notes,actual_revenue FROM calendar_executions WHERE slot_date=%s ORDER BY content_type",(date.today(),)).fetchall()
        return [{"t":r[0],"a":r[1],"tp":r[2] or "","s":r[3],"n":(r[4] or "")[:80],"rv":float(r[5] or 0)} for r in rows]
    except:
        return []

def _week():
    try:
        wa = date.today() - timedelta(days=7)
        with _conn() as c:
            rows = c.execute("SELECT audience,content_type,COUNT(*),COALESCE(SUM(actual_revenue),0),COALESCE(AVG(actual_rpr),0) FROM calendar_executions WHERE slot_date BETWEEN %s AND %s AND status IN ('dispatched','completed') GROUP BY audience,content_type ORDER BY 4 DESC",(wa,date.today())).fetchall()
        return [{"a":r[0],"t":r[1],"s":int(r[2]),"rv":float(r[3]),"rpr":float(r[4])} for r in rows]
    except:
        return []

def _cal():
    try:
        with _conn() as c:
            rows = c.execute("SELECT slot_date,content_type,audience,topic_angle,send_time_est,status FROM calendar_executions WHERE slot_date BETWEEN %s AND %s ORDER BY slot_date,send_time_est",(date.today(),date.today()+timedelta(days=7))).fetchall()
        return [{"d":str(r[0]),"t":r[1],"a":r[2],"tp":r[3] or "","tm":r[4] or "","s":r[5]} for r in rows]
    except:
        return []

def _flows():
    try:
        with _conn() as c:
            r = c.execute("SELECT strategy_text FROM strategies WHERE component='flow_monitor' ORDER BY created_at DESC LIMIT 1").fetchone()
        return json.loads(r[0]) if r else None
    except:
        return None

def _blocks():
    try:
        with _conn() as c:
            rows = c.execute("SELECT slot_date,content_type,audience,notes FROM calendar_executions WHERE status='failed' AND notes LIKE '%%blocked%%' ORDER BY slot_date DESC LIMIT 5").fetchall()
        return [{"d":str(r[0]),"t":r[1],"a":r[2],"r":r[3] or ""} for r in rows]
    except:
        return []

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    from datetime import datetime
    now = datetime.now(NY)
    p = _pacing()
    circ = 2*3.14159*70
    fp = min(p["pct"]/100,1.0)
    df, de = circ*fp, circ-circ*fp
    gc = "#27500a" if p["pct"]>=80 else "#ef9f27" if p["pct"]>=50 else "#c0392b"

    tc = _today()
    t_html = '<div class="empty">No campaigns dispatched today.</div>'
    if tc:
        t_html = '<table><tr><th>Type</th><th>Audience</th><th>Topic</th><th>Status</th></tr>'
        for c in tc:
            t_html += f'<tr><td>{c["t"]}</td><td>{c["a"]}</td><td>{c["tp"][:40]}</td><td><span class="badge">{c["s"]}</span></td></tr>'
        t_html += '</table>'

    wk = _week()
    w_html = '<div class="empty">No performance data this week.</div>'
    if wk:
        w_html = '<table><tr><th>Audience</th><th>Type</th><th>Sends</th><th>Revenue</th><th>RPR</th></tr>'
        for w in wk:
            w_html += f'<tr><td>{w["a"]}</td><td>{w["t"]}</td><td>{w["s"]}</td><td>${w["rv"]:,.0f}</td><td>${w["rpr"]:.3f}</td></tr>'
        w_html += '</table>'

    cal = _cal()
    c_html = '<div class="empty">No upcoming slots.</div>'
    if cal:
        c_html = '<table><tr><th>Date</th><th>Type</th><th>Audience</th><th>Topic</th><th>Time</th><th>Status</th></tr>'
        ld = ""
        for s in cal:
            d = s["d"] if s["d"]!=ld else ""
            ld = s["d"]
            c_html += f'<tr><td style="font-weight:{"600" if d else "400"}">{d}</td><td>{s["t"]}</td><td>{s["a"]}</td><td>{s["tp"][:35]}</td><td>{s["tm"]}</td><td>{s["s"] or "pending"}</td></tr>'
        c_html += '</table>'

    fl = _flows()
    f_html = '<div class="empty">No flow data yet. Type "flow check" in Slack.</div>'
    if fl:
        analyses = fl.get("analyses",[])
        if analyses:
            f_html = '<table><tr><th>Flow</th><th>Rev</th><th>RPR</th><th>Status</th></tr>'
            for a in analyses[:8]:
                sev = a.get("severity","ok")
                f_html += f'<tr><td>{a["name"][:30]}</td><td>${a["revenue"]:,.0f}</td><td>${a["rpr"]:.2f}</td><td>{sev}</td></tr>'
            f_html += '</table>'

    bl = _blocks()
    b_html = '<div class="empty">No recent blocks. Validator is happy.</div>'
    if bl:
        b_html = '<table><tr><th>Date</th><th>Type</th><th>Audience</th><th>Reason</th></tr>'
        for b in bl:
            b_html += f'<tr><td>{b["d"]}</td><td>{b["t"]}</td><td>{b["a"]}</td><td>{b["r"][:60]}</td></tr>'
        b_html += '</table>'

    html = f'''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><meta http-equiv="refresh" content="300"><title>Beezy Agents</title>
<style>@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Serif+Display&display=swap');
*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:'DM Sans',sans-serif;background:#faf8f4;color:#1a1a1a;padding:24px}}
.hdr{{display:flex;justify-content:space-between;align-items:center;margin-bottom:32px;border-bottom:2px solid #1a1a1a;padding-bottom:16px}}
.hdr h1{{font-family:'DM Serif Display',serif;font-size:28px;font-weight:400}}.hdr .tm{{font-size:13px;color:#888}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;max-width:1100px}}@media(max-width:768px){{.grid{{grid-template-columns:1fr}}}}
.card{{background:#fff;border:1px solid #e8e4dc;border-radius:8px;padding:20px}}.card-t{{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;color:#8b7355;margin-bottom:12px}}
.pace{{grid-column:span 2;display:flex;align-items:center;gap:32px;flex-wrap:wrap}}.gauge{{position:relative;width:160px;height:160px;flex-shrink:0}}
.gauge svg{{transform:rotate(-90deg)}}.gauge-tx{{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center}}
.gauge-pct{{font-size:32px;font-weight:700;color:#8b4513}}.gauge-lb{{font-size:11px;color:#888}}
.stats{{display:grid;grid-template-columns:1fr 1fr;gap:12px;flex:1;min-width:200px}}.st-v{{font-size:22px;font-weight:700}}.st-l{{font-size:11px;color:#888;margin-top:2px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}th{{text-align:left;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;color:#8b7355;padding:6px 8px;border-bottom:1px solid #e8e4dc}}
td{{padding:6px 8px;border-bottom:1px solid #f0ece4}}.badge{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500;background:#eaf3de;color:#27500a}}
.empty{{color:#bbb;font-style:italic;font-size:13px;padding:12px 0}}.span2{{grid-column:span 2}}
.ftr{{margin-top:32px;font-size:11px;color:#aaa;text-align:center}}</style></head><body>
<div class="hdr"><h1>Beezy Agents</h1><div class="tm">{now.strftime("%b %d, %Y %I:%M %p ET")} · Auto-refreshes every 5 min</div></div>
<div class="grid">
<div class="card pace"><div class="card-t" style="position:absolute;top:20px;left:20px">Monthly Pacing</div>
<div class="gauge" style="margin-top:20px"><svg width="160" height="160" viewBox="0 0 160 160"><circle cx="80" cy="80" r="70" fill="none" stroke="#f0ece4" stroke-width="12"/><circle cx="80" cy="80" r="70" fill="none" stroke="{gc}" stroke-width="12" stroke-dasharray="{df:.1f} {de:.1f}" stroke-linecap="round"/></svg><div class="gauge-tx"><div class="gauge-pct">{p["pct"]:.0f}%</div><div class="gauge-lb">of ${p["goal"]:,}</div></div></div>
<div class="stats" style="margin-top:20px"><div><div class="st-v">${p["rev"]:,.0f}</div><div class="st-l">Revenue MTD</div></div><div><div class="st-v">${p["cr"]:,.0f} / ${p["fr"]:,.0f}</div><div class="st-l">Campaigns / Flows</div></div><div><div class="st-v">${p["dn"]:,.0f}</div><div class="st-l">Daily needed</div></div><div><div class="st-v">{p["dl"]}d</div><div class="st-l">Days left</div></div></div></div>
<div class="card"><div class="card-t">Today's Campaigns</div>{t_html}</div>
<div class="card"><div class="card-t">This Week's Performance</div>{w_html}</div>
<div class="card span2"><div class="card-t">Next 7 Days</div>{c_html}</div>
<div class="card"><div class="card-t">Flow Health</div>{f_html}</div>
<div class="card"><div class="card-t">Recent Validator Blocks</div>{b_html}</div>
</div><div class="ftr">Beezy Agents v2 · Auto-schedule · Validator · Learning Loop</div></body></html>'''
    return HTMLResponse(content=html)
