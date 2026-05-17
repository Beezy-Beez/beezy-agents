"""Beezy Agents Dashboard — /dashboard control center."""
from __future__ import annotations
import json, os
from config import KLAVIYO_REVISION
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
    stale = False

    def _load_cache():
        nonlocal cr, fr, cc, as_of, stale
        row = _q1("SELECT value FROM agent_state WHERE key='pacing_cache'")
        if not row:
            return False
        d = json.loads(row[0])
        cr = float(d.get("campaign_rev", 0))
        fr = float(d.get("flow_rev", 0))
        cc = int(d.get("campaign_count", 0))
        as_of = d.get("as_of")
        if as_of:
            try:
                # Handle formats: "2026-05-15 00:29 UTC", "2026-05-15T00:29:00Z", ISO with offset
                as_of_clean = (
                    as_of.replace(" UTC", "+00:00")
                         .replace("Z", "+00:00")
                         .replace(" ", "T")
                )
                if "+" not in as_of_clean and len(as_of_clean) <= 16:
                    as_of_clean += "+00:00"
                cache_dt = datetime.fromisoformat(as_of_clean)
                if (datetime.now(NY) - cache_dt).total_seconds() > 8 * 3600:
                    stale = True
            except Exception:
                stale = True  # unparseable timestamp → treat as stale
        return True

    def _refresh_cache():
        """Pull live from Klaviyo and reload into locals."""
        try:
            from workers.pacing_cache import refresh_pacing_cache
            refresh_pacing_cache()
            _load_cache()
        except Exception as e:
            print(f"[dashboard] inline pacing refresh failed: {e}")

    try:
        if not _load_cache():
            _refresh_cache()
        elif stale:
            # Cache exists but is >8h old — pull fresh silently so numbers are current
            stale = False
            _refresh_cache()
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
        "status": status, "stale": stale,
    }


def _today_slots() -> list:
    today = date.today()
    today_iso = today.isoformat()
    month = today.strftime("%Y-%m")

    # Load today's plan slots — this is the authoritative list
    plan_slots: list[dict] = []
    drow = _q1(
        "SELECT output FROM decisions WHERE decision_type='calendar_plan' AND output->>'month'=%s "
        "ORDER BY created_at DESC LIMIT 1",
        (month,)
    )
    if drow:
        try:
            payload = drow[0] if isinstance(drow[0], dict) else json.loads(drow[0])
            for s in payload.get("slots", []):
                if s.get("date") == today_iso:
                    plan_slots.append(s)
        except Exception:
            pass

    # For each plan slot, find the best execution row — prefer rows that have a
    # Klaviyo campaign ID (proof the campaign was actually created), then fall
    # back to any dispatched row, ordered newest-first to avoid stale duplicates.
    result = []
    for s in plan_slots:
        ct = s.get("content_type", "")
        aud = s.get("audience", "")
        # Best row = has a Klaviyo draft ID; among ties take most recent (id DESC)
        rows = _q(
            """SELECT id, status, notes, actual_revenue, klaviyo_campaign_id
               FROM calendar_executions
               WHERE slot_date=%s AND content_type=%s AND audience=%s
               ORDER BY COALESCE((notes LIKE 'klaviyo_draft:%%'), false) DESC, id DESC
               LIMIT 1""",
            (today, ct, aud)
        )
        if rows:
            r = rows[0]
            notes = r[2] or ""
            kid = r[4] or ""
            # Extract campaign ID from notes if not stored in the dedicated column
            if not kid and notes.startswith("klaviyo_draft:"):
                kid = notes[len("klaviyo_draft:"):]
            status = r[1]
            # "dispatched" with no campaign ID and no meaningful notes = still pending/uncertain
            if status == "dispatched" and not kid and not notes:
                status = "planned"
            result.append({
                "id": str(r[0]), "t": ct, "a": aud,
                "tp": s.get("topic_angle", "")[:100],
                "s": status,
                "n": notes[:100],
                "rv": float(r[3] or s.get("revenue_estimate", 0) or 0),
                "kid": kid,
            })
        else:
            result.append({
                "id": "", "t": ct, "a": aud,
                "tp": s.get("topic_angle", "")[:100], "s": "planned",
                "n": "", "rv": float(s.get("revenue_estimate", 0) or 0), "kid": "",
            })

    return result


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


def _upcoming_slots() -> list:
    today = date.today()
    today_iso = today.isoformat()
    import calendar as _cal2
    last_day = _cal2.monthrange(today.year, today.month)[1]
    month_end = date(today.year, today.month, last_day)
    month = today.strftime("%Y-%m")

    # Load the calendar plan
    drow = _q1(
        "SELECT output FROM decisions WHERE decision_type='calendar_plan' AND output->>'month'=%s "
        "ORDER BY created_at DESC LIMIT 1",
        (month,)
    )
    plan_slots: list = []
    if drow:
        try:
            payload = drow[0] if isinstance(drow[0], dict) else json.loads(drow[0])
            plan_slots = payload.get("slots", [])
        except Exception:
            pass

    # Build plan lookups keyed by (date, content_type, audience) and (content_type, audience)
    plan_by_date_key: dict = {}
    plan_by_type_key: dict = {}
    for s in plan_slots:
        dk = (s.get("date", ""), s.get("content_type", ""), s.get("audience", ""))
        tk = (s.get("content_type", ""), s.get("audience", ""))
        entry = {
            "tp": s.get("topic_angle", "")[:60],
            "tm": s.get("send_time_est", ""),
            "rv": float(s.get("revenue_estimate", 0) or 0),
        }
        plan_by_date_key[dk] = entry
        if tk not in plan_by_type_key:
            plan_by_type_key[tk] = entry

    # Fetch ALL executions from earliest plan date through month_end (deduped per day)
    plan_start = min((s.get("date", today_iso) for s in plan_slots), default=today_iso)
    exec_rows = _q(
        """SELECT DISTINCT ON (slot_date, content_type, audience)
               id, slot_date, content_type, audience, status, klaviyo_campaign_id, actual_revenue
           FROM calendar_executions
           WHERE slot_date BETWEEN %s AND %s
           ORDER BY slot_date, content_type, audience, id ASC""",
        (plan_start, month_end)
    )
    exec_by_key: dict = {}
    for r in exec_rows:
        k = (str(r[1]), r[2], r[3])
        exec_by_key[k] = {"id": str(r[0]), "status": r[4], "kid": r[5] or "", "actual_rev": float(r[6] or 0)}

    result: list = []
    seen_keys: set = set()

    # ALL plan slots (past + today + future) — overlay with exec data
    for s in plan_slots:
        d = s.get("date", "")
        dk = (d, s.get("content_type", ""), s.get("audience", ""))
        seen_keys.add(dk)
        ex = exec_by_key.get(dk, {})

        # Today: skip planned slots that never ran
        if d == today_iso and not ex:
            continue

        # Past: show plan slot; if exec ran, overlay status + actual_rev
        # Future: show plan slot; overlay if pre-dispatched
        if d < today_iso and not ex:
            status = "not_sent"
        else:
            status = ex.get("status", "planned")

        result.append({
            "date": d,
            "t": s.get("content_type", ""),
            "a": s.get("audience", ""),
            "tp": s.get("topic_angle", "")[:60],
            "tm": s.get("send_time_est", ""),
            "rv": float(s.get("revenue_estimate", 0) or 0),
            "actual_rev": ex.get("actual_rev", 0),
            "status": status,
            "exec_id": ex.get("id", ""),
            "kid": ex.get("kid", ""),
        })

    # Extra executions not in the current plan (e.g., slots dispatched under a prior plan version)
    for r in exec_rows:
        k = (str(r[1]), r[2], r[3])
        if k not in seen_keys:
            plan = plan_by_date_key.get(k) or plan_by_type_key.get((r[2], r[3]), {})
            result.append({
                "date": str(r[1]),
                "t": r[2],
                "a": r[3],
                "tp": plan.get("tp", ""),
                "tm": plan.get("tm", ""),
                "rv": plan.get("rv", 0),
                "actual_rev": float(r[6] or 0),
                "status": r[4],
                "exec_id": str(r[0]),
                "kid": r[5] or "",
            })

    result.sort(key=lambda x: (x.get("date", ""), x.get("tm", ""), x.get("t", "")))
    return result


# Confirmed May 2026 segment IDs from CLAUDE.md
_SEG_TO_AUDIENCE = {
    "UEQD6k": "lapsed_30d", "UfARWm": "lapsed_60d", "XuS7rY": "lapsed_90d",
    "W98qh3": "lapsed_180d", "RArtzN": "vip", "RvtHdn": "engaged_customers",
    "UBFUcH": "active_seal", "VAUD58": "whales", "Xrp3ha": "engaged_prospects",
    "Sme9Nq": "super_engaged", "QHV2s5": "inner_circle",
    "Y6VSre": "hive_mind_prospects", "XFSxZt": "all_customers",
}


def pull_klaviyo_audience_health() -> list:
    """
    Pull sent campaigns from Klaviyo (last 365 days), map segment_ids → audience,
    fetch revenue/RPR from the reporting API, aggregate by audience.
    Stores result in agent_state['audience_health'] and returns the list.
    """
    import os, time as _t
    import httpx as _httpx

    api_key = os.environ.get("KLAVIYO_API_KEY", "")
    if not api_key:
        return []

    headers = {
        "Authorization": f"Klaviyo-API-Key {api_key}",
        "revision": KLAVIYO_REVISION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    today = date.today()
    cutoff = date(today.year - 1, today.month, today.day)

    # Step 1: List sent campaigns (newest first, stop at 1 year back)
    campaigns_info: dict[str, dict] = {}
    url: str | None = "https://a.klaviyo.com/api/campaigns/"
    params: dict = {
        "filter": "equals(messages.channel,'email'),equals(status,'Sent')",
        "fields[campaign]": "name,send_time,audiences",
        "sort": "-created_at",
        "page[size]": "50",
    }
    page = 0
    # 5 pages × 50 = 250 campaigns, covers ~2 years of weekly sends
    while url and page < 5:
        try:
            resp = _httpx.get(url, headers=headers, params=params, timeout=15)
            if not resp.is_success:
                break
            body = resp.json()
            stop = False
            for item in body.get("data", []):
                attrs = item.get("attributes", {})
                st = attrs.get("send_time") or ""
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    send_date = _dt.fromisoformat(st.replace("Z", "+00:00")).date()
                except Exception:
                    continue
                if send_date < cutoff:
                    stop = True
                    break
                seg_ids = list((attrs.get("audiences") or {}).get("included") or [])
                audience = next((v for k, v in _SEG_TO_AUDIENCE.items() if k in seg_ids), None)
                if audience:
                    campaigns_info[item["id"]] = {"audience": audience, "send_date": send_date}
            url = body.get("links", {}).get("next") if not stop else None
            params = {}
            page += 1
            _t.sleep(0.1)
        except Exception:
            break

    if not campaigns_info:
        return []

    # Step 2: Fetch metrics for all campaigns in one report.
    # group_by campaign_message_id is required by the API; aggregate to campaign level in Python.
    metrics_by_campaign: dict[str, dict] = {}
    try:
        resp = _httpx.post(
            "https://a.klaviyo.com/api/campaign-values-reports/",
            headers=headers,
            json={"data": {"type": "campaign-values-report", "attributes": {
                "statistics": ["recipients", "conversion_value"],
                "timeframe": {"key": "last_365_days"},
                "conversion_metric_id": "X93gjq",
                "group_by": ["campaign_id", "campaign_message_id"],
            }}},
            timeout=60,
        )
        if resp.is_success:
            # Aggregate per-message rows up to campaign level
            agg: dict[str, dict] = {}
            for r in resp.json().get("data", {}).get("attributes", {}).get("results", []):
                cid = r.get("groupings", {}).get("campaign_id", "")
                if not cid or cid not in campaigns_info:
                    continue
                s = r.get("statistics", {})
                if cid not in agg:
                    agg[cid] = {"recipients": 0.0, "revenue": 0.0}
                agg[cid]["recipients"] += float(s.get("recipients", 0) or 0)
                agg[cid]["revenue"] += float(s.get("conversion_value", 0) or 0)
            for cid, a in agg.items():
                rpr = a["revenue"] / a["recipients"] if a["recipients"] > 0 else 0.0
                metrics_by_campaign[cid] = {
                    "recipients": int(a["recipients"]),
                    "revenue": round(a["revenue"], 2),
                    "rpr": round(rpr, 4),
                }
    except Exception:
        pass

    # Step 3: Aggregate by audience
    by_aud: dict[str, dict] = {}
    for cid, info in campaigns_info.items():
        aud = info["audience"]
        sd = info["send_date"]
        rpr = metrics_by_campaign.get(cid, {}).get("rpr", 0)
        if aud not in by_aud:
            by_aud[aud] = {"last_send": sd, "sends_90d": 0, "rprs_90d": []}
        e = by_aud[aud]
        if sd > e["last_send"]:
            e["last_send"] = sd
        if (today - sd).days <= 90:
            e["sends_90d"] += 1
            if rpr > 0:
                e["rprs_90d"].append(rpr)

    result = []
    for aud, data in by_aud.items():
        rprs = data["rprs_90d"]
        rpr_90d = sum(rprs) / len(rprs) if rprs else 0
        days_since = (today - data["last_send"]).days
        health = "RECENT" if days_since < 7 else ("WARM" if days_since < 14 else "FRESH")
        result.append({
            "audience": aud,
            "last_send": str(data["last_send"]),
            "days_since": days_since,
            "rpr_90d": round(rpr_90d, 4),
            "rpr_30d": 0.0,
            "sends_90d": data["sends_90d"],
            "health": health,
        })
    result.sort(key=lambda x: -x["rpr_90d"])

    # Cache in agent_state
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO agent_state (key, value, updated_at) VALUES ('audience_health', %s, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                (json.dumps({"data": result, "as_of": today.isoformat()}),)
            )
            conn.commit()
    except Exception as e:
        print(f"[dashboard] audience_health cache write failed: {e}")

    return result


def _audience_health() -> list:
    # Check agent_state cache (written by pull_klaviyo_audience_health or audience_health worker)
    row = _q1("SELECT value, updated_at FROM agent_state WHERE key='audience_health'")
    if row:
        try:
            d = json.loads(row[0])
            if isinstance(d, list):
                return d  # legacy format
            if isinstance(d, dict) and "data" in d:
                return d["data"]
        except Exception:
            pass

    # Fallback: compute from calendar_executions (works once backfill has run)
    today = date.today()
    rows = _q(
        """SELECT audience,
                  MAX(slot_date) as last_send,
                  COALESCE(AVG(actual_rpr) FILTER (WHERE actual_rpr > 0 AND slot_date > CURRENT_DATE - 90), 0) as rpr_90d,
                  COALESCE(AVG(actual_rpr) FILTER (WHERE actual_rpr > 0 AND slot_date > CURRENT_DATE - 30), 0) as rpr_30d,
                  COUNT(DISTINCT slot_date) FILTER (WHERE slot_date > CURRENT_DATE - 90 AND status IN ('dispatched','completed')) as sends_90d
           FROM calendar_executions
           WHERE status IN ('dispatched','completed') AND audience IS NOT NULL
             AND audience NOT LIKE 'test_%%' AND actual_rpr > 0
           GROUP BY audience
           ORDER BY MAX(slot_date) DESC"""
    )
    if not rows:
        return []

    result = []
    for r in rows:
        last_send = r[1]
        days_since = (today - last_send).days if last_send else 999
        health = "RECENT" if days_since < 7 else ("WARM" if days_since < 14 else "FRESH")
        result.append({
            "audience": r[0],
            "last_send": str(last_send) if last_send else "Never",
            "days_since": days_since,
            "rpr_90d": float(r[2] or 0),
            "rpr_30d": float(r[3] or 0),
            "sends_90d": int(r[4] or 0),
            "health": health,
        })
    return sorted(result, key=lambda x: -x["rpr_90d"])


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
    if rows:
        return [{"a": r[0], "t": r[1], "rv": float(r[2] or 0), "rpr": float(r[3] or 0),
                 "d": str(r[4])} for r in rows]
    # Fallback: read from Klaviyo performance table (ingested historical data)
    rows = _q(
        """SELECT cv.name, cv.revenue, COALESCE(rpr.rpr, 0) as rpr, cv.send_date
           FROM (
             SELECT dimensions->>'entity_name' as name,
                    MAX(metric_value) as revenue,
                    (MAX(dimensions->>'send_time'))::date as send_date,
                    dimensions->>'entity_id' as eid
             FROM performance
             WHERE source='klaviyo' AND metric_name='conversion_value'
               AND dimensions->>'kind'='campaign'
               AND dimensions->>'send_channel'='email'
             GROUP BY dimensions->>'entity_id', dimensions->>'entity_name'
             HAVING MAX(metric_value) > 0
           ) cv
           LEFT JOIN (
             SELECT dimensions->>'entity_id' as eid, AVG(metric_value) as rpr
             FROM performance
             WHERE source='klaviyo' AND metric_name='revenue_per_recipient'
               AND dimensions->>'kind'='campaign'
             GROUP BY dimensions->>'entity_id'
           ) rpr ON rpr.eid = cv.eid
           ORDER BY cv.revenue DESC LIMIT 10"""
    )
    return [{"a": (r[0] or "")[:40], "t": "klaviyo_campaign",
             "rv": float(r[1] or 0), "rpr": float(r[2] or 0), "d": str(r[3] or "")} for r in rows]


def _learning_loop() -> dict:
    rows = _q(
        "SELECT component, strategy_text, created_at FROM strategies "
        "WHERE component='learning_loop' ORDER BY created_at DESC LIMIT 3"
    )
    result: dict = {"entries": [], "rpr_by_audience": {}}
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

    # If no learning_loop strategies yet, fall back to Klaviyo audience health cache
    if not result["rpr_by_audience"]:
        row = _q1("SELECT value FROM agent_state WHERE key='audience_health'")
        if row:
            try:
                d = json.loads(row[0])
                aud_list = d if isinstance(d, list) else d.get("data", [])
                result["rpr_by_audience"] = {
                    a["audience"]: a["rpr_90d"]
                    for a in aud_list if a.get("rpr_90d", 0) > 0
                }
                if result["rpr_by_audience"] and not result["entries"]:
                    as_of = d.get("as_of", "") if isinstance(d, dict) else ""
                    result["entries"].append({
                        "component": "audience_health",
                        "summary": f"90-day RPR by audience — {len(result['rpr_by_audience'])} audiences tracked (from Klaviyo history)",
                        "at": as_of,
                    })
            except Exception:
                pass

    return result


# ── Business / store-wide data (Shopify) ──────────────────────────────────────

def _store_revenue() -> dict:
    """
    Store-wide revenue from Shopify orders (net, post-refund), MTD + 30d trend.
    Deduped by order_id (latest row by measured_at), scoped by order created_at.
    Live data only — no estimates (CLAUDE.md §0).
    """
    mtd_row = _q1(
        """SELECT COALESCE(SUM(rev),0), COUNT(*) FROM (
             SELECT DISTINCT ON (dimensions->>'order_id') metric_value AS rev
             FROM performance
             WHERE source='shopify' AND metric_name='order_revenue'
               AND (dimensions->>'created_at')::timestamptz >= date_trunc('month', CURRENT_DATE)
             ORDER BY dimensions->>'order_id', measured_at DESC
           ) t"""
    )
    store_mtd = float(mtd_row[0]) if mtd_row else 0.0
    order_count = int(mtd_row[1]) if mtd_row else 0
    aov = store_mtd / order_count if order_count else 0.0

    trend_rows = _q(
        """SELECT day, SUM(rev) FROM (
             SELECT DISTINCT ON (dimensions->>'order_id')
                    (dimensions->>'created_at')::date AS day, metric_value AS rev
             FROM performance
             WHERE source='shopify' AND metric_name='order_revenue'
               AND (dimensions->>'created_at')::timestamptz >= CURRENT_DATE - INTERVAL '30 days'
             ORDER BY dimensions->>'order_id', measured_at DESC
           ) t GROUP BY day ORDER BY day"""
    )
    store_trend = [{"date": str(r[0]), "revenue": round(float(r[1] or 0), 2)} for r in trend_rows]

    # Email-attributed (Klaviyo) MTD from the pacing cache
    p = _pacing()
    attributed = p["rev"]
    pct_attributed = (attributed / store_mtd * 100) if store_mtd else 0.0

    return {
        "store_mtd": round(store_mtd, 2),
        "order_count": order_count,
        "aov": round(aov, 2),
        "attributed": round(attributed, 2),
        "campaign_rev": round(p["cr"], 2),
        "flow_rev": round(p["fr"], 2),
        "pct_attributed": round(pct_attributed, 1),
        "store_trend": store_trend,
        "goal": MONTHLY_GOAL,
    }


def _shopify_customer_stats() -> dict:
    """Return returning-customer stats for MTD via paginated Shopify Orders API.
    Cached in agent_state['shopify_customer_stats'] for 4h to avoid repeated pagination."""
    # Check cache first
    cache_row = _q1("SELECT value, updated_at FROM agent_state WHERE key='shopify_customer_stats'")
    if cache_row:
        try:
            cached = json.loads(cache_row[0])
            age_h = (datetime.now(NY) - datetime.fromisoformat(
                str(cache_row[1]).replace(" ", "T").split("+")[0] + "+00:00"
            ).astimezone(NY)).total_seconds() / 3600
            if age_h < 4:
                return cached
        except Exception:
            pass

    # Paginate through MTD orders to compute customer stats
    from lib.shopify_admin import graphql
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    query = """
query($cursor: String) {
  orders(first: 250, after: $cursor,
         query: "created_at:>=""" + month_start + """T00:00:00 financial_status:paid") {
    edges {
      node {
        customer { numberOfOrders }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}"""
    total = 0
    returning = 0
    unique_customers = set()
    cursor = None
    for _ in range(20):  # max 20 pages = 5,000 orders
        try:
            result = graphql(query, variables={"cursor": cursor})
        except Exception as e:
            print(f"[dashboard] Shopify customer stats page failed: {e}")
            break
        edges = result.get("orders", {}).get("edges", [])
        page_info = result.get("orders", {}).get("pageInfo", {})
        for edge in edges:
            total += 1
            n_orders = int(edge["node"].get("customer", {}).get("numberOfOrders", 1) or 1)
            if n_orders > 1:
                returning += 1
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    new = total - returning
    rate = round(returning / total * 100, 1) if total else 0.0
    result_dict = {
        "orders": total,
        "returning_customers": returning,
        "new_customers": new,
        "returning_customer_rate": rate,
    }
    # Cache result
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO agent_state (key, value, updated_at) VALUES ('shopify_customer_stats', %s, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                (json.dumps(result_dict),)
            )
            conn.commit()
    except Exception:
        pass
    return result_dict


def _shopify_store_live() -> dict:
    """MTD store metrics from the performance table (net revenue, fresh from ingestion)
    plus live returning-customer rate from Shopify Orders API (4h cache)."""
    # Revenue + order count from performance table (ingested every 4h)
    mtd_row = _q1(
        """SELECT COALESCE(SUM(rev),0), COUNT(*) FROM (
             SELECT DISTINCT ON (dimensions->>'order_id') metric_value AS rev
             FROM performance
             WHERE source='shopify' AND metric_name='order_revenue'
               AND (dimensions->>'created_at')::timestamptz >= date_trunc('month', CURRENT_DATE)
             ORDER BY dimensions->>'order_id', measured_at DESC
           ) t"""
    )
    net_sales   = round(float(mtd_row[0]) if mtd_row else 0.0, 2)
    order_count = int(mtd_row[1]) if mtd_row else 0
    aov         = round(net_sales / order_count, 2) if order_count else 0.0

    # Customer stats via Orders API (cached 4h)
    cstats = _shopify_customer_stats()

    return {
        "gross_sales": net_sales,   # we only store net; label correctly in HTML
        "net_sales": net_sales,
        "orders": order_count,
        "customers": cstats.get("orders", order_count),
        "returning_customers": cstats.get("returning_customers", 0),
        "new_customers": cstats.get("new_customers", 0),
        "returning_customer_rate": cstats.get("returning_customer_rate", 0.0),
        "aov": aov,
        "_source": "live",
    }


def _pacing_history() -> list:
    """Daily pacing snapshots — actual vs target trajectory for charting."""
    rows = _q(
        """SELECT measured_at::date, period_to_date_value, target_to_date_value,
                  gap_pct, required_daily_rate
           FROM pacing_state ORDER BY measured_at ASC LIMIT 90"""
    )
    return [{
        "date": str(r[0]),
        "actual": round(float(r[1] or 0), 2),
        "target": round(float(r[2] or 0), 2),
        "gap_pct": round(float(r[3] or 0), 2),
        "required_daily": round(float(r[4] or 0), 2),
    } for r in rows]


def _deliverability() -> dict:
    """
    Latest deliverability posture. Prefers the deliverability_monitor strategy
    row; falls back to a live 30-day aggregate from the performance table.
    """
    row = _q1(
        "SELECT strategy_text, created_at FROM strategies "
        "WHERE component='deliverability_monitor' ORDER BY created_at DESC LIMIT 1"
    )
    if row:
        try:
            d = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            if isinstance(d, dict):
                d["_checked_at"] = str(row[1])[:16] if row[1] else ""
                d["_source"] = "monitor"
                return d
        except Exception:
            pass

    agg = _q1(
        """SELECT
             COALESCE(SUM(CASE WHEN metric_name='bounces'      THEN metric_value END),0),
             COALESCE(SUM(CASE WHEN metric_name='unsubscribes' THEN metric_value END),0),
             COALESCE(SUM(CASE WHEN metric_name='recipients'   THEN metric_value END),0),
             COALESCE(SUM(CASE WHEN metric_name='deliveries'   THEN metric_value END),0)
           FROM performance
           WHERE source='klaviyo'
             AND window_start >= CURRENT_DATE - INTERVAL '30 days'
             AND metric_name IN ('bounces','unsubscribes','recipients','deliveries')"""
    )
    if not agg:
        return {"_source": "none"}
    bounces, unsubs, recipients, deliveries = (float(x or 0) for x in agg)
    base = recipients or deliveries or 1
    return {
        "_source": "performance_30d",
        "_checked_at": "",
        "recipients": int(recipients),
        "deliveries": int(deliveries),
        "bounce_rate": round(bounces / base * 100, 3),
        "unsub_rate": round(unsubs / base * 100, 3),
        "delivery_rate": round(deliveries / base * 100, 2) if recipients else 0.0,
    }


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
    "dispatched": ("#1a73e8", "Sent"),
    "completed": ("#1e7e34", "Sent"),
    "failed": ("#c0392b", "Failed"),
    "blocked": ("#e07b00", "Blocked"),
    "planned": ("#888", "Planned"),
    "pending": ("#888", "Pending"),
    "cancelled": ("#555", "Cancelled"),
    "skipped": ("#aaa", "Skipped"),
    "not_sent": ("#ddd", "—"),
}


def _status_badge(status: str) -> str:
    color, label = _STATUS_COLORS.get(status, ("#888", status))
    return f'<span class="badge" style="background:{color}">{label}</span>'


def _ct_chip(ct: str) -> str:
    color = _CT_COLORS.get(ct, "#555")
    label = _CT_LABEL.get(ct, ct)
    return f'<span class="chip" style="background:{color}">{label}</span>'


def _html_command_center(p: dict) -> str:
    # Show load state when no cache data exists yet
    if p["rev"] == 0 and not p.get("as_of"):
        return f"""
    <div class="card command-center">
      <div class="cc-stats" style="flex:1;text-align:center;padding:32px 20px">
        <div class="hero-num" style="color:#ccc;font-size:28px">Revenue not loaded</div>
        <div style="color:#aaa;font-size:13px;margin-top:8px">
          Cache refreshes automatically at 7:35am ET daily.<br>Click to pull from Klaviyo now.
        </div>
        <button class="btn-approve" style="margin-top:20px;max-width:280px"
                onclick="apiPost('/api/refresh-pacing','Revenue loaded!')">
          Load Revenue Data
        </button>
        <div style="color:#bbb;font-size:11px;margin-top:10px">
          Goal: ${p["goal"]:,}/month · {p["days_left"]}d remaining
        </div>
      </div>
    </div>"""

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

    if p.get("as_of"):
        as_of_str = f" · as of {p['as_of'][:16]}"
        if p.get("stale"):
            as_of_str += " ⚠ stale — <a href='#' onclick=\"apiPost('/api/refresh-pacing','Revenue refreshed!');return false\">refresh</a>"
    else:
        as_of_str = ""
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
          <button class="btn-boost" onclick="apiPost('/api/boost','Boost activated! Check Slack for details.')">Boost Revenue Now</button>
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
            retry_btn = f'<button class="btn-retry" onclick="apiPost(\'/api/retry-slot?id={s["id"]}\',\'Slot queued for retry.\')">Retry</button>'
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
        <button class="btn-approve" onclick="apiPost('/api/approve-week','Approved! Campaigns will run automatically each morning.')">Approve This Week</button>
        <div class="approval-note">Or type <code>approved week</code> in #beezy-agents</div>"""

    plan_status = "✓ Calendar plan exists for this month" if apv["month_has_plan"] else "⚠ No calendar plan — type <code>generate calendar</code> in Slack"
    month_approve_html = ""
    if apv["month_has_plan"]:
        month_approve_html = """
        <button class="btn-approve" style="background:#555;font-size:12px;padding:8px;margin-top:10px"
                onclick="apiPost('/api/approve-month','All weeks approved!')">
          Approve All Weeks This Month
        </button>"""

    return f"""
    <div class="card">
      <div class="card-title">Approval Center</div>
      {approval_html}
      <div class="plan-status">{plan_status}</div>
      {month_approve_html}
    </div>"""


def _html_calendar(slots: list) -> str:
    today = date.today()
    month_label = today.strftime("%B %Y")
    if not slots:
        return f"""
        <div class="card">
          <div class="card-title">{month_label} Calendar</div>
          <div class="empty-state">No upcoming slots found. Generate a calendar first.</div>
        </div>"""

    total_est = sum(s["rv"] for s in slots if s["t"] not in ("seo_blog", "flow_experiment"))
    total_actual = sum(s.get("actual_rev", 0) for s in slots if s["t"] not in ("seo_blog", "flow_experiment"))
    today_iso = today.isoformat()
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
        is_past = d < today_iso
        row_class = "cal-row today-row" if is_today else ("cal-row past-row" if is_past else "cal-row")
        retry_btn = ""
        if s["exec_id"] and s["status"] in ("failed", "blocked"):
            eid = s["exec_id"]
            retry_btn = f'<button class="btn-retry-sm" onclick="apiPost(\'/api/retry-slot?id={eid}\',\'Queued.\')">↺</button>'
        ct_color = _CT_COLORS.get(s["t"], "#555")
        if s["status"] == "not_sent":
            ct_color = "#ccc"
        est_str = f'${s["rv"]:,.0f}' if s["rv"] else "—"
        actual_rev = s.get("actual_rev", 0)
        if actual_rev > 0:
            pct_diff = (actual_rev - s["rv"]) / s["rv"] * 100 if s["rv"] else 0
            diff_color = "#1e7e34" if actual_rev >= s["rv"] else "#c0392b"
            diff_str = f'<span style="color:{diff_color};font-size:11px">({pct_diff:+.0f}%)</span>'
            actual_str = f'${actual_rev:,.0f} {diff_str}'
        elif s["status"] in ("dispatched", "completed") and d <= today_iso:
            actual_str = '<span style="color:#aaa;font-size:11px">pending backfill</span>'
        else:
            actual_str = "—"
        rows_html += f"""
        <tr class="{row_class}" style="border-left:3px solid {ct_color}">
          <td class="cal-date"><strong>{d_label}</strong>{"<span class='today-tag'>TODAY</span>" if is_today else ""}</td>
          <td>{_ct_chip(s["t"])}</td>
          <td>{s["a"]}</td>
          <td class="cal-topic">{s["tp"]}</td>
          <td>{s["tm"]} ET</td>
          <td>{est_str}</td>
          <td>{actual_str}</td>
          <td>{_status_badge(s["status"])} {retry_btn}</td>
        </tr>"""

    meta = f"Est: ${total_est:,.0f}"
    if total_actual > 0:
        meta += f" · Actual: ${total_actual:,.0f}"
    return f"""
    <div class="card">
      <div class="card-title">{month_label} Calendar
        <span class="card-title-meta">{meta}</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Date</th><th>Type</th><th>Audience</th><th>Topic</th><th>Time</th><th>Est. Rev</th><th>Actual</th><th>Status</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </div>"""


def _html_audience_health(data: list) -> str:
    if not data:
        return """
        <div class="card">
          <div class="card-title">Audience Health</div>
          <div class="empty-state" style="padding:20px 0">
            No audience data cached yet.<br>
            <small style="color:#bbb">Loads from Klaviyo history — 90-day RPR per audience.</small>
          </div>
          <button class="btn-approve" style="margin-top:12px"
                  onclick="apiPost('/api/refresh-audience-health','Audience health loaded! Refreshing...')">
            Load Audience History from Klaviyo
          </button>
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
          <div class="empty-state" style="padding:20px 0">
            No flow data yet — runs weekly Sunday 9:15pm.<br>
            <small style="color:#bbb">Or click below to run now (pulls 30-day Klaviyo flow metrics).</small>
          </div>
          <button class="btn-approve" style="margin-top:12px"
                  onclick="apiPost('/api/run-flow-check','Flow check running! Takes ~30s. Refresh in a moment.')">
            Run Flow Health Check Now
          </button>
        </div>"""

    checked_at = data.get("_checked_at", "")
    analyses = data.get("analyses", [])
    if not analyses:
        return f"""
        <div class="card">
          <div class="card-title">Flow Health <span class="card-title-meta">{checked_at}</span></div>
          <div class="empty-state">No flow analysis available.</div>
          <button class="btn-approve" style="margin-top:12px;font-size:12px;padding:8px"
                  onclick="apiPost('/api/run-flow-check','Flow check running! Refresh in a moment.')">
            Re-run Flow Check
          </button>
        </div>"""

    rows_html = ""
    _SEV_COLOR = {"ok": "#1e7e34", "warn": "#e07b00", "critical": "#c0392b"}
    _SEV_LABEL = {"ok": "HEALTHY", "warn": "WARNING", "critical": "CRITICAL"}
    for a in analyses[:10]:
        sev = a.get("severity", "ok")
        sev_color = _SEV_COLOR.get(sev, "#888")
        sev_label = _SEV_LABEL.get(sev, sev.upper())
        fix_badge = '<span class="badge" style="background:#7b2d8b">Fix queued</span>' if a.get("fix_queued") else ""
        rows_html += f"""
        <tr>
          <td>{a.get("name","?")[:28]}</td>
          <td>${float(a.get("revenue",0)):,.0f}</td>
          <td>${float(a.get("rpr",0)):.2f}</td>
          <td><span class="badge" style="background:{sev_color}">{sev_label}</span> {fix_badge}</td>
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


def _html_store_perf(store: dict) -> str:
    if store.get("_source") == "error":
        return f"""
    <div class="card">
      <div class="card-title">Store Performance (MTD)</div>
      <div class="empty-state">Could not load live Shopify data: {store.get("_error","")[:80]}</div>
    </div>"""

    net    = store["net_sales"]
    orders = store["orders"]
    ret    = store["returning_customers"]
    new_c  = store.get("new_customers", 0)
    rate   = store["returning_customer_rate"]
    aov    = store["aov"]

    rate_color = "#1e7e34" if rate >= 60 else ("#e07b00" if rate >= 40 else "#c0392b")

    return f"""
    <div class="card">
      <div class="card-title">Store Performance (MTD — live Shopify)
        <span class="card-title-meta">revenue from ingestion · customer rate from Orders API</span>
      </div>
      <div class="cc-grid" style="grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:12px">
        <div class="cc-stat">
          <div class="stat-val">${net:,.0f}</div>
          <div class="stat-lbl">Net Revenue MTD</div>
        </div>
        <div class="cc-stat">
          <div class="stat-val">{orders:,}</div>
          <div class="stat-lbl">Orders MTD</div>
        </div>
        <div class="cc-stat">
          <div class="stat-val">${aov:,.0f}</div>
          <div class="stat-lbl">Avg Order Value</div>
        </div>
      </div>
      <div class="cc-grid" style="grid-template-columns:repeat(3,1fr);gap:10px">
        <div class="cc-stat">
          <div class="stat-val" style="color:{rate_color}">{rate:.1f}%</div>
          <div class="stat-lbl">Repeat Customer Rate</div>
        </div>
        <div class="cc-stat">
          <div class="stat-val">{ret:,}</div>
          <div class="stat-lbl">Returning Customers</div>
        </div>
        <div class="cc-stat">
          <div class="stat-val">{new_c:,}</div>
          <div class="stat-lbl">New Customers</div>
        </div>
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
.past-row{opacity:0.75}
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

@router.get("/dashboard-classic", response_class=HTMLResponse)
def dashboard():
    now = datetime.now(NY)
    p = _pacing()
    today_slots = _today_slots()
    next_send = _next_send_date() if not today_slots else ""
    apv = _approval_status()
    upcoming = _upcoming_slots()
    audience_health = _audience_health()
    flows = _flow_health()
    performers = _top_performers()
    learning = _learning_loop()
    store = _shopify_store_live()

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
  {_html_store_perf(store)}
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
<script>
function apiPost(url, successMsg) {{
  fetch(url, {{method: 'POST'}})
    .then(r => r.json())
    .then(d => {{ if (d.error) {{ alert('Error: ' + d.error); }} else {{ alert(successMsg); location.reload(); }} }})
    .catch(e => alert('Request failed: ' + e));
}}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)
