"""
Live data layer for calendar generation.
Pulled before Opus runs so estimates are based on real Klaviyo performance,
not guesses.
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Any


SEGMENT_LABELS = {
    "UEQD6k": "lapsed_30d",
    "UfARWm": "lapsed_60d",
    "XuS7rY": "lapsed_90d",
    "W98qh3": "lapsed_180d",
    "RArtzN": "vip",
    "RvtHdn": "engaged_customers",
    "UBFUcH": "active_seal",
    "VAUD58": "whales",
    "Xrp3ha": "engaged_prospects",
    "Sme9Nq": "super_engaged",
    "Y6VSre": "hive_mind_prospects",
}

# Conservative fallbacks when no real data exists
# RPR and list sizes derived from actual Klaviyo campaign data (May 2026, 90-day window)
# Median across all sends per segment — not peaks, not guesses
FALLBACK_RPR = {
    "lapsed_30d":        0.267,   # 14 sends, median RPR
    "lapsed_60d":        0.081,   # 1 send
    "lapsed_90d":        0.093,   # 16 sends
    "lapsed_180d":       0.046,   # 9 sends
    "vip":               0.161,   # 26 sends
    "whales":            0.658,   # 6 sends
    "engaged_customers": 0.101,   # 5 sends
    "active_seal":       1.268,   # 8 sends — highest RPR, small list
    "engaged_prospects": 0.064,   # 31 sends
    "super_engaged":     0.150,   # estimated — subset of engaged_prospects
    "hive_mind_prospects": 0.030, # estimated — cold-ish prospect list
    "one_time_buyers":   0.056,   # 15 sends
    "sniper_followup":   0.120,   # 5 sends
    "default":           0.090,
}

# Median recipient count per segment across actual sends
FALLBACK_LIST_SIZE = {
    "lapsed_30d":        3618,
    "lapsed_60d":        7115,
    "lapsed_90d":        1993,
    "lapsed_180d":       16192,
    "vip":               5424,
    "whales":            1038,
    "engaged_customers": 13340,
    "active_seal":       511,
    "engaged_prospects": 12002,
    "super_engaged":     2000,
    "hive_mind_prospects": 3000,
    "one_time_buyers":   12951,
    "sniper_followup":   4447,
    "default":           5000,
}


def get_performance_by_segment(conn) -> dict[str, dict]:
    """
    Pull actual RPR and avg revenue per send by audience from performance table.
    Returns dict: audience_label -> {rpr, avg_revenue, list_size, sends, source}
    """
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    result: dict[str, dict] = {}

    try:
        with conn.cursor() as cur:
            # Try to get campaign-level performance grouped by audience
            cur.execute("""
                SELECT
                    audience,
                    COUNT(*)                          AS sends,
                    AVG(revenue)                      AS avg_revenue,
                    AVG(revenue / NULLIF(recipients,0)) AS avg_rpr,
                    AVG(recipients)                   AS avg_recipients
                FROM (
                    SELECT
                        ce.audience,
                        COALESCE(p.value, 0)          AS revenue,
                        COALESCE(ce.recipients, 0)    AS recipients
                    FROM calendar_executions ce
                    LEFT JOIN performance p
                      ON p.source_id = ce.klaviyo_campaign_id
                     AND p.metric_name = 'revenue'
                     AND p.is_preliminary = false
                    WHERE ce.executed_at >= %s
                      AND ce.status = 'completed'
                ) sub
                GROUP BY audience
                HAVING COUNT(*) >= 1
            """, (cutoff,))
            rows = cur.fetchall()

        for row in rows:
            audience, sends, avg_rev, avg_rpr, avg_recip = row
            audience_key = audience.lower().replace("-","_").replace(" ","_")
            result[audience_key] = {
                "rpr":        float(avg_rpr or 0),
                "avg_revenue": float(avg_rev or 0),
                "list_size":  int(avg_recip or FALLBACK_LIST_SIZE.get(audience_key, 4000)),
                "sends":      int(sends),
                "source":     "actual",
            }
    except Exception as e:
        print("[calendar_live_data] Performance query failed: " + str(e))

    # Fill gaps with fallbacks
    for label, rpr in FALLBACK_RPR.items():
        if label not in result:
            size = FALLBACK_LIST_SIZE.get(label, 4000)
            result[label] = {
                "rpr":        rpr,
                "avg_revenue": round(rpr * size, 0),
                "list_size":  size,
                "sends":      0,
                "source":     "fallback",
            }

    return result


def get_pacing_context(conn) -> dict[str, Any]:
    """Pull current pacing state for calendar generation."""
    try:
        from pacing.brain import active_goals, compute_pacing_state
        goals = active_goals()
        if not goals:
            return {}
        g     = goals[0]
        state = compute_pacing_state(g.id)
        return {
            "goal_title":         g.title,
            "target_total":       float(g.target_value or 0),
            "period_to_date":     float(state.period_to_date_value or 0),
            "target_to_date":     float(state.target_to_date_value or 0),
            "gap":                float(state.period_to_date_value - state.target_to_date_value),
            "required_daily":     float(state.required_daily_rate or 0),
            "days_remaining":     int(state.days_remaining or 0),
            "projected_revenue":  float(state.projected_value or 0),
            "status":             "behind" if state.period_to_date_value < state.target_to_date_value else "ahead",
        }
    except Exception as e:
        print("[calendar_live_data] Pacing query failed: " + str(e))
        return {}


def build_performance_context_text(perf: dict, pacing: dict) -> str:
    """Format live data as text block for Opus prompt."""
    lines = ["=== LIVE PERFORMANCE DATA (use these — do not invent numbers) ==="]

    if pacing:
        gap_str = ("BEHIND $" + f"{abs(pacing['gap']):,.0f}" if pacing["gap"] < 0
                   else "AHEAD $" + f"{pacing['gap']:,.0f}")
        lines += [
            "",
            "PACING STATE:",
            f"  Month target:     ${pacing['target_total']:,.0f}",
            f"  Revenue to date:  ${pacing['period_to_date']:,.0f}",
            f"  Status:           {gap_str}",
            f"  Required daily:   ${pacing['required_daily']:,.0f}/day",
            f"  Days remaining:   {pacing['days_remaining']}",
            f"  Projected total:  ${pacing['projected_revenue']:,.0f} (at current pace)",
            "",
            "⚠️  The calendar MUST close the gap. Weight higher-revenue segments",
            f"   heavily. You need ${pacing['required_daily']:,.0f}/day average.",
        ]

    lines += ["", "HISTORICAL PERFORMANCE BY AUDIENCE (last 90 days):"]
    actual   = {k: v for k, v in perf.items() if v["source"] == "actual"}
    fallback = {k: v for k, v in perf.items() if v["source"] == "fallback"}

    if actual:
        lines.append("  [ACTUAL data — use these numbers directly]")
        for aud, d in sorted(actual.items(), key=lambda x: -x[1]["avg_revenue"]):
            lines.append(
                f"  {aud:<22} RPR ${d['rpr']:.3f}  "
                f"list ~{d['list_size']:,}  "
                f"≈ ${d['avg_revenue']:,.0f}/send  "
                f"({d['sends']} sends)"
            )

    if fallback:
        lines.append("  [ESTIMATED (no recent data) — use conservatively]")
        for aud, d in sorted(fallback.items(), key=lambda x: -x[1]["avg_revenue"]):
            if d["avg_revenue"] > 0:
                lines.append(
                    f"  {aud:<22} RPR ${d['rpr']:.3f}  "
                    f"list ~{d['list_size']:,}  "
                    f"≈ ${d['avg_revenue']:,.0f}/send  (estimated)"
                )

    lines += [
        "",
        "Set revenue_estimate for each slot using the matching audience row above.",
        "SEO blog slots always get revenue_estimate = 0.",
        "=== END PERFORMANCE DATA ===",
    ]
    return "\n".join(lines)
