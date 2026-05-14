"""Pacing brain — Phase 2A (pure math, no LLM).

Reads:
  - `goals` (active revenue / engagement targets)
  - `performance` (Shopify order_revenue + Klaviyo conversion_value)

Writes (via cron.py, not here):
  - `pacing_state` row per active goal per daily run

Phase 2A scope is intentionally narrow: compute period-to-date vs linear
target-to-date, and surface the top-5 revenue contributors from Klaviyo.
LLM-driven priority decisions and `priorities`/`decisions`/`strategies`
writes are deferred to Phase 2B.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from db.connection import get_conn

# Phase 2A status thresholds against gap_pct. ±5% is the on-track band.
ON_TRACK_BAND_PCT = Decimal("5")


@dataclass(frozen=True)
class Goal:
    id: str
    title: str
    target_metric: str
    target_value: Decimal
    period_start: date
    period_end: date


@dataclass(frozen=True)
class PacingState:
    goal_id: str
    as_of: datetime
    period_to_date_value: Decimal
    target_to_date_value: Decimal
    gap_pct: Decimal
    days_remaining: int
    required_daily_rate: Decimal
    # Convenience fields (not persisted, but used by the Slack digest).
    status: str  # "ahead" | "on-track" | "behind"
    days_elapsed: int
    total_days: int


@dataclass(frozen=True)
class Contributor:
    kind: str  # "campaign" | "flow"
    entity_id: str
    entity_name: str
    send_channel: str | None
    conversion_value: Decimal


def _classify(gap_pct: Decimal) -> str:
    if gap_pct > ON_TRACK_BAND_PCT:
        return "ahead"
    if gap_pct < -ON_TRACK_BAND_PCT:
        return "behind"
    return "on-track"


def _fetch_goal(conn, goal_id: str) -> Goal:
    with conn.cursor() as cur:
        cur.execute(
            """
            select id, title, target_metric, target_value, period_start, period_end
              from goals
             where id = %s
            """,
            (goal_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"goal not found: {goal_id}")
    return Goal(
        id=str(row[0]),
        title=row[1],
        target_metric=row[2],
        target_value=Decimal(row[3]),
        period_start=row[4],
        period_end=row[5],
    )


def active_goals() -> list[Goal]:
    """All goals with status='active'."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, title, target_metric, target_value, period_start, period_end
                  from goals
                 where status = 'active'
                 order by period_start
                """
            )
            rows = cur.fetchall()
    return [
        Goal(
            id=str(r[0]),
            title=r[1],
            target_metric=r[2],
            target_value=Decimal(r[3]),
            period_start=r[4],
            period_end=r[5],
        )
        for r in rows
    ]


def _period_to_date_revenue(conn, period_start: date, as_of: datetime) -> Decimal:
    """Sum of order_revenue from Shopify performance rows, deduped to the latest
    row per dimensions->>'order_id'.

    Filter is on the order's actual creation timestamp (`dimensions->>'created_at'`)
    so a goal period reflects orders *placed* in that window, not orders *ingested*
    in it. The `::timestamptz` cast also filters out legacy rows that pre-date the
    dimension being populated, which is the intended behavior.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            with latest_per_order as (
              select distinct on (dimensions->>'order_id')
                     metric_value
                from performance
               where source = 'shopify'
                 and metric_name = 'order_revenue'
                 and (dimensions->>'created_at')::timestamptz >= %s
                 and (dimensions->>'created_at')::timestamptz <= %s
            order by dimensions->>'order_id', measured_at desc
            )
            select coalesce(sum(metric_value), 0) from latest_per_order
            """,
            (period_start, as_of),
        )
        (total,) = cur.fetchone()
    return Decimal(total)


def compute_pacing_state(goal_id: str, as_of: datetime | None = None) -> PacingState:
    """Compute pacing for a single goal at a specific instant.

    Pure math wrapper — does NOT write to pacing_state. cron.run_daily_pacing
    handles persistence.
    """
    if as_of is None:
        as_of = datetime.now(timezone.utc).replace(microsecond=0)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)

    with get_conn() as conn:
        goal = _fetch_goal(conn, goal_id)
        ptd = _period_to_date_revenue(conn, goal.period_start, as_of)

    total_days = (goal.period_end - goal.period_start).days + 1  # inclusive
    raw_elapsed = (as_of.date() - goal.period_start).days + 1
    days_elapsed = max(0, min(raw_elapsed, total_days))

    target_to_date = goal.target_value * Decimal(days_elapsed) / Decimal(total_days)

    if target_to_date > 0:
        gap_pct = ((ptd - target_to_date) / target_to_date) * Decimal(100)
    else:
        # Pre-period: no expectation yet, any progress is "on-track".
        gap_pct = Decimal(0)

    days_remaining = max(0, (goal.period_end - as_of.date()).days)
    remaining_target = max(Decimal(0), goal.target_value - ptd)
    required_daily_rate = remaining_target / Decimal(max(days_remaining, 1))

    return PacingState(
        goal_id=goal.id,
        as_of=as_of,
        period_to_date_value=ptd.quantize(Decimal("0.01")),
        target_to_date_value=target_to_date.quantize(Decimal("0.01")),
        gap_pct=gap_pct.quantize(Decimal("0.01")),
        days_remaining=days_remaining,
        required_daily_rate=required_daily_rate.quantize(Decimal("0.01")),
        status=_classify(gap_pct),
        days_elapsed=days_elapsed,
        total_days=total_days,
    )


def _top_campaign_contributors(conn, days: int) -> list[Contributor]:
    """Top-5 campaigns by conversion_value over the last `days` days.

    Dedupe: latest row per (entity_id, campaign_message_id, send_channel) by
    measured_at, then sum to roll up A/B variants. Output rows are
    (campaign, send_channel) tuples so email + sms appear as separate lines.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            with latest as (
              select distinct on (
                       dimensions->>'entity_id',
                       dimensions->>'campaign_message_id',
                       dimensions->>'send_channel'
                     )
                     dimensions->>'entity_id'    as entity_id,
                     dimensions->>'entity_name'  as entity_name,
                     dimensions->>'send_channel' as send_channel,
                     metric_value
                from performance
               where source = 'klaviyo'
                 and metric_name = 'conversion_value'
                 and dimensions->>'kind' = 'campaign'
                 and measured_at >= now() - make_interval(days => %s)
            order by dimensions->>'entity_id',
                     dimensions->>'campaign_message_id',
                     dimensions->>'send_channel',
                     measured_at desc
            )
            select entity_id, entity_name, send_channel, sum(metric_value)
              from latest
             group by entity_id, entity_name, send_channel
             order by sum(metric_value) desc
             limit 5
            """,
            (days,),
        )
        rows = cur.fetchall()
    return [
        Contributor(
            kind="campaign",
            entity_id=r[0],
            entity_name=r[1] or "",
            send_channel=r[2],
            conversion_value=Decimal(r[3] or 0).quantize(Decimal("0.01")),
        )
        for r in rows
    ]


def _top_flow_contributors(conn, days: int) -> list[Contributor]:
    """Top-5 flows by conversion_value over the last `days` days.

    Dedupe: latest row per (entity_id, flow_message_id, send_channel), then sum
    across messages + channels per flow. Output is one row per flow.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            with latest as (
              select distinct on (
                       dimensions->>'entity_id',
                       dimensions->>'flow_message_id',
                       dimensions->>'send_channel'
                     )
                     dimensions->>'entity_id'    as entity_id,
                     dimensions->>'entity_name'  as entity_name,
                     metric_value
                from performance
               where source = 'klaviyo'
                 and metric_name = 'conversion_value'
                 and dimensions->>'kind' = 'flow'
                 and measured_at >= now() - make_interval(days => %s)
            order by dimensions->>'entity_id',
                     dimensions->>'flow_message_id',
                     dimensions->>'send_channel',
                     measured_at desc
            )
            select entity_id, entity_name, sum(metric_value)
              from latest
             group by entity_id, entity_name
             order by sum(metric_value) desc
             limit 5
            """,
            (days,),
        )
        rows = cur.fetchall()
    return [
        Contributor(
            kind="flow",
            entity_id=r[0],
            entity_name=r[1] or "",
            send_channel=None,  # rolled up across channels
            conversion_value=Decimal(r[2] or 0).quantize(Decimal("0.01")),
        )
        for r in rows
    ]


def top_contributors(days: int = 7) -> dict[str, list[Contributor]]:
    """Top-5 campaigns + top-5 flows by Klaviyo conversion_value in the last `days` days."""
    with get_conn() as conn:
        campaigns = _top_campaign_contributors(conn, days)
        flows = _top_flow_contributors(conn, days)
    return {"campaigns": campaigns, "flows": flows}


def compute_daily_priorities(as_of: datetime | None = None) -> dict:
    """Phase 2B priority brain — decide today's operating mode from pacing state.

    Returns a dict with mode, reasoning, recommended_actions, and writes rows to
    both `decisions` (decision_type='daily_priority') and `priorities` tables.

    Modes:
      boost    — behind >20%: add unscheduled high-RPR slot
      push     — behind 5-20%: prioritize high-RPR segments first
      maintain — on-track ±5%: run as planned
      ease     — ahead >5%: can skip lowest-RPR slots if at cadence limit
    """
    import json
    if as_of is None:
        as_of = datetime.now(timezone.utc).replace(microsecond=0)

    goals = active_goals()
    if not goals:
        return {"mode": "maintain", "reasoning": "No active goals", "recommended_actions": []}

    # Use first active goal (the $150K/month revenue goal)
    goal = goals[0]
    state = compute_pacing_state(goal.id, as_of=as_of)
    gap_pct = float(state.gap_pct)

    today = as_of.date()
    today_iso = today.isoformat()

    # Read today's calendar slots from decisions table
    today_slots = []
    with get_conn() as conn:
        row = conn.execute(
            "SELECT output FROM decisions WHERE decision_type='calendar_plan' AND output->>'month'=%s ORDER BY created_at DESC LIMIT 1",
            (today.strftime("%Y-%m"),)
        ).fetchone()
        if row:
            payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            today_slots = [s for s in payload.get("slots", []) if s.get("date") == today_iso]

    # Determine mode
    if gap_pct < -20:
        mode = "boost"
        reasoning = f"Revenue is {abs(gap_pct):.1f}% behind target. Aggressive recovery needed."
        recommended_actions = [
            "Add unscheduled high-RPR slot (lapsed_30d or VIP) if not sent in 7+ days",
            "Prioritize sleep audio + email over SEO content today",
            "Flag in morning brief as BOOST MODE",
        ]
    elif gap_pct < -5:
        mode = "push"
        reasoning = f"Revenue is {abs(gap_pct):.1f}% behind target. Stay on schedule, prioritize high-RPR."
        recommended_actions = [
            "Run all planned slots — do not skip any",
            "Prioritize highest-RPR audience if multiple slots today",
        ]
    elif gap_pct > 5:
        mode = "ease"
        reasoning = f"Revenue is {gap_pct:.1f}% ahead of target. System is performing well."
        recommended_actions = [
            "Run planned slots; can skip lowest-priority slot if already at 3/day cadence",
            "Focus on content quality over volume",
        ]
    else:
        mode = "maintain"
        reasoning = f"Revenue is within ±5% of target ({gap_pct:+.1f}%). Proceed as planned."
        recommended_actions = ["Execute calendar as planned"]

    output = {
        "mode": mode,
        "date": today_iso,
        "gap_pct": gap_pct,
        "reasoning": reasoning,
        "recommended_actions": recommended_actions,
        "today_slots": len(today_slots),
        "status": state.status,
        "period_to_date": float(state.period_to_date_value),
        "required_daily_rate": float(state.required_daily_rate),
    }

    pacing_snapshot = {
        "gap_pct": gap_pct,
        "status": state.status,
        "period_to_date_value": float(state.period_to_date_value),
        "required_daily_rate": float(state.required_daily_rate),
        "days_remaining": state.days_remaining,
    }

    # Write to decisions + priorities tables
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO decisions (decided_by, decision_type, input_context, reasoning, output) "
                "VALUES (%s, %s, %s, %s, %s)",
                ("pacing_brain", "daily_priority", json.dumps({"goal_id": goal.id, "as_of": as_of.isoformat()}),
                 reasoning, json.dumps(output))
            )
            conn.execute(
                "INSERT INTO priorities (decided_at, effective_for, prioritized_workers, reasoning, pacing_snapshot) "
                "VALUES (NOW(), %s, %s, %s, %s)",
                (today, json.dumps([mode]), reasoning, json.dumps(pacing_snapshot))
            )
            conn.commit()
    except Exception as exc:
        print(f"[brain] priorities write error: {exc}")

    return output


def content_strategy_attribution(days: int = 90) -> dict:
    """Classify calendar_executions by topic theme and compute RPR per pillar.

    Pillars: sleep_science | product_offer | story_narrative | other
    Returns dict with avg_rpr, total_revenue, sends per pillar.
    """
    SLEEP_KEYWORDS = {"sleep", "science", "research", "study", "brain", "rem", "cortisol", "melatonin", "circadian"}
    OFFER_KEYWORDS = {"% off", "discount", "bundle", "deal", "save", "code", "limited", "sale", "bogo"}
    STORY_KEYWORDS = {"years ago", "discovered", "story", "one night", "her name", "he noticed", "she realized"}

    def classify(topic: str) -> str:
        t = (topic or "").lower()
        if any(k in t for k in STORY_KEYWORDS):
            return "story_narrative"
        if any(k in t for k in OFFER_KEYWORDS):
            return "product_offer"
        if any(k in t for k in SLEEP_KEYWORDS):
            return "sleep_science"
        return "other"

    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT topic_angle, actual_rpr, actual_revenue FROM calendar_executions "
                "WHERE is_preliminary = false AND actual_rpr > 0 "
                f"AND slot_date > CURRENT_DATE - INTERVAL '{int(days)} days'"
            ).fetchall()
    except Exception:
        return {}

    pillars: dict[str, dict] = {}
    for topic, rpr, revenue in rows:
        pillar = classify(topic or "")
        if pillar not in pillars:
            pillars[pillar] = {"sends": 0, "total_rpr": 0.0, "total_revenue": 0.0}
        pillars[pillar]["sends"] += 1
        pillars[pillar]["total_rpr"] += float(rpr or 0)
        pillars[pillar]["total_revenue"] += float(revenue or 0)

    result = {}
    for pillar, data in pillars.items():
        sends = data["sends"]
        result[pillar] = {
            "sends": sends,
            "avg_rpr": round(data["total_rpr"] / sends, 4) if sends else 0,
            "total_revenue": round(data["total_revenue"], 2),
        }
    return result
