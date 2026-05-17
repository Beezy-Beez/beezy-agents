"""Morning briefing — posts a daily Slack digest at 8:05am ET.

Format (max 15 lines):
  🌅 Good morning. Here's today.
  💰 Revenue MTD: $X / $150K (XX%)
  📈 Pace: AHEAD by $X | ON TRACK | BEHIND $X — need $X/day
  📧 Today's sends: ...
  ⚠️ Action needed: ...
  📊 Dashboard: https://[domain]/dashboard
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

MONTHLY_GOAL = 150_000
REPLIT_DOMAIN = os.environ.get("REPLIT_DOMAIN", "beezy-agents-ingestion.replit.app")

_CONTENT_LABEL = {
    "klaviyo_campaign": "Email",
    "sniper_followup":  "Follow-up email",
    "hive_mind":        "Hive Mind newsletter",
    "seo_blog":         "SEO blog post",
    "sleep_audio":      "Sleep audio episode",
    "sms_campaign":     "SMS",
    "flow_experiment":  "Flow experiment",
}
_AUDIENCE_LABEL = {
    "lapsed_30d": "Lapsed 30d customers", "lapsed_60d": "Lapsed 60d customers",
    "lapsed_90d": "Lapsed 90d customers", "lapsed_180d": "Lapsed 180d customers",
    "vip": "VIP customers", "inner_circle": "Inner Circle", "whales": "Whales",
    "high_aov": "High-AOV customers", "engaged_customers": "Engaged customers",
    "all_customers": "All customers", "active_seal": "Active Seal members",
    "active_subscribers": "Active subscribers", "one_time_buyers": "One-time buyers",
    "otb": "One-time buyers", "cart_abandoners": "Cart abandoners",
    "engaged_prospects": "Engaged prospects", "super_engaged": "Super engaged prospects",
    "hive_mind_prospects": "Hive Mind prospects",
}
_CONTENT_EMOJI = {
    "hive_mind": "🌙", "klaviyo_campaign": "📧", "sniper_followup": "⚡",
    "seo_blog": "📝", "sleep_audio": "🎙", "sms_campaign": "📱", "flow_experiment": "🔬",
}


def _fmt_time(t: str) -> str:
    try:
        h, m = int(t[:2]), int(t[3:5])
        suffix = "am" if h < 12 else "pm"
        h12 = h % 12 or 12
        return (str(h12) + f":{m:02d}" + suffix) if m else (str(h12) + suffix)
    except Exception:
        return t


def _pacing_data() -> dict:
    """Read pacing cache; fall back to performance table for zero values."""
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            row = conn.execute("SELECT value FROM agent_state WHERE key='pacing_cache'").fetchone()
            cache = json.loads(row[0]) if row else {}
            cr = float(cache.get("campaign_rev", 0))
            fr = float(cache.get("flow_rev", 0))
            if cr == 0:
                r = conn.execute(
                    "SELECT COALESCE(SUM(metric_value),0) FROM performance "
                    "WHERE source='klaviyo' AND metric_name='conversion_value' "
                    "AND dimensions->>'kind'='campaign' AND measured_at >= date_trunc('month',NOW())"
                ).fetchone()
                cr = float(r[0] or 0) if r else 0
            if fr == 0:
                r = conn.execute(
                    "SELECT COALESCE(SUM(metric_value),0) FROM performance "
                    "WHERE source='klaviyo' AND metric_name='conversion_value' "
                    "AND dimensions->>'kind'='flow' AND measured_at >= date_trunc('month',NOW())"
                ).fetchone()
                fr = float(r[0] or 0) if r else 0
            cache["campaign_rev"] = cr
            cache["flow_rev"] = fr
            return cache
    except Exception:
        pass
    return {}


def _pacing_state() -> dict | None:
    """Read the latest pacing_state row + active goal target.

    This is the SAME source the 7:30am pacing brain digest uses (Shopify
    order revenue vs target). The morning brief MUST report the same numbers
    — comparing Klaviyo-attributed revenue against the store goal made pacing
    look ~$34K worse than reality (CLAUDE.md §0).
    """
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT g.target_value, ps.period_to_date_value, ps.target_to_date_value, "
                "       ps.gap_pct, ps.required_daily_rate, ps.days_remaining, ps.measured_at "
                "FROM pacing_state ps JOIN goals g ON g.id = ps.goal_id "
                "WHERE g.status = 'active' "
                "ORDER BY ps.measured_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return {
            "target": float(row[0] or 0),
            "ptd": float(row[1] or 0),
            "ttd": float(row[2] or 0),
            "gap_pct": float(row[3] or 0),
            "required_daily": float(row[4] or 0),
            "days_remaining": int(row[5] or 1),
            "measured_at": row[6],
        }
    except Exception:
        return None


def _today_executions() -> list:
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT content_type, audience, topic_angle, status, actual_revenue "
                "FROM calendar_executions WHERE slot_date=%s ORDER BY executed_at",
                (date.today(),)
            ).fetchall()
        return [{"t": r[0], "a": r[1], "tp": r[2] or "", "s": r[3], "rv": float(r[4] or 0)} for r in rows]
    except Exception:
        return []


def _failed_slots() -> list:
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, audience, content_type, notes FROM calendar_executions "
                "WHERE slot_date=%s AND status IN ('failed','blocked')",
                (date.today(),)
            ).fetchall()
        return [{"id": str(r[0]), "a": r[1], "t": r[2], "reason": (r[3] or "")[:60]} for r in rows]
    except Exception:
        return []


def _week_approved() -> bool:
    today = date.today()
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM calendar_approvals WHERE week_start <= %s AND %s < week_start + INTERVAL '7 days' AND approved_at IS NOT NULL LIMIT 1",
                (today, today)
            ).fetchone()
        return row is not None
    except Exception:
        return False


def _today_planned_slots() -> list:
    """Read planned slots for today from the decisions table (has send_time_est + revenue_estimate)."""
    today = date.today()
    month = today.strftime("%Y-%m")
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT output FROM decisions WHERE decision_type='calendar_plan' AND output->>'month'=%s ORDER BY created_at DESC LIMIT 1",
                (month,)
            ).fetchone()
        if not row:
            return []
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        slots = payload.get("slots", [])
        return [s for s in slots if s.get("date") == today.isoformat()]
    except Exception:
        return []


def _daily_priority_mode() -> str | None:
    """Read today's priority mode from decisions table (written by compute_daily_priorities)."""
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT output FROM decisions WHERE decision_type='daily_priority' AND (output->>'date')=%s ORDER BY created_at DESC LIMIT 1",
                (date.today().isoformat(),)
            ).fetchone()
        if row:
            d = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return d.get("mode")
    except Exception:
        pass
    return None


def run_morning_brief() -> None:
    from lib.slack import _post as slack_post

    today = date.today()
    import calendar as _cal
    last_day = _cal.monthrange(today.year, today.month)[1]
    days_elapsed = today.day
    days_left = max(last_day - today.day, 1)

    # Primary source: pacing_state (same Shopify-order-revenue basis as the
    # 7:30am pacing brain). Both posts MUST agree.
    ps = _pacing_state()
    if ps:
        goal = ps["target"] or MONTHLY_GOAL
        rev = ps["ptd"]
        pct = rev / goal * 100 if goal else 0
        linear_expected = ps["ttd"]
        gap = rev - linear_expected
        daily_needed = ps["required_daily"]
        threshold = max(linear_expected * 0.05, 1)
        if gap > threshold:
            pace_line = f"📈 *Pace:* AHEAD by ${abs(gap):,.0f} — looking good"
        elif gap > -threshold:
            pace_line = f"📈 *Pace:* ON TRACK — ${daily_needed:,.0f}/day needed"
        else:
            pace_line = f"📉 *Pace:* BEHIND by ${abs(gap):,.0f} — need ${daily_needed:,.0f}/day to recover"
    else:
        # Fallback only if pacing brain hasn't run yet today — flagged as preliminary.
        cache = _pacing_data()
        goal = MONTHLY_GOAL
        rev = float(cache.get("campaign_rev", 0)) + float(cache.get("flow_rev", 0))
        pct = rev / MONTHLY_GOAL * 100
        daily_needed = max(MONTHLY_GOAL - rev, 0) / days_left
        linear_expected = MONTHLY_GOAL * days_elapsed / (days_elapsed + days_left)
        gap = rev - linear_expected
        if gap > linear_expected * 0.05:
            pace_line = f"📈 *Pace:* AHEAD by ${abs(gap):,.0f} (preliminary — pacing brain pending)"
        elif gap > -(linear_expected * 0.05):
            pace_line = f"📈 *Pace:* ON TRACK — ${daily_needed:,.0f}/day (preliminary)"
        else:
            pace_line = f"📉 *Pace:* BEHIND by ${abs(gap):,.0f} — need ${daily_needed:,.0f}/day (preliminary)"

    # Today's sends from decisions (planned) merged with executions (actual)
    planned = _today_planned_slots()
    executed = _today_executions()
    exec_map = {(e["t"], e["a"]): e for e in executed}

    send_lines = []
    for s in planned:
        ct  = s.get("content_type", "?")
        aud = s.get("audience", "?")
        raw_tm = (s.get("send_time_est") or "").strip()
        tm  = _fmt_time(raw_tm) if raw_tm else ""
        rv  = float(s.get("revenue_estimate", 0) or 0)
        ex  = exec_map.get((ct, aud))
        emoji    = _CONTENT_EMOJI.get(ct, "•")
        label    = _CONTENT_LABEL.get(ct, ct)
        aud_nice = _AUDIENCE_LABEL.get(aud, aud.replace("_", " ").title())
        done_str = " ✅" if ex and ex["s"] in ("dispatched", "completed") else ""
        rv_str   = f" — est. ${rv:,.0f}" if rv else ""
        tm_str   = f" at {tm}" if tm else ""
        send_lines.append(f"  {emoji} {label} → {aud_nice}{tm_str}{rv_str}{done_str}")

    if not send_lines:
        # Check executions in case orchestrator already ran
        for e in executed:
            label    = _CONTENT_LABEL.get(e["t"], e["t"])
            aud_nice = _AUDIENCE_LABEL.get(e["a"], e["a"].replace("_", " ").title())
            send_lines.append(f"  • {label} → {aud_nice} — {e['s']}")

    # Action items
    action_lines = []
    approved = _week_approved()
    if not approved:
        next_monday = today + timedelta(days=(7 - today.weekday()))
        action_lines.append(f"  • Week of {next_monday.strftime('%b %d')} needs approval → type `approved week`")

    failed = _failed_slots()
    for f in failed:
        label    = _CONTENT_LABEL.get(f["t"], f["t"])
        aud_nice = _AUDIENCE_LABEL.get(f["a"], f["a"].replace("_", " ").title()) if f["a"] else ""
        action_lines.append(f"  • ❌ {label}{' / ' + aud_nice if aud_nice else ''} failed — {f['reason']}")

    priority_mode = _daily_priority_mode()
    if priority_mode and priority_mode != "maintain":
        mode_labels = {"boost": "BOOST MODE 🔥", "push": "PUSH MODE 💪", "ease": "EASE MODE 😌"}
        action_lines.append(f"  • Today's focus: {mode_labels.get(priority_mode, priority_mode)}")

    lines = [
        f"🌅 *Good morning. Here's today.*",
        f"",
        f"💰 *Revenue MTD:* ${rev:,.0f} / ${goal:,.0f} ({pct:.1f}%)",
        pace_line,
        f"",
    ]

    if send_lines:
        lines.append(f"📧 *Today's sends:*")
        lines.extend(send_lines)
    else:
        # Check next send date
        try:
            from db.connection import get_conn
            with get_conn() as conn:
                nr = conn.execute(
                    "SELECT slot_date FROM calendar_executions WHERE slot_date > %s ORDER BY slot_date LIMIT 1",
                    (today,)
                ).fetchone()
            next_str = nr[0].strftime("%a %b %d") if nr and nr[0] else "soon"
        except Exception:
            next_str = "soon"
        lines.append(f"😴 *Today:* Rest day — batteries recharging. Next send: {next_str}")

    lines.append("")

    if action_lines:
        lines.append("⚠️ *Action needed:*")
        lines.extend(action_lines)
    else:
        lines.append("✅ *Nothing needed — system is running.*")

    lines.append("")
    lines.append(f"📊 *Dashboard:* https://{REPLIT_DOMAIN}/dashboard")

    slack_post({"blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}]})
    print(f"[morning_brief] Posted daily briefing — {today.isoformat()}")

    # sleep-science-hub is audio-only; no Hive Mind featured box
