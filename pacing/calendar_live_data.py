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


def _load_strategies_rpr(conn) -> dict[str, float]:
    """Read the most recent monthly RPR update from the strategies table.

    The learning loop writes here every 1st of the month with component='learning_loop'
    and strategy_text containing {"type": "monthly_rpr_update", "rpr_by_audience": {...}}.
    Returns dict[audience -> avg_rpr].  Empty dict if no record exists yet.
    """
    import json as _json
    try:
        row = conn.execute(
            """SELECT strategy_text FROM strategies
               WHERE component = 'learning_loop' AND is_active = true
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()
        if not row:
            return {}
        data = _json.loads(row[0]) if isinstance(row[0], str) else row[0]
        raw = data.get("rpr_by_audience") or {}
        return {aud: float(v["avg_rpr"]) for aud, v in raw.items() if "avg_rpr" in v}
    except Exception as exc:
        print(f"[calendar_live_data] strategies RPR load failed: {exc}")
        return {}


def get_performance_by_segment(conn) -> dict[str, dict]:
    """
    Pull actual RPR and avg revenue per send by audience.

    Priority order:
      1. calendar_executions + performance join (last 90d, finalized rows) — most current
      2. learning_loop strategies table (last monthly retro RPR update)  — one month old
      3. FALLBACK_RPR / FALLBACK_LIST_SIZE hardcoded constants           — May 2026 baseline

    Returns dict: audience_label -> {rpr, avg_revenue, list_size, sends, source}
    """
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    result: dict[str, dict] = {}

    # ── Source 1: calendar_executions joined to performance ──────────────────
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    audience,
                    COUNT(*)                                AS sends,
                    AVG(COALESCE(actual_revenue, 0))        AS avg_revenue,
                    AVG(COALESCE(actual_rpr, 0))            AS avg_rpr,
                    AVG(COALESCE(recipients, 0))            AS avg_recipients
                FROM calendar_executions
                WHERE executed_at >= %s
                  AND status IN ('dispatched','completed')
                  AND is_preliminary = false
                  AND actual_revenue IS NOT NULL
                GROUP BY audience
                HAVING COUNT(*) >= 1
            """, (cutoff,))
            rows = cur.fetchall()

        for audience, sends, avg_rev, avg_rpr, avg_recip in rows:
            key = audience.lower().replace("-", "_").replace(" ", "_")
            if float(avg_rpr or 0) > 0:          # skip zero-revenue rows
                result[key] = {
                    "rpr":         float(avg_rpr),
                    "avg_revenue": float(avg_rev),
                    "list_size":   int(avg_recip or FALLBACK_LIST_SIZE.get(key, 4000)),
                    "sends":       int(sends),
                    "source":      "actual",
                }
        if result:
            print(f"[calendar_live_data] Live data: {len(result)} audiences from executions table")
    except Exception as exc:
        print(f"[calendar_live_data] Executions query failed: {exc}")

    # ── Source 2: strategies table RPR (monthly retro) ───────────────────────
    strategies_rpr = _load_strategies_rpr(conn)
    for aud, rpr in strategies_rpr.items():
        if aud not in result and rpr > 0:
            size = FALLBACK_LIST_SIZE.get(aud, 4000)
            result[aud] = {
                "rpr":         rpr,
                "avg_revenue": round(rpr * size, 0),
                "list_size":   size,
                "sends":       0,
                "source":      "strategies",
            }
    if strategies_rpr:
        strategies_filled = sum(1 for v in result.values() if v["source"] == "strategies")
        if strategies_filled:
            print(f"[calendar_live_data] Strategies table: {strategies_filled} audiences filled")

    # ── Source 3: hardcoded fallbacks for anything still missing ─────────────
    fallback_count = 0
    for label, rpr in FALLBACK_RPR.items():
        if label not in result:
            size = FALLBACK_LIST_SIZE.get(label, 4000)
            result[label] = {
                "rpr":         rpr,
                "avg_revenue": round(rpr * size, 0),
                "list_size":   size,
                "sends":       0,
                "source":      "fallback",
            }
            fallback_count += 1

    if fallback_count:
        total = len(result)
        pct   = int(100 * fallback_count / total)
        print(
            f"[calendar_live_data] WARNING: {fallback_count}/{total} audiences ({pct}%) "
            "using hardcoded May 2026 fallbacks — performance table may be sparse"
        )

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

    actual     = {k: v for k, v in perf.items() if v["source"] == "actual"}
    strategies = {k: v for k, v in perf.items() if v["source"] == "strategies"}
    fallback   = {k: v for k, v in perf.items() if v["source"] == "fallback"}

    # Surface data-quality warning when live data is sparse
    total = len(perf)
    n_live = len(actual) + len(strategies)
    if total > 0 and n_live == 0:
        lines += [
            "",
            "⚠️  DATA QUALITY WARNING: No recent campaign performance data available.",
            "   All numbers below are hardcoded baseline estimates (May 2026).",
            "   Treat revenue_estimate values as rough guides only — do NOT over-optimize.",
        ]
    elif total > 0 and len(fallback) > total // 2:
        lines += [
            "",
            f"⚠️  DATA NOTE: {len(fallback)}/{total} audiences using baseline estimates "
            "(no recent finalized sends). Live data rows marked [ACTUAL] are reliable.",
        ]

    lines += ["", "HISTORICAL PERFORMANCE BY AUDIENCE (last 90 days):"]

    if actual:
        lines.append("  [ACTUAL — 90d calendar_executions, finalized attribution]")
        for aud, d in sorted(actual.items(), key=lambda x: -x[1]["avg_revenue"]):
            lines.append(
                f"  {aud:<22} RPR ${d['rpr']:.3f}  "
                f"list ~{d['list_size']:,}  "
                f"≈ ${d['avg_revenue']:,.0f}/send  "
                f"({d['sends']} sends)"
            )

    if strategies:
        lines.append("  [STRATEGIES — from last monthly retro, ~30 days old]")
        for aud, d in sorted(strategies.items(), key=lambda x: -x[1]["avg_revenue"]):
            lines.append(
                f"  {aud:<22} RPR ${d['rpr']:.3f}  "
                f"list ~{d['list_size']:,}  "
                f"≈ ${d['avg_revenue']:,.0f}/send  (monthly retro)"
            )

    if fallback:
        lines.append("  [ESTIMATED — May 2026 baseline, use conservatively]")
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
