"""Beezy Agents Dashboard — /dashboard control center."""
from __future__ import annotations
import json, os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

NY = ZoneInfo("America/New_York")
MONTHLY_GOAL = 150_000
REPLIT_DOMAIN = os.environ.get("REPLIT_DOMAIN", "beezy-agents-ingestion.replit.app")

router = APIRouter()


# ── DB helpers ────────────────────────────────────────────────────────────────

def _conn():
    from db.connection import get_conn
    return get_conn()


def _q(sql, params=()):
    try:
        with _conn() as c:
            return c.execute(sql, params).fetchall()
    except Exception:
        return []


def _q1(sql, params=()):
    rows = _q(sql, params)
    return rows[0] if rows else None


# ── Data functions ────────────────────────────────────────────────────────────

def _pacing() -> dict:
    today = date.today()
    ms = today.replace(day=1)
    days_elapsed = (today - ms).days + 1
    days_left = max((ms.replace(month=ms.month % 12 + 1, day=1) - timedelta(days=1) - today).days, 1)
    if ms.month == 12:
        next_month_start = ms.replace(year=ms.year + 1, month=1, day=1)
    else:
        next_month_start = ms.replace(month=ms.month + 1, day=1)
    import calendar as _cal
    last_day_of_month = _cal.monthrange(today.year, today.month)[1]
    days_left = max(last_day_of_month - today.day, 1)

    cr = fr = 0.0
    cc = 0
    as_of = None
    try:
        row = _q1("SELECT value FROM agent_state WHERE key='pacing_cache'")
        if row:
            d = json.loads(row[0])
            cr = float(d.get("campaign_rev", 0))
            fr = float(d.get("flow_rev", 0))
            cc = int(d.get("campaign_count", 0))
            as_of = d.get("as_of")
    except Exception:
        pass
    rev = cr + fr
    pct = rev / MONTHLY_GOAL * 100 if MONTHLY_GOAL else 0
    daily_rate_actual = rev / days_elapsed if days_elapsed else 0
    forecast = rev + daily_rate_actual * days_left
    daily_needed = max(MONTHLY_GOAL - rev, 0) / days_left
    if pct >= 80:
        status = "ON TRACK"
    elif pct >= (days_elapsed / 31 * 100) * 0.95:
        status = "ON TRACK"
    else:
        linear_expected = MONTHLY_GOAL * days_elapsed / (days_elapsed + days_left)
        if rev >= linear_expected * 1.05:
            status = "AHEAD"
        elif rev >= linear_expected * 0.95:
            status = "ON TRACK"
        else:
            status = "BEHIND"
    return {
        "rev": rev, "goal": MONTHLY_GOAL, "pct": pct, "cc": cc,
        "days_elapsed": days_elapsed, "days_left": days_left,
        "daily_needed": daily_needed, "daily_actual": daily_rate_actual,
        "forecast": forecast, "cr": cr, "fr": fr, "as_of": as_of,
        "status": status,
    }


def _today_slots() -> list:
    today = date.today()
    rows = _q(
        "SELECT id, content_type, audience, topic_angle, status, notes, actual_revenue, klaviyo_campaign_id "
        "FROM calendar_executions WHERE slot_date=%s ORDER BY executed_at",
        (today,)
    )
    return [{"id": str(r[0]), "t": r[1], "a": r[2], "tp": r[3] or "",
             "s": r[4], "n": (r[5] or "")[:100], "rv": float(r[6] or 0),
             "kid": r[7] or ""} for r in rows]


def _next_send_date() -> str:
    today = date.today()
    row = _q1(
        "SELECT slot_date FROM calendar_executions WHERE slot_date > %s ORDER BY slot_date ASC LIMIT 1",
        (today,)
    )
    if row and row[0]:
        return row[0].strftime("%a %b %d")
    # Check decisions table
    month = today.strftime("%Y-%m")
    drow = _q1("SELECT output FROM decisions WHERE decision_type='calendar_plan' AND output->>'month'=%s ORDER BY created_at DESC LIMIT 1", (month,))
    if drow:
        try:
            payload = drow[0] if isinstance(drow[0], dict) else json.loads(drow[0])
            slots = payload.get("slots", [])
            future = sorted([s["date"] for s in slots if s.get("date", "") > today.isoformat()])
            if future:
                return date.fromisoformat(future[0]).strftime("%a %b %d")
        except Exception:
            pass
    return "soon"


def _approval_status() -> dict:
    today = date.today()
    row = _q1(
        "SELECT week_start, approved_at, token FROM calendar_approvals "
        "WHERE week_start <= %s AND %s < week_start + INTERVAL '7 days' "
        "ORDER BY week_start DESC LIMIT 1",
        (today, today)
    )
    week_approved = False
    week_start = None
    if row:
        week_start = row[0]
        week_approved = row[1] is not None

    # Month approval: check if there's a calendar plan this month
    month = today.strftime("%Y-%m")
    plan_row = _q1(
        "SELECT id, created_at FROM decisions WHERE decision_type='calendar_plan' "
        "AND output->>'month'=%s ORDER BY created_at DESC LIMIT 1",
        (month,)
    )
    month_has_plan = plan_row is not None

    # Count upcoming slots in this month's plan
    upcoming_count = 0
    total_estimated_rev = 0.0
    if plan_row:
        try:
            drow = _q1("SELECT output FROM decisions WHERE id=%s", (str(plan_row[0]),))
            if drow:
                payload = drow[0] if isinstance(drow[0], dict) else json.loads(drow[0])
                slots = payload.get("slots", [])
                for s in slots:
                    if s.get("date", "") >= today.isoformat():
                        upcoming_count += 1
                        ct = s.get("content_type", "")
                        if ct not in ("seo_blog", "flow_experiment"):
                            total_estimated_rev += float(s.get("revenue_estimate", 0) or 0)
        except Exception:
            pass

    return {
        "week_approved": week_approved,
        "week_start": week_start,
        "month_has_plan": month_has_plan,
        "upcoming_count": upcoming_count,
        "total_estimated_rev": total_estimated_rev,
    }


def _upcoming_slots(days=7) -> list:
    today = date.today()
    end = today + timedelta(days=days)
    month = today.strftime("%Y-%m")
    drow = _q1(
        "SELECT output FROM decisions WHERE decision_type='calendar_plan' AND output->>'month'=%s "
        "ORDER BY created_at DESC LIMIT 1",
        (month,)
    )
    if not drow:
        return []
    try:
        payload = drow[0] if isinstance(drow[0], dict) else json.loads(drow[0])
        slots = payload.get("slots", [])
    except Exception:
        return []

    window = [s for s in slots if today.isoformat() <= s.get("date", "") <= end.isoformat()]
    window.sort(key=lambda x: (x.get("date", ""), x.get("send_time_est", "")))

    # Overlay execution status
    exec_rows = _q(
        "SELECT id, slot_date, content_type, audience, status, klaviyo_campaign_id "
        "FROM calendar_executions WHERE slot_date BETWEEN %s AND %s",
        (today, end)
    )
    exec_map = {}
    for r in exec_rows:
        k = (str(r[1]), r[2], r[3])
        exec_map[k] = {"id": str(r[0]), "status": r[4], "kid": r[5] or ""}

    result = []
    for s in window:
        k = (s.get("date", ""), s.get("content_type", ""), s.get("audience", ""))
        ex = exec_map.get(k, {})
        result.append({
            "date": s.get("date", ""),
            "t": s.get("content_type", ""),
            "a": s.get("audience", ""),
            "tp": s.get("topic_angle", "")[:60],
            "tm": s.get("send_time_est", ""),
            "rv": float(s.get("revenue_estimate", 0) or 0),
            "status": ex.get("status", "planned"),
            "exec_id": ex.get("id", ""),
            "kid": ex.get("kid", ""),
        })
    return result


def _audience_health() -> list:
    # Try agent_state first (written by workers/audience_health.py once built)
    row = _q1("SELECT value FROM agent_state WHERE key='audience_health'")
    if row:
        try:
            return json.loads(row[0])
        except Exception:
            pass
    # Fallback: compute from calendar_executions
    today = date.today()
    rows = _q(
        """SELECT audience,
                  MAX(slot_date) as last_send,
                  COALESCE(AVG(actual_rpr) FILTER (WHERE actual_rpr > 0 AND slot_date > CURRENT_DATE - 90), 0) as rpr_90d,
                  COALESCE(AVG(actual_rpr) FILTER (WHERE actual_rpr > 0 AND slot_date > CURRENT_DATE - 30), 0) as rpr_30d,
                  COUNT(*) FILTER (WHERE slot_date > CURRENT_DATE - 90 AND status IN ('dispatched','completed')) as sends_90d
           FROM calendar_executions
           WHERE status IN ('dispatched','completed') AND audience IS NOT NULL
           GROUP BY audience
           ORDER BY rpr_90d DESC"""
    )
    result = []
    for r in rows:
        audience = r[0]
        last_send = r[1]
        rpr_90d = float(r[2] or 0)
        rpr_30d = float(r[3] or 0)
        sends_90d = int(r[4] or 0)
        days_since = (today - last_send).days if last_send else 999
        if days_since < 7:
            health = "RECENT"
        elif days_since < 14:
            health = "WARM"
        else:
            health = "FRESH"
        result.append({
            "audience": audience,
            "last_send": str(last_send) if last_send else "Never",
            "days_since": days_since,
            "rpr_90d": rpr_90d,
            "rpr_30d": rpr_30d,
            "sends_90d": sends_90d,
            "health": health,
        })
    return result


def _flow_health() -> dict | None:
    row = _q1("SELECT strategy_text, created_at FROM strategies WHERE component='flow_monitor' ORDER BY created_at DESC LIMIT 1")
    if not row:
        return None
    try:
        data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        data["_checked_at"] = str(row[1])[:16] if row[1] else ""
        return data
    except Exception:
        return None


def _top_performers() -> list:
    rows = _q(
        "SELECT audience, content_type, actual_revenue, actual_rpr, slot_date "
        "FROM calendar_executions WHERE actual_revenue > 0 "
        "ORDER BY actual_revenue DESC LIMIT 10"
    )
    return [{"a": r[0], "t": r[1], "rv": float(r[2] or 0), "rpr": float(r[3] or 0),
             "d": str(r[4])} for r in rows]


def _learning_loop() -> dict:
    rows = _q(
        "SELECT component, strategy_text, created_at FROM strategies "
        "WHERE component='learning_loop' ORDER BY created_at DESC LIMIT 3"
    )
    result = {"entries": [], "rpr_by_audience": {}}
    for r in rows:
        try:
            data = r[1] if isinstance(r[1], dict) else json.loads(r[1])
            if isinstance(data, dict) and "rpr_by_audience" in data:
                result["rpr_by_audience"] = data["rpr_by_audience"]
            result["entries"].append({
                "component": r[0],
                "summary": str(r[1])[:120] if isinstance(r[1], str) else json.dumps(r[1])[:120],
                "at": str(r[2])[:16] if r[2] else "",
            })
        except Exception:
            pass
    return result


# ── HTML section builders ─────────────────────────────────────────────────────

_CT_COLORS = {
    "klaviyo_campaign": "#1a73e8",
    "sniper_followup": "#1558b0",
    "hive_mind": "#7b2d8b",
    "seo_blog": "#1e7e34",
    "sleep_audio": "#0e7c7b",
    "sms_campaign": "#e07b00",
    "flow_experiment": "#888",
}

_CT_LABEL = {
    "klaviyo_campaign": "Email",
    "sniper_followup": "Email (Sniper)",
    "hive_mind": "Hive Mind",
    "seo_blog": "SEO Blog",
    "sleep_audio": "Sleep Audio",
    "sms_campaign": "SMS",
    "flow_experiment": "Flow Exp.",
}

_STATUS_COLORS = {
    "dispatched": ("#1a73e8", "Scheduled"),
    "completed": ("#1e7e34", "Sent"),
    "failed": ("#c0392b", "Failed"),
    "blocked": ("#e07b00", "Blocked"),
    "planned": ("#888", "Planned"),
    "pending": ("#888", "Pending"),
    "cancelled": ("#555", "Cancelled"),
    "skipped": ("#888", "Skipped"),
}


def _status_badge(status: str) -> str:
    color, label = _STATUS_COLORS.get(status, ("#888", status))
    return f'<span class="badge" style="background:{color}">{label}</span>'


def _ct_chip(ct: str) -> str:
    color = _CT_COLORS.get(ct, "#555")
    label = _CT_LABEL.get(ct, ct)
    return f'<span class="chip" style="background:{color}">{label}</span>'


def _html_command_center(p: dict) -> str:
    circ = 2 * 3.14159 * 70
    fp = min(p["pct"] / 100, 1.0)
    filled = circ * fp
    empty = circ - filled
    if p["status"] == "AHEAD":
        gc = "#1e7e34"
        status_color = "#1e7e34"
    elif p["status"] == "ON TRACK":
        gc = "#1e7e34"
        status_color = "#1e7e34"
    else:
        behind_pct = (p["days_elapsed"] / (p["days_elapsed"] + p["days_left"])) * 100 - p["pct"]
        gc = "#c0392b" if behind_pct > 10 else "#e07b00"
        status_color = "#c0392b" if behind_pct > 10 else "#e07b00"

    as_of_str = f" · as of {p['as_of'][:16]}" if p.get("as_of") else ""
    forecast_str = f"${p['forecast']:,.0f}"
    forecast_note = "projected month-end at current pace"
    if p["forecast"] >= MONTHLY_GOAL:
        forecast_class = "forecast-good"
    else:
        forecast_class = "forecast-warn"

    boost_button = ""
    if p["status"] == "BEHIND":
        gap = max(0, MONTHLY_GOAL * (p["days_elapsed"] / (p["days_elapsed"] + p["days_left"])) - p["rev"])
        boost_button = f"""
        <div class="boost-bar">
          <span class="boost-label">You're behind pace by ${gap:,.0f}.</span>
          <form method="POST" action="/api/boost" style="display:inline">
            <button type="submit" class="btn-boost">Boost Revenue Now</button>
          </form>
        </div>"""

    return f"""
    <div class="card command-center">
      <div class="cc-gauge">
        <svg width="160" height="160" viewBox="0 0 160 160">
          <circle cx="80" cy="80" r="70" fill="none" stroke="#e8dcc8" stroke-width="14"/>
          <circle cx="80" cy="80" r="70" fill="none" stroke="{gc}" stroke-width="14"
            stroke-dasharray="{filled:.1f} {empty:.1f}" stroke-linecap="round"
            transform="rotate(-90 80 80)"/>
        </svg>
        <div class="gauge-inner">
          <div class="gauge-pct" style="color:{gc}">{p["pct"]:.0f}%</div>
          <div class="gauge-label">of ${p["goal"]:,}</div>
        </div>
      </div>
      <div class="cc-stats">
        <div class="cc-hero">
          <div class="hero-num">${p["rev"]:,.0f}</div>
          <div class="hero-sub">Revenue MTD{as_of_str}</div>
          <div class="status-pill" style="background:{status_color}">{p["status"]}</div>
        </div>
        <div class="cc-grid">
          <div class="cc-stat">
            <div class="stat-val">${p["cr"]:,.0f} / ${p["fr"]:,.0f}</div>
            <div class="stat-lbl">Campaigns / Flows</div>
          </div>
          <div class="cc-stat">
            <div class="stat-val {forecast_class}">{forecast_str}</div>
            <div class="stat-lbl">{forecast_note}</div>
          </div>
          <div class="cc-stat">
            <div class="stat-val">${p["daily_needed"]:,.0f}/day</div>
            <div class="stat-lbl">Needed to hit goal</div>
          </div>
          <div class="cc-stat">
            <div class="stat-val">{p["days_left"]}d left · {p["days_elapsed"]}d elapsed</div>
            <div class="stat-lbl">Month progress</div>
          </div>
        </div>
      </div>
      {boost_button}
    </div>"""


def _html_today(slots: list, next_send: str) -> str:
    if not slots:
        return f"""
        <div class="card">
          <div class="card-title">Today's Agenda</div>
          <div class="empty-state">Rest day — next send: <strong>{next_send}</strong></div>
        </div>"""

    rows_html = ""
    for s in slots:
        retry_btn = ""
        if s["s"] in ("failed", "blocked"):
            retry_btn = f"""<form method="POST" action="/api/retry-slot?id={s["id"]}" style="display:inline">
              <button type="submit" class="btn-retry">Retry</button></form>"""
        klav_link = ""
        if s["kid"]:
            klav_link = f'<a href="https://www.klaviyo.com/campaign/{s["kid"]}/edit" target="_blank" class="link-sm">Klaviyo ↗</a>'
        rows_html += f"""
        <div class="agenda-row">
          <div class="agenda-type">{_ct_chip(s["t"])}</div>
          <div class="agenda-detail">
            <div class="agenda-audience">{s["a"]}</div>
            <div class="agenda-topic">{s["tp"][:55]}</div>
          </div>
          <div class="agenda-meta">
            {_status_badge(s["s"])}
            {"$" + f'{s["rv"]:,.0f}' if s["rv"] else ""}
            {retry_btn}
            {klav_link}
          </div>
        </div>"""

    return f"""
    <div class="card">
      <div class="card-title">Today's Agenda</div>
      {rows_html}
    </div>"""


def _html_approval(apv: dict) -> str:
    today = date.today()
    if apv["week_start"]:
        ws_label = apv["week_start"].strftime("%b %d")
        we_label = (apv["week_start"] + timedelta(days=6)).strftime("%b %d")
        week_range = f"Week of {ws_label}–{we_label}"
    else:
        # Derive week_start from Monday of current week
        monday = today - timedelta(days=today.weekday())
        week_range = f"Week of {monday.strftime('%b %d')}"

    if apv["week_approved"]:
        approval_html = f"""
        <div class="approval-status approved">
          <span class="approval-icon">✓</span>
          <div>
            <div class="approval-title">{week_range} — APPROVED</div>
            <div class="approval-sub">Campaigns will run automatically each morning.</div>
          </div>
        </div>"""
    else:
        approval_html = f"""
        <div class="approval-status pending">
          <span class="approval-icon">⚠</span>
          <div>
            <div class="approval-title">{week_range} — PENDING APPROVAL</div>
            <div class="approval-sub">{apv["upcoming_count"]} slots queued · est. ${apv["total_estimated_rev"]:,.0f} revenue</div>
          </div>
        </div>
        <form method="POST" action="/api/approve-week">
          <button type="submit" class="btn-approve">Approve This Week</button>
        </form>
        <div class="approval-note">Or type <code>approved week</code> in #beezy-agents</div>"""

    plan_status = "✓ Calendar plan exists for this month" if apv["month_has_plan"] else "⚠ No calendar plan — type <code>generate calendar</code> in Slack"

    return f"""
    <div class="card">
      <div class="card-title">Approval Center</div>
      {approval_html}
      <div class="plan-status">{plan_status}</div>
    </div>"""


def _html_calendar(slots: list) -> str:
    if not slots:
        return """
        <div class="card">
          <div class="card-title">7-Day Calendar</div>
          <div class="empty-state">No upcoming slots found. Generate a calendar first.</div>
        </div>"""

    total_rev = sum(s["rv"] for s in slots if s["t"] not in ("seo_blog", "flow_experiment"))
    today_iso = date.today().isoformat()
    last_date = ""
    rows_html = ""
    for s in slots:
        d = s["date"]
        d_label = ""
        if d != last_date:
            last_date = d
            try:
                d_label = date.fromisoformat(d).strftime("%a %b %d")
            except Exception:
                d_label = d
        is_today = d == today_iso
        row_class = "cal-row today-row" if is_today else "cal-row"
        retry_btn = ""
        if s["exec_id"] and s["status"] in ("failed", "blocked"):
            retry_btn = f'<form method="POST" action="/api/retry-slot?id={s["exec_id"]}" style="display:inline"><button type="submit" class="btn-retry-sm">↺</button></form>'
        ct_color = _CT_COLORS.get(s["t"], "#555")
        rows_html += f"""
        <tr class="{row_class}" style="border-left:3px solid {ct_color}">
          <td class="cal-date"><strong>{d_label}</strong>{"<span class='today-tag'>TODAY</span>" if is_today else ""}</td>
          <td>{_ct_chip(s["t"])}</td>
          <td>{s["a"]}</td>
          <td class="cal-topic">{s["tp"]}</td>
          <td>{s["tm"]} ET</td>
          <td>{"$" + f'{s["rv"]:,.0f}' if s["rv"] else "—"}</td>
          <td>{_status_badge(s["status"])} {retry_btn}</td>
        </tr>"""

    return f"""
    <div class="card">
      <div class="card-title">7-Day Calendar
        <span class="card-title-meta">Projected revenue (campaigns only): ${total_rev:,.0f}</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Date</th><th>Type</th><th>Audience</th><th>Topic</th><th>Time</th><th>Est. Rev</th><th>Status</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </div>"""


def _html_audience_health(data: list) -> str:
    if not data:
        return """
        <div class="card">
          <div class="card-title">Audience Health</div>
          <div class="empty-state">No send history yet.</div>
        </div>"""

    _HC = {"FRESH": ("#1e7e34", "FRESH"), "WARM": ("#e07b00", "WARM"), "RECENT": ("#c0392b", "COOLDOWN")}
    rows_html = ""
    for r in data[:12]:
        hc, hl = _HC.get(r["health"], ("#888", r["health"]))
        trend = ""
        if r.get("rpr_90d") and r.get("rpr_30d"):
            if r["rpr_30d"] > r["rpr_90d"] * 1.05:
                trend = '<span style="color:#1e7e34">↑</span>'
            elif r["rpr_30d"] < r["rpr_90d"] * 0.9:
                trend = '<span style="color:#c0392b">↓</span>'
        rows_html += f"""
        <tr>
          <td><strong>{r["audience"]}</strong></td>
          <td>{r["last_send"]}</td>
          <td>{r["days_since"]}d ago</td>
          <td>${r["rpr_90d"]:.3f} {trend}</td>
          <td>{r["sends_90d"]}</td>
          <td><span class="badge" style="background:{hc}">{hl}</span></td>
        </tr>"""

    return f"""
    <div class="card">
      <div class="card-title">Audience Health</div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Audience</th><th>Last Send</th><th>Since</th><th>90d RPR</th><th>Sends</th><th>Status</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </div>"""


def _html_flows(data: dict | None) -> str:
    if not data:
        return """
        <div class="card">
          <div class="card-title">Flow Health</div>
          <div class="empty-state">No flow data. Type <code>flow check</code> in Slack.</div>
        </div>"""

    checked_at = data.get("_checked_at", "")
    analyses = data.get("analyses", [])
    if not analyses:
        return f"""
        <div class="card">
          <div class="card-title">Flow Health <span class="card-title-meta">{checked_at}</span></div>
          <div class="empty-state">No flow analysis available.</div>
        </div>"""

    rows_html = ""
    for a in analyses[:10]:
        sev = a.get("severity", "ok")
        sev_color = {"ok": "#1e7e34", "underperforming": "#e07b00", "broken": "#c0392b"}.get(sev, "#888")
        fix_badge = '<span class="badge" style="background:#7b2d8b">Fix queued</span>' if a.get("fix_queued") else ""
        rows_html += f"""
        <tr>
          <td>{a.get("name","?")[:28]}</td>
          <td>${float(a.get("revenue",0)):,.0f}</td>
          <td>${float(a.get("rpr",0)):.2f}</td>
          <td><span class="badge" style="background:{sev_color}">{sev.upper()}</span> {fix_badge}</td>
        </tr>"""

    return f"""
    <div class="card">
      <div class="card-title">Flow Health <span class="card-title-meta">{checked_at}</span></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Flow</th><th>30d Rev</th><th>RPR</th><th>Status</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </div>"""


def _html_performers(data: list) -> str:
    if not data:
        return """
        <div class="card">
          <div class="card-title">Top Performers</div>
          <div class="empty-state">No finalized campaign data yet.</div>
        </div>"""

    rows_html = ""
    for i, r in enumerate(data):
        rows_html += f"""
        <tr>
          <td class="rank">#{i+1}</td>
          <td>{r["a"]}</td>
          <td>{_ct_chip(r["t"])}</td>
          <td><strong>${r["rv"]:,.0f}</strong></td>
          <td>${r["rpr"]:.3f}</td>
          <td>{r["d"]}</td>
        </tr>"""

    return f"""
    <div class="card">
      <div class="card-title">Top Performers</div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>#</th><th>Audience</th><th>Type</th><th>Revenue</th><th>RPR</th><th>Date</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </div>"""


def _html_learning(data: dict) -> str:
    entries = data.get("entries", [])
    rpr = data.get("rpr_by_audience", {})

    if not entries and not rpr:
        return """
        <div class="card">
          <div class="card-title">Learning Loop</div>
          <div class="empty-state">No learning loop data yet. Runs weekly Sunday 9pm.</div>
        </div>"""

    entry_html = ""
    for e in entries[:3]:
        entry_html += f'<div class="ll-entry"><span class="ll-at">{e["at"]}</span> {e["summary"]}</div>'

    rpr_html = ""
    if rpr:
        rpr_html = '<div class="ll-rpr">'
        for aud, val in list(rpr.items())[:6]:
            rpr_html += f'<div class="rpr-row"><span>{aud}</span><span>${float(val):.3f}</span></div>'
        rpr_html += '</div>'

    return f"""
    <div class="card">
      <div class="card-title">Learning Loop</div>
      {entry_html}
      {rpr_html}
    </div>"""


# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = """
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Serif+Display&display=swap');
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'DM Sans',sans-serif;background:#faf6ee;color:#2c2417;padding:20px;font-size:14px}
a{color:#8b4513;text-decoration:none}
a:hover{text-decoration:underline}

/* Layout */
.page-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;padding-bottom:16px;border-bottom:2px solid #8b4513}
.page-header h1{font-family:'DM Serif Display',serif;font-size:26px;font-weight:400;color:#8b4513}
.page-header .ts{font-size:12px;color:#888}
.grid{display:grid;gap:16px;max-width:1200px}
.row-2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:768px){.row-2{grid-template-columns:1fr}}

/* Cards */
.card{background:#fff;border:1px solid #e8dcc8;border-radius:10px;padding:20px}
.card-title{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#8b7355;margin-bottom:14px;display:flex;justify-content:space-between;align-items:center}
.card-title-meta{font-weight:400;font-size:11px;color:#aaa;text-transform:none;letter-spacing:0}

/* Command Center */
.command-center{display:flex;align-items:center;flex-wrap:wrap;gap:24px;padding:24px}
.cc-gauge{position:relative;flex-shrink:0}
.gauge-inner{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center}
.gauge-pct{font-size:30px;font-weight:700}
.gauge-label{font-size:11px;color:#888}
.cc-stats{flex:1;min-width:220px}
.cc-hero{margin-bottom:16px}
.hero-num{font-size:36px;font-weight:700;color:#2c2417;line-height:1}
.hero-sub{font-size:12px;color:#888;margin-top:4px}
.status-pill{display:inline-block;padding:3px 12px;border-radius:20px;font-size:11px;font-weight:700;color:#fff;letter-spacing:0.05em;margin-top:6px}
.cc-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.cc-stat{background:#faf6ee;border-radius:6px;padding:10px 12px}
.stat-val{font-size:15px;font-weight:600}
.stat-lbl{font-size:11px;color:#888;margin-top:2px}
.forecast-good{color:#1e7e34}
.forecast-warn{color:#c0392b}
.boost-bar{width:100%;padding:12px 16px;background:#fff5f5;border:1px solid #f5c6c6;border-radius:8px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:8px}
.boost-label{font-size:13px;color:#c0392b;font-weight:500}
.btn-boost{background:#c0392b;color:#fff;border:none;padding:8px 18px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;letter-spacing:0.03em}
.btn-boost:hover{background:#a93226}

/* Today */
.agenda-row{display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid #f0ece4}
.agenda-row:last-child{border-bottom:none}
.agenda-type{flex-shrink:0;width:110px}
.agenda-detail{flex:1;min-width:0}
.agenda-audience{font-weight:600;font-size:13px}
.agenda-topic{font-size:12px;color:#888;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.agenda-meta{flex-shrink:0;display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:#8b4513}

/* Approval */
.approval-status{display:flex;align-items:flex-start;gap:12px;padding:14px;border-radius:8px;margin-bottom:14px}
.approval-status.approved{background:#f0faf0;border:1px solid #a3d9a3}
.approval-status.pending{background:#fffbf0;border:1px solid #f5dfa0}
.approval-icon{font-size:20px;flex-shrink:0;margin-top:2px}
.approval-title{font-weight:700;font-size:14px}
.approval-sub{font-size:12px;color:#888;margin-top:3px}
.btn-approve{width:100%;padding:12px;background:#8b4513;color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:700;cursor:pointer;letter-spacing:0.04em;text-transform:uppercase;margin-bottom:8px}
.btn-approve:hover{background:#6d3410}
.approval-note{font-size:12px;color:#888;text-align:center}
.approval-note code{background:#f5f5f5;padding:1px 4px;border-radius:3px;font-size:11px}
.plan-status{font-size:12px;color:#888;margin-top:12px;padding-top:10px;border-top:1px solid #f0ece4}
.plan-status code{background:#f5f5f5;padding:1px 4px;border-radius:3px;font-size:11px}

/* Badges + Chips */
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;color:#fff}
.chip{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;color:#fff}
.btn-retry{background:#fff;border:1px solid #c0392b;color:#c0392b;padding:2px 8px;border-radius:4px;font-size:11px;cursor:pointer;font-weight:600}
.btn-retry:hover{background:#c0392b;color:#fff}
.btn-retry-sm{background:#fff;border:1px solid #c0392b;color:#c0392b;padding:1px 6px;border-radius:4px;font-size:11px;cursor:pointer}
.link-sm{font-size:11px;color:#888}

/* Tables */
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;color:#8b7355;padding:7px 10px;border-bottom:2px solid #e8dcc8;white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid #f0ece4;vertical-align:middle}
tr:last-child td{border-bottom:none}
.cal-row{transition:background 0.1s}
.cal-row:hover{background:#fdf8f2}
.today-row{background:#fffef8}
.today-tag{display:inline-block;margin-left:6px;padding:1px 6px;background:#d4a847;color:#fff;border-radius:3px;font-size:10px;font-weight:700}
.cal-date{white-space:nowrap}
.cal-topic{max-width:200px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rank{color:#8b7355;font-weight:700;font-size:12px}

/* Learning Loop */
.ll-entry{padding:8px 0;border-bottom:1px solid #f0ece4;font-size:13px;color:#555}
.ll-entry:last-child{border-bottom:none}
.ll-at{font-size:11px;color:#aaa;margin-right:8px}
.ll-rpr{margin-top:12px;display:grid;grid-template-columns:1fr 1fr;gap:4px}
.rpr-row{display:flex;justify-content:space-between;padding:4px 8px;background:#faf6ee;border-radius:4px;font-size:12px}

/* Misc */
.empty-state{color:#bbb;font-style:italic;font-size:13px;padding:16px 0}
.footer{margin-top:32px;font-size:11px;color:#aaa;text-align:center;padding-top:16px;border-top:1px solid #e8dcc8}
"""


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    now = datetime.now(NY)
    p = _pacing()
    today_slots = _today_slots()
    next_send = _next_send_date() if not today_slots else ""
    apv = _approval_status()
    upcoming = _upcoming_slots(7)
    audience_health = _audience_health()
    flows = _flow_health()
    performers = _top_performers()
    learning = _learning_loop()

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <meta http-equiv="refresh" content="300">
  <title>Beezy Agents</title>
  <style>{CSS}</style>
</head>
<body>
<div class="page-header">
  <h1>Beezy Agents</h1>
  <div class="ts">{now.strftime("%a %b %d, %Y %I:%M %p ET")} · Auto-refreshes every 5 min</div>
</div>
<div class="grid">
  {_html_command_center(p)}
  <div class="row-2">
    {_html_approval(apv)}
    {_html_today(today_slots, next_send)}
  </div>
  {_html_calendar(upcoming)}
  <div class="row-2">
    {_html_audience_health(audience_health)}
    {_html_flows(flows)}
  </div>
  <div class="row-2">
    {_html_performers(performers)}
    {_html_learning(learning)}
  </div>
</div>
<div class="footer">Beezy Agents · Validator (17 rules) · Learning Loop · /healthz</div>
</body>
</html>"""
    return HTMLResponse(content=html)
