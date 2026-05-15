"""
Learning loop — three cadences of self-correction.

Weekly (Sunday 9pm):
  - Pull last 7 days of actual vs projected revenue
  - Identify what over/underperformed
  - Auto-adjust next week's calendar: increase high-RPR, reduce low-RPR
  - Post digest to Slack

Bi-weekly (15th at 9am):
  - Mid-month pacing check against $150K goal
  - If behind: increase frequency to top performers
  - If ahead: ease off lowest-RPR segments
  - Post adjustment to Slack

Monthly (1st at 9am):
  - Full month retrospective
  - Update RPR table with actual 30-day data
  - Feed constraints into next month's calendar generation
  - Post full report to Slack

Usage:
    from workers.learning_loop import run_weekly, run_biweekly, run_monthly
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

# Revenue goal
MONTHLY_GOAL = 150_000


# ── Data pull helpers ──────────────────────────────────────────────────────────

def _get_week_performance(conn, start: date, end: date) -> list[dict]:
    """Pull finalized calendar_executions for a date range."""
    rows = conn.execute(
        """SELECT slot_date, content_type, audience, actual_revenue, recipients,
                  actual_rpr, notes, revenue_estimate
           FROM calendar_executions
           WHERE slot_date BETWEEN %s AND %s
             AND status IN ('dispatched', 'completed')
           ORDER BY slot_date, audience""",
        (start, end)
    ).fetchall()
    return [
        {"date": str(r[0]), "content_type": r[1], "audience": r[2],
         "actual_revenue": float(r[3] or 0), "recipients": int(r[4] or 0),
         "actual_rpr": float(r[5] or 0), "notes": r[6] or "",
         "projected_revenue": float(r[7] or 0)}
        for r in rows
    ]


def _get_month_totals(conn, month_start: date, month_end: date) -> dict:
    """Aggregate revenue for the month so far."""
    row = conn.execute(
        """SELECT COALESCE(SUM(actual_revenue), 0),
                  COUNT(*) FILTER (WHERE actual_revenue > 0),
                  COUNT(*),
                  COALESCE(AVG(actual_rpr) FILTER (WHERE actual_rpr > 0), 0)
           FROM calendar_executions
           WHERE slot_date BETWEEN %s AND %s
             AND status IN ('dispatched', 'completed')""",
        (month_start, month_end)
    ).fetchone()
    return {
        "total_revenue": float(row[0]),
        "campaigns_with_revenue": int(row[1]),
        "total_campaigns": int(row[2]),
        "avg_rpr": float(row[3]),
    }


def _get_top_performers(conn, days: int = 30, limit: int = 5) -> list[dict]:
    """Top audience/content_type combos by actual RPR in the last N days."""
    cutoff = date.today() - timedelta(days=days)
    rows = conn.execute(
        """SELECT audience, content_type,
                  AVG(actual_rpr) as avg_rpr,
                  SUM(actual_revenue) as total_rev,
                  COUNT(*) as sends
           FROM calendar_executions
           WHERE slot_date > %s AND actual_rpr > 0 AND is_preliminary = false
           GROUP BY audience, content_type
           HAVING COUNT(*) >= 2
           ORDER BY avg_rpr DESC
           LIMIT %s""",
        (cutoff, limit)
    ).fetchall()
    return [
        {"audience": r[0], "content_type": r[1], "avg_rpr": float(r[2]),
         "total_rev": float(r[3]), "sends": int(r[4])}
        for r in rows
    ]


def _get_underperformers(conn, days: int = 30, limit: int = 5) -> list[dict]:
    """Lowest RPR audience/content_type combos."""
    cutoff = date.today() - timedelta(days=days)
    rows = conn.execute(
        """SELECT audience, content_type,
                  AVG(actual_rpr) as avg_rpr,
                  SUM(actual_revenue) as total_rev,
                  COUNT(*) as sends
           FROM calendar_executions
           WHERE slot_date > %s AND is_preliminary = false AND status IN ('dispatched','completed')
           GROUP BY audience, content_type
           HAVING COUNT(*) >= 2
           ORDER BY avg_rpr ASC
           LIMIT %s""",
        (cutoff, limit)
    ).fetchall()
    return [
        {"audience": r[0], "content_type": r[1], "avg_rpr": float(r[2]),
         "total_rev": float(r[3]), "sends": int(r[4])}
        for r in rows
    ]


# ── A/B subject pattern learning ──────────────────────────────────────────────

def _update_subject_patterns(conn, perf: list[dict]) -> None:
    """Read finalized calendar_executions notes, compute RPR by subject_type per audience,
    write winner to agent_state['subject_patterns'] when ≥2 sends per type exist."""
    by_aud_type: dict[tuple, list] = {}
    for p in perf:
        notes = p.get("notes", "") or ""
        rpr   = p.get("actual_rpr", 0) or 0
        if "subject_type:" not in notes or rpr <= 0:
            continue
        subject_type = None
        for part in notes.split("|"):
            part = part.strip()
            if part.startswith("subject_type:"):
                subject_type = part.split(":", 1)[1].strip()
                break
        if not subject_type:
            continue
        by_aud_type.setdefault((p["audience"], subject_type), []).append(rpr)

    if not by_aud_type:
        return

    row = conn.execute("SELECT value FROM agent_state WHERE key='subject_patterns'").fetchone()
    sp = json.loads(row[0]) if row else {}

    audiences = {aud for aud, _ in by_aud_type}
    for aud in audiences:
        c_rprs = by_aud_type.get((aud, "curiosity"), [])
        b_rprs = by_aud_type.get((aud, "benefit"),   [])
        aud_data = sp.get(aud, {})
        if c_rprs:
            aud_data["avg_rpr_curiosity"] = round(sum(c_rprs) / len(c_rprs), 4)
            aud_data["sends_curiosity"]   = len(c_rprs)
        if b_rprs:
            aud_data["avg_rpr_benefit"] = round(sum(b_rprs) / len(b_rprs), 4)
            aud_data["sends_benefit"]   = len(b_rprs)
        if len(c_rprs) >= 2 and len(b_rprs) >= 2:
            winner = "curiosity" if aud_data.get("avg_rpr_curiosity", 0) >= aud_data.get("avg_rpr_benefit", 0) else "benefit"
            aud_data["winning_type"] = winner
            print(f"[learning_loop/patterns] {aud}: curiosity ${aud_data.get('avg_rpr_curiosity',0):.4f} "
                  f"vs benefit ${aud_data.get('avg_rpr_benefit',0):.4f} → {winner}")
        sp[aud] = aud_data

    conn.execute(
        "INSERT INTO agent_state (key, value, updated_at) VALUES ('subject_patterns', %s, NOW()) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
        (json.dumps(sp),)
    )
    conn.commit()
    print(f"[learning_loop/patterns] Updated subject_patterns for {len(audiences)} audience(s)")


# ── Weekly review (Sunday 9pm) ─────────────────────────────────────────────────

def run_weekly() -> str:
    """
    Weekly performance review + next-week adjustment.
    Posts digest to Slack. Returns summary string.
    """
    from db.connection import get_conn
    from lib.slack import post_draft

    today = date.today()
    week_start = today - timedelta(days=7)

    with get_conn() as conn:
        perf = _get_week_performance(conn, week_start, today)
        tops = _get_top_performers(conn, days=14)
        lows = _get_underperformers(conn, days=14)

        # Calculate week stats
        total_rev = sum(p["actual_revenue"] for p in perf)
        total_projected = sum(p["projected_revenue"] for p in perf)
        campaigns_sent = len(perf)
        campaigns_with_rev = len([p for p in perf if p["actual_revenue"] > 0])

        # By audience breakdown
        audience_rev = {}
        for p in perf:
            aud = p["audience"]
            if aud not in audience_rev:
                audience_rev[aud] = {"revenue": 0, "sends": 0, "rpr_sum": 0}
            audience_rev[aud]["revenue"] += p["actual_revenue"]
            audience_rev[aud]["sends"] += 1
            audience_rev[aud]["rpr_sum"] += p["actual_rpr"]

        # Pacing
        month_start = today.replace(day=1)
        month_totals = _get_month_totals(conn, month_start, today)
        days_elapsed = (today - month_start).days + 1
        days_in_month = 30  # approximate
        daily_needed = (MONTHLY_GOAL - month_totals["total_revenue"]) / max(days_in_month - days_elapsed, 1)

        # Build Slack report
        lines = [
            f"*Weekly Review — {week_start.strftime('%b %d')} to {today.strftime('%b %d')}*",
            "",
            f"📊 *This week:* ${total_rev:,.0f} actual vs ${total_projected:,.0f} projected",
            f"📧 {campaigns_sent} campaigns sent, {campaigns_with_rev} generated revenue",
            "",
            "*By audience:*",
        ]
        for aud, data in sorted(audience_rev.items(), key=lambda x: -x[1]["revenue"]):
            avg_rpr = data["rpr_sum"] / max(data["sends"], 1)
            lines.append(f"  {aud}: ${data['revenue']:,.0f} ({data['sends']} sends, ${avg_rpr:.3f} RPR)")

        lines.append("")
        lines.append(f"*Month pacing:* ${month_totals['total_revenue']:,.0f} / ${MONTHLY_GOAL:,} "
                     f"({month_totals['total_revenue']/MONTHLY_GOAL*100:.1f}%)")
        lines.append(f"*Daily needed:* ${daily_needed:,.0f}/day for rest of month")

        if tops:
            lines.append("")
            lines.append("*Top performers (14d):*")
            for t in tops[:3]:
                lines.append(f"  🟢 {t['audience']}/{t['content_type']}: ${t['avg_rpr']:.3f} RPR, ${t['total_rev']:,.0f} total")

        if lows:
            lines.append("")
            lines.append("*Underperformers (14d):*")
            for l in lows[:3]:
                lines.append(f"  🔴 {l['audience']}/{l['content_type']}: ${l['avg_rpr']:.4f} RPR, ${l['total_rev']:,.0f} total")

        # Auto-adjustment recommendations
        lines.append("")
        lines.append("*Next week adjustments:*")
        if daily_needed > 5000:
            lines.append("  ⚠️ Significantly behind pace. Recommend +2 sends to top RPR segments.")
        elif daily_needed > 3000:
            lines.append("  📈 Behind pace. Recommend +1 send to VIP or lapsed_30d.")
        elif month_totals["total_revenue"] / MONTHLY_GOAL > 0.9:
            lines.append("  ✅ On track. Maintain current cadence.")
        else:
            lines.append("  📊 Moderate pace. Hold steady, monitor daily.")

        # Close A/B feedback loop: parse subject_type from notes, update winner in subject_patterns
        try:
            _update_subject_patterns(conn, perf)
        except Exception as sp_err:
            print(f"[learning_loop] subject_patterns update failed (non-fatal): {sp_err}")

        report = "\n".join(lines)

        post_draft(
            title="📊 Weekly Review — " + today.strftime("%b %d, %Y"),
            summary_lines=[report],
            body="",
        )

        return report


# ── Bi-weekly pacing check (15th at 9am) ───────────────────────────────────────

def run_biweekly() -> str:
    """
    Mid-month pacing check. Are we on track for $150K?
    If behind: recommend increasing frequency to high-RPR segments.
    If ahead: recommend easing off low-RPR segments.
    """
    from db.connection import get_conn
    from lib.slack import post_draft

    today = date.today()
    month_start = today.replace(day=1)

    with get_conn() as conn:
        totals = _get_month_totals(conn, month_start, today)
        tops = _get_top_performers(conn, days=14)
        lows = _get_underperformers(conn, days=14)

        pct = totals["total_revenue"] / MONTHLY_GOAL * 100
        days_elapsed = (today - month_start).days + 1
        days_left = 30 - days_elapsed
        daily_needed = (MONTHLY_GOAL - totals["total_revenue"]) / max(days_left, 1)

        lines = [
            f"*Mid-Month Pacing — {today.strftime('%B %d')}*",
            "",
            f"💰 *Revenue MTD:* ${totals['total_revenue']:,.0f} / ${MONTHLY_GOAL:,} ({pct:.1f}%)",
            f"📧 *Campaigns:* {totals['total_campaigns']} sent, {totals['campaigns_with_revenue']} with revenue",
            f"📊 *Avg RPR:* ${totals['avg_rpr']:.4f}",
            f"🎯 *Daily needed:* ${daily_needed:,.0f}/day for remaining {days_left} days",
            "",
        ]

        if pct < 40:  # behind at midpoint
            lines.append("🔴 *BEHIND PACE* — calendar adjustment needed:")
            lines.append("  → Increase sends to top RPR segments")
            if tops:
                for t in tops[:3]:
                    lines.append(f"    + More {t['audience']}/{t['content_type']} (${t['avg_rpr']:.3f} RPR)")
            lines.append("  → Reduce or pause lowest performers")
            if lows:
                for l in lows[:2]:
                    lines.append(f"    - Pause {l['audience']}/{l['content_type']} (${l['avg_rpr']:.4f} RPR)")
        elif pct < 50:
            lines.append("🟡 *SLIGHTLY BEHIND* — minor adjustment:")
            lines.append("  → Add 1-2 sends/week to VIP and lapsed_30d")
        elif pct >= 50:
            lines.append("🟢 *ON TRACK* — maintain current cadence")
        else:
            lines.append("🟢 *AHEAD OF PACE* — can ease off lapsed 90d+")

        report = "\n".join(lines)

        post_draft(
            title="📈 Mid-Month Pacing — " + today.strftime("%b %d, %Y"),
            summary_lines=[report],
            body="",
        )

        return report


# ── Monthly retrospective (1st at 9am) ────────────────────────────────────────

def run_monthly() -> str:
    """
    Full month retrospective. Updates RPR table. Feeds into next calendar.
    """
    from db.connection import get_conn
    from lib.slack import post_draft

    today = date.today()
    # Last month
    if today.month == 1:
        last_month_start = date(today.year - 1, 12, 1)
    else:
        last_month_start = date(today.year, today.month - 1, 1)
    last_month_end = today.replace(day=1) - timedelta(days=1)

    with get_conn() as conn:
        totals = _get_month_totals(conn, last_month_start, last_month_end)
        tops = _get_top_performers(conn, days=35)
        lows = _get_underperformers(conn, days=35)

        # Pull RPR by audience for the month
        rows = conn.execute(
            """SELECT audience,
                      AVG(actual_rpr) as avg_rpr,
                      SUM(actual_revenue) as total_rev,
                      AVG(recipients) as avg_recip,
                      COUNT(*) as sends
               FROM calendar_executions
               WHERE slot_date BETWEEN %s AND %s
                 AND is_preliminary = false
               GROUP BY audience
               ORDER BY avg_rpr DESC""",
            (last_month_start, last_month_end)
        ).fetchall()

        # Build updated RPR table
        rpr_table = {}
        for r in rows:
            rpr_table[r[0]] = {
                "avg_rpr": float(r[1]),
                "total_rev": float(r[2]),
                "avg_recipients": int(r[3]),
                "sends": int(r[4]),
            }

        # Store updated RPR table for next calendar generation
        conn.execute(
            """INSERT INTO strategies (component, strategy_text, approved_by, is_active, created_at)
               VALUES ('learning_loop', %s, 'system', true, NOW())""",
            (json.dumps({
                "type": "monthly_rpr_update",
                "month": last_month_start.strftime("%Y-%m"),
                "rpr_by_audience": rpr_table,
                "total_revenue": totals["total_revenue"],
                "goal": MONTHLY_GOAL,
                "pct_of_goal": totals["total_revenue"] / MONTHLY_GOAL * 100,
            }),)
        )
        conn.commit()

        pct = totals["total_revenue"] / MONTHLY_GOAL * 100
        lines = [
            f"*Monthly Retrospective — {last_month_start.strftime('%B %Y')}*",
            "",
            f"💰 *Total Revenue:* ${totals['total_revenue']:,.0f} / ${MONTHLY_GOAL:,} ({pct:.1f}%)",
            f"📧 *Total Campaigns:* {totals['total_campaigns']}",
            f"📊 *Avg RPR:* ${totals['avg_rpr']:.4f}",
            "",
            "*RPR by audience (updated for next month's calendar):*",
        ]
        for aud, data in sorted(rpr_table.items(), key=lambda x: -x[1]["avg_rpr"]):
            lines.append(f"  {aud}: ${data['avg_rpr']:.4f} RPR × {data['avg_recipients']} recip "
                        f"= ${data['avg_rpr'] * data['avg_recipients']:,.0f}/send ({data['sends']} sends)")

        lines.append("")
        if pct >= 100:
            lines.append("🎉 *GOAL MET.* Maintain strategy, optimize for margin.")
        elif pct >= 80:
            lines.append("🟡 *Close.* Increase top-3 segments by 1 send/week.")
        else:
            lines.append("🔴 *Missed goal.* Calendar needs restructuring around top performers only.")

        lines.append("")
        lines.append("_RPR table saved to strategies DB. Next calendar generation will use these numbers._")

        report = "\n".join(lines)

        post_draft(
            title="📊 Monthly Retro — " + last_month_start.strftime("%B %Y"),
            summary_lines=[report],
            body="",
        )

        return report
