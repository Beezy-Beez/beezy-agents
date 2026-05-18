"""
Calendar campaign builder — builds Klaviyo drafts for approved upcoming slots.

Flow
────
1. seed_approved_slots(conn)
   Scans the active calendar plan for klaviyo_campaign slots within the next
   48 hours whose week is approved in calendar_approvals.  Pre-inserts each
   qualifying slot into calendar_executions with status='approved'.
   Idempotent — skips rows that already have any non-failed execution.

2. build_pending(conn)
   Queries calendar_executions for status='approved' rows within 48h.
   For each: reconstructs the full slot dict from decisions.output['slots'],
   adds hierarchy metadata, runs beezy_campaign.run(), and updates the row
   to status='dispatched' or status='failed'.
   Posts a per-slot Slack notification on completion.

3. run(dry_run=False)
   seed + build in sequence.  Main entry point for the cron loop.
   With dry_run=True (or BEEZY_DRY_RUN=1 env var): runs copy generation
   and validator for real, but skips all Klaviyo / Shopify / Slack side effects.

Hierarchy tiers (for sorting / labelling only, never blocks a send)
────────────────────────────────────────────────────────────────────
  Tier 1 — HIGH_FREQ: active_seal, vip, lapsed_30d, whales, high_aov
  Tier 2 — MODERATE:  one_time_buyers, otb, lapsed_60d, lapsed_60_90d,
                       engaged_customers, inner_circle
  Tier 3 — LOW:       lapsed_90d, lapsed_90_180d, lapsed_180d, lapsed_180d_plus,
                       engaged_prospects, super_engaged
"""
from __future__ import annotations

import json
import os
import traceback
from datetime import date, datetime, timedelta, timezone
from typing import Any

import psycopg

from config import DATABASE_URL
from db.connection import get_conn
from lib.dryrun import is_dry_run, dry_banner
from lib.slack import post_draft, notify_failure

# ── Hierarchy tiers ───────────────────────────────────────────────────────────

HIERARCHY: dict[str, int] = {}
for _a in ("active_seal", "vip", "lapsed_30d", "whales", "high_aov",
           "active_subscribers", "inner_circle"):
    HIERARCHY[_a] = 1
for _a in ("one_time_buyers", "otb", "lapsed_60d", "lapsed_60_90d",
           "engaged_customers", "all_customers", "cart_abandoners"):
    HIERARCHY[_a] = 2
for _a in ("lapsed_90d", "lapsed_90_180d", "lapsed_180d", "lapsed_180d_plus",
           "winback_180d", "engaged_prospects", "super_engaged"):
    HIERARCHY[_a] = 3

HIERARCHY_LABEL = {1: "HIGH_FREQ · Tier 1", 2: "MODERATE · Tier 2", 3: "LOW · Tier 3"}

# Only these content_types are handled by this builder.
# hive_mind, sleep_audio, seo_blog, flow_experiment have their own pipelines.
HANDLED_TYPES = {"klaviyo_campaign"}

# ── Calendar helpers ──────────────────────────────────────────────────────────

def _week_is_approved(conn, slot_date: date) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM calendar_approvals "
            "WHERE week_start <= %s AND %s < week_start + INTERVAL '7 days' "
            "AND approved_at IS NOT NULL LIMIT 1",
            (slot_date, slot_date),
        )
        return cur.fetchone() is not None


def _already_has_execution(conn, slot_date: date, content_type: str, audience: str) -> bool:
    """True if a non-failed, non-skipped execution row already exists."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM calendar_executions "
            "WHERE slot_date = %s AND content_type = %s AND audience = %s "
            "AND status NOT IN ('failed', 'skipped') LIMIT 1",
            (slot_date, content_type, audience),
        )
        return cur.fetchone() is not None


def _latest_calendar(conn) -> tuple[str | None, list[dict]]:
    month = date.today().strftime("%Y-%m")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, output FROM decisions "
            "WHERE decision_type = 'calendar_plan' AND output->>'month' = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (month,),
        )
        row = cur.fetchone()
    if not row:
        return None, []
    decision_id = str(row[0])
    payload = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    return decision_id, payload.get("slots", [])


def _lookup_full_slot(
    conn,
    slot_date: date,
    audience: str,
    content_type: str,
    decision_id: str | None,
) -> dict | None:
    """Find the matching slot in the decisions JSON."""
    month = slot_date.strftime("%Y-%m")
    query = (
        "SELECT output->'slots' FROM decisions "
        "WHERE decision_type = 'calendar_plan' AND output->>'month' = %s "
        "ORDER BY created_at DESC LIMIT 1"
    )
    with conn.cursor() as cur:
        cur.execute(query, (month,))
        row = cur.fetchone()
    if not row:
        return None
    slots = row[0] if isinstance(row[0], list) else json.loads(row[0])
    for s in slots:
        if (
            s.get("date") == slot_date.isoformat()
            and s.get("content_type") == content_type
            and s.get("audience") == audience
        ):
            return s
    return None


# ── Seed ─────────────────────────────────────────────────────────────────────

def seed_approved_slots(
    conn,
    horizon_hours: int = 48,
    dry_run: bool = False,
) -> list[dict]:
    """
    Insert status='approved' rows into calendar_executions for every
    klaviyo_campaign slot that:
      - falls within [today, today + horizon_hours)
      - belongs to an approved week
      - has no existing non-failed execution row

    Returns the list of slot dicts that were (or would be) seeded.
    """
    today = date.today()
    cutoff = today + timedelta(hours=horizon_hours)

    decision_id, all_slots = _latest_calendar(conn)
    if not decision_id:
        print("[builder/seed] No calendar plan for this month — nothing to seed.")
        return []

    qualifying: list[dict] = []
    for slot in all_slots:
        slot_date_str = slot.get("date", "")
        if not slot_date_str:
            continue
        try:
            slot_date = date.fromisoformat(slot_date_str)
        except ValueError:
            continue

        if not (today <= slot_date <= cutoff):
            continue
        if slot.get("content_type") not in HANDLED_TYPES:
            continue
        if not _week_is_approved(conn, slot_date):
            continue
        audience = slot.get("audience", "")
        content_type = slot.get("content_type", "")
        if _already_has_execution(conn, slot_date, content_type, audience):
            print(f"[builder/seed] {slot_date} {content_type}/{audience} already executed — skip")
            continue

        qualifying.append(slot)

    if not qualifying:
        print("[builder/seed] No qualifying slots to seed.")
        return []

    if dry_run or is_dry_run():
        print(f"[builder/seed] DRY RUN — would seed {len(qualifying)} slot(s):")
        for s in qualifying:
            tier = HIERARCHY.get(s.get("audience", ""), 0)
            print(f"  {s['date']} | {s['content_type']:20} | {s['audience']:20} | tier={tier} | {s.get('topic_angle','')[:60]}")
        return qualifying

    for slot in qualifying:
        slot_date = date.fromisoformat(slot["date"])
        tier = HIERARCHY.get(slot.get("audience", ""), 0)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO calendar_executions "
                "(decision_id, slot_date, content_type, audience, topic_angle, status, notes) "
                "VALUES (%s, %s, %s, %s, %s, 'approved', %s) "
                "ON CONFLICT DO NOTHING",
                (
                    decision_id,
                    slot_date,
                    slot.get("content_type"),
                    slot.get("audience", ""),
                    slot.get("topic_angle", ""),
                    json.dumps({"send_time_est": slot.get("send_time_est", ""),
                                "discount_code": slot.get("discount_code", ""),
                                "discount_pct":  slot.get("discount_pct"),
                                "revenue_estimate": slot.get("revenue_estimate", 0),
                                "needs_page":    slot.get("needs_page", False),
                                "priority":      slot.get("priority", ""),
                                "hierarchy_tier": tier}),
                ),
            )
        conn.commit()
        print(f"[builder/seed] Seeded: {slot['date']} | {slot['content_type']}/{slot['audience']} | tier={tier}")

    print(f"[builder/seed] Done — {len(qualifying)} slot(s) seeded.")
    return qualifying


# ── Build pending ─────────────────────────────────────────────────────────────

def _enrich_slot(raw_slot: dict) -> dict:
    """Add hierarchy_tier and ensure all required fields have defaults."""
    slot = dict(raw_slot)
    audience = slot.get("audience", "")
    slot.setdefault("priority", "high")
    slot.setdefault("needs_page", False)
    slot.setdefault("revenue_estimate", 0)
    slot.setdefault("discount_code", "")
    slot.setdefault("discount_pct", None)
    slot.setdefault("send_time_est", "14:00")
    slot["hierarchy_tier"] = HIERARCHY.get(audience, 0)
    slot["hierarchy_label"] = HIERARCHY_LABEL.get(slot["hierarchy_tier"], "UNCLASSIFIED")
    return slot


def _slot_from_ce_row(row: tuple) -> dict:
    """
    Build a minimal slot dict from a calendar_executions row when the full
    slot can't be found in the decisions table.  Uses the notes JSON that
    seed_approved_slots() stores.
    """
    (
        row_id, decision_id, slot_date, content_type,
        audience, topic_angle, notes,
    ) = row[:7]

    extras: dict = {}
    if notes and notes.startswith("{"):
        try:
            extras = json.loads(notes)
        except Exception:
            pass

    slot: dict[str, Any] = {
        "date":             slot_date.isoformat(),
        "content_type":     content_type or "klaviyo_campaign",
        "audience":         audience or "",
        "topic_angle":      topic_angle or "",
        "send_time_est":    extras.get("send_time_est", "14:00"),
        "discount_code":    extras.get("discount_code", ""),
        "discount_pct":     extras.get("discount_pct"),
        "revenue_estimate": extras.get("revenue_estimate", 0),
        "needs_page":       extras.get("needs_page", False),
        "priority":         extras.get("priority", "high"),
    }
    if extras.get("excluded_segment_ids"):
        slot["excluded_segment_ids"] = extras["excluded_segment_ids"]
    return slot


def _post_slot_result(
    slot: dict,
    result: Any,
    dry_run: bool,
) -> None:
    """Post a per-slot Slack notification."""
    prefix = dry_banner() if dry_run else ""
    audience = slot.get("audience", "?")
    tier = slot.get("hierarchy_tier", 0)
    tier_label = slot.get("hierarchy_label", "")
    send_time = slot.get("send_time_est", "?")
    rev = int(slot.get("revenue_estimate", 0) or 0)

    if isinstance(result, str) and result.startswith("blocked:"):
        emoji = "🔴"
        status_line = f"*Validator blocked* — {result.split(':',1)[1]}"
        camp_url = ""
    elif isinstance(result, dict):
        emoji = "🧪" if dry_run else "✅"
        camp_url = result.get("campaign_url", "")
        status_line = f"*Draft ready* — <{camp_url}|Open in Klaviyo>" if camp_url else "*Draft ready* (dry run)"
    else:
        emoji = "⚠️"
        status_line = f"*Unexpected result* — {str(result)[:120]}"
        camp_url = ""

    lines = [
        f"{prefix}{emoji} *{slot['date']} · {audience}* — Tier {tier} ({tier_label})",
        f"Topic:    {slot.get('topic_angle','')[:80]}",
        f"Send:     {send_time} ET  ·  est ${rev}/send",
        status_line,
    ]
    disc = slot.get("discount_code") or ""
    if disc:
        lines.append(f"Discount: {disc}  ({slot.get('discount_pct', '?')}% off)")

    post_draft(
        title=f"{'[DRY RUN] ' if dry_run else ''}Campaign Built — {slot['date']} {audience}",
        summary_lines=lines,
        body=camp_url,
    )


def build_pending(conn, dry_run: bool = False, horizon_hours: int = 48) -> list[dict]:
    """
    Process all status='approved' rows within the horizon window.

    Returns a list of result dicts:
      {slot, result, status}   where status ∈ dispatched|failed|blocked
    """
    today = date.today()
    cutoff = today + timedelta(hours=horizon_hours)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, decision_id, slot_date, content_type, audience, "
            "       topic_angle, notes "
            "FROM calendar_executions "
            "WHERE status = 'approved' "
            "  AND slot_date BETWEEN %s AND %s "
            "ORDER BY slot_date ASC, "
            # sort by hierarchy tier ascending (tier 1 = most important runs first)
            "         CASE audience "
            "           WHEN 'active_seal'  THEN 1 WHEN 'vip'        THEN 1 "
            "           WHEN 'lapsed_30d'   THEN 1 WHEN 'whales'     THEN 1 "
            "           WHEN 'high_aov'     THEN 1 "
            "           WHEN 'one_time_buyers' THEN 2 WHEN 'otb'     THEN 2 "
            "           WHEN 'lapsed_60d'   THEN 2 WHEN 'lapsed_60_90d' THEN 2 "
            "           WHEN 'engaged_customers' THEN 2 "
            "           ELSE 3 END ASC",
            (today, cutoff),
        )
        rows = cur.fetchall()

    if not rows:
        print("[builder] No approved slots to build.")
        return []

    print(f"[builder] {len(rows)} approved slot(s) to build.")

    # Scope BEEZY_DRY_RUN for the entire build pass so all side effects
    # (including _post_slot_result) honour the dry-run flag.
    _set_dry_env = dry_run and not is_dry_run()
    if _set_dry_env:
        os.environ["BEEZY_DRY_RUN"] = "1"

    results: list[dict] = []
    for row in rows:
        row_id = row[0]
        slot_date = row[2]
        content_type = row[3]
        audience = row[4]
        label = f"{slot_date} {content_type}/{audience}"

        # Reconstruct full slot from decisions table; fall back to row data
        full_slot = _lookup_full_slot(conn, slot_date, audience, content_type, str(row[1]) if row[1] else None)
        if full_slot:
            slot = _enrich_slot(full_slot)
            print(f"[builder] Slot resolved from calendar: {label}")
        else:
            slot = _enrich_slot(_slot_from_ce_row(row))
            print(f"[builder] Slot reconstructed from CE row (not in calendar): {label}")

        # Mark building in DB (guard against concurrent double-run)
        if not (dry_run or is_dry_run()):
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE calendar_executions SET status='building', notes=notes "
                    "WHERE id = %s AND status = 'approved'",
                    (row_id,),
                )
            conn.commit()
            if cur.rowcount == 0:
                print(f"[builder] {label} already claimed by another worker — skip.")
                continue

        print(f"[builder] Running pipeline for {label}...")

        try:
            from workers.beezy_campaign import run as campaign_run
            result = campaign_run(slot)
        except Exception as exc:
            tb = traceback.format_exc()
            print(f"[builder] EXCEPTION for {label}:\n{tb}")
            result = f"exception:{str(exc)[:120]}"

        # Determine final status
        if isinstance(result, str) and result.startswith("blocked:"):
            final_status = "blocked"
            klaviyo_id = None
        elif isinstance(result, str):
            final_status = "failed"
            klaviyo_id = None
        else:
            final_status = "dispatched"
            klaviyo_id = result.get("campaign_id") if isinstance(result, dict) else None

        # Update DB
        if not (dry_run or is_dry_run()):
            notes_val = str(result)[:500] if isinstance(result, str) else json.dumps(result)[:500]
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE calendar_executions "
                    "SET status = %s, notes = %s, executed_at = NOW(), "
                    "    klaviyo_campaign_id = %s, is_preliminary = %s "
                    "WHERE id = %s",
                    (
                        final_status,
                        notes_val,
                        klaviyo_id,
                        True if klaviyo_id else None,
                        row_id,
                    ),
                )
            conn.commit()
        else:
            print(f"[builder/DRY RUN] would mark {label} → {final_status}")

        # Slack notification
        try:
            _post_slot_result(slot, result, dry_run=dry_run)
        except Exception as exc:
            print(f"[builder] Slack notify failed (non-fatal): {exc}")

        results.append({"slot": slot, "result": result, "status": final_status})
        print(f"[builder] {label} → {final_status}")

    if _set_dry_env:
        os.environ.pop("BEEZY_DRY_RUN", None)

    return results


# ── Main entry point ──────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> str:
    """
    Seed approved slots, then execute pending ones.
    Returns a short summary string for logging.
    """
    print(f"[builder] Starting calendar_campaign_builder (dry_run={dry_run})")
    with get_conn() as conn:
        seeded = seed_approved_slots(conn, dry_run=dry_run)
        results = build_pending(conn, dry_run=dry_run)

    n_dispatched = sum(1 for r in results if r["status"] == "dispatched")
    n_blocked = sum(1 for r in results if r["status"] == "blocked")
    n_failed = sum(1 for r in results if r["status"] == "failed")

    summary = (
        f"seeded={len(seeded)} dispatched={n_dispatched} "
        f"blocked={n_blocked} failed={n_failed}"
    )
    print(f"[builder] Done — {summary}")
    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Calendar campaign builder")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run copy gen + validator without Klaviyo/Shopify side effects")
    parser.add_argument("--seed-only", action="store_true",
                        help="Only seed approved rows, don't execute them")
    parser.add_argument("--build-only", action="store_true",
                        help="Only execute already-seeded approved rows")
    parser.add_argument("--inject-test-slot", action="store_true",
                        help="Insert a test 'approved' row for May 19 lapsed_60_90d and exit")
    parser.add_argument("--horizon-hours", type=int, default=48,
                        help="Look-ahead window in hours for approved slots (default 48)")
    args = parser.parse_args()

    import psycopg as _psycopg

    if args.inject_test_slot:
        with _psycopg.connect(DATABASE_URL) as conn:
            # Fetch the current calendar decision_id (required NOT NULL)
            _did = conn.execute(
                "SELECT id FROM decisions WHERE decision_type='calendar_plan' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()[0]
            # Insert a test approved row so build_pending has something to process
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO calendar_executions "
                    "(decision_id, slot_date, content_type, audience, topic_angle, status, notes) "
                    "VALUES (%s, %s, %s, %s, %s, 'approved', %s)",
                    (
                        _did,
                        "2026-05-19",
                        "klaviyo_campaign",
                        "lapsed_60_90d",
                        "Sleep science: why 50+ women wake at 3am",
                        json.dumps({
                            "send_time_est":   "10:00",
                            "discount_code":   "",
                            "discount_pct":    None,
                            "revenue_estimate": 300,
                            "needs_page":       False,
                            "priority":         "high",
                            "hierarchy_tier":   2,
                            "excluded_segment_ids": ["UBFUcH", "WSkan5", "TTN62U", "T2TXFk"],
                        }),
                    ),
                )
            conn.commit()
        print("Test slot injected — run with --dry-run --build-only to test")
    elif args.seed_only:
        with _psycopg.connect(DATABASE_URL) as conn:
            seed_approved_slots(conn, dry_run=args.dry_run, horizon_hours=args.horizon_hours)
    elif args.build_only:
        with _psycopg.connect(DATABASE_URL) as conn:
            build_pending(conn, dry_run=args.dry_run, horizon_hours=args.horizon_hours)
    else:
        run(dry_run=args.dry_run)
