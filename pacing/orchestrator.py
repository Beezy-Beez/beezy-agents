from __future__ import annotations
import json
from datetime import date, timedelta

from db.connection import get_conn
from lib.slack import notify_failure, post_draft


# Ordered by estimated $/send (RPR × median list size) from calendar_live_data fallback.
# Only HIGH_FREQ and top MODERATE audiences — never lapsed_90d+ or cold prospects.
BOOST_AUDIENCE_PRIORITY = [
    # (audience,              est_$/send, topic_angle)
    ("lapsed_30d",  967,  "Why women 50+ wake at 3am — your CBN reset protocol"),
    ("vip",         873,  "The sleep compound your doctor doesn't know about yet"),
    ("active_seal", 648,  "Your Beehive Club benefit this month: deep sleep protocol"),
    ("whales",      683,  "The 45-day reorder window: your sleep stack is due"),
    ("engaged_customers", 1347, "Sleep science: the cortisol window women 50+ keep missing"),
    ("one_time_buyers",   725,  "Your first jar worked — here's the next step"),
]

MODE_LABELS = {
    "boost":    "BOOST MODE",
    "push":     "PUSH MODE",
    "maintain": "On Track",
    "ease":     "EASE MODE",
}

MODE_EMOJI = {
    "boost":    "URGENT",
    "push":     "PUSH",
    "maintain": "OK",
    "ease":     "AHEAD",
}


def _latest_calendar(conn):
    month = date.today().strftime("%Y-%m")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, output FROM decisions WHERE decision_type = 'calendar_plan' AND output->>'month' = %s ORDER BY created_at DESC LIMIT 1",
            (month,)
        )
        row = cur.fetchone()
    if not row:
        return None, []
    payload = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    return str(row[0]), payload.get("slots", [])


def _todays_slots(slots):
    return [s for s in slots if s.get("date") == date.today().isoformat()]


def _is_approved(conn) -> bool:
    today = date.today()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM calendar_approvals WHERE week_start <= %s AND %s < week_start + INTERVAL '7 days' AND approved_at IS NOT NULL LIMIT 1",
            (today, today)
        )
        return cur.fetchone() is not None


def _today_priority_mode(conn) -> str:
    """Read today's priority mode from priorities table. Default: maintain."""
    try:
        row = conn.execute(
            "SELECT prioritized_workers, pacing_snapshot FROM priorities WHERE effective_for=%s ORDER BY decided_at DESC LIMIT 1",
            (date.today(),)
        ).fetchone()
        if row and row[0]:
            workers = row[0] if isinstance(row[0], list) else json.loads(row[0])
            return workers[0] if workers else "maintain"
    except Exception:
        pass
    return "maintain"


def _already_ran(conn, decision_id, slot):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM calendar_executions "
            "WHERE slot_date = %s AND content_type = %s AND audience = %s "
            "AND status NOT IN ('failed', 'skipped') LIMIT 1",
            (slot["date"], slot.get("content_type"), slot.get("audience", ""))
        )
        return cur.fetchone() is not None


def _audience_in_cooldown(conn, audience: str) -> bool:
    """Return True if this audience was sent to within the last 7 days (R2 check)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM calendar_executions "
            "WHERE audience = %s "
            "AND slot_date >= CURRENT_DATE - INTERVAL '7 days' "
            "AND status NOT IN ('failed', 'skipped') LIMIT 1",
            (audience,)
        )
        return cur.fetchone() is not None


def _audience_sent_today(conn, audience: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM calendar_executions WHERE audience = %s AND slot_date = %s LIMIT 1",
            (audience, date.today().isoformat())
        )
        return cur.fetchone() is not None


def _boost_candidate_slot(conn) -> dict | None:
    """
    BOOST mode: find the best eligible audience for an emergency extra send.
    Respects R2 (7-day cooldown) — never bypasses it.
    Returns a ready-to-dispatch slot dict, or None if all are in cooldown.
    """
    today = date.today().isoformat()
    for audience, est_rev, topic in BOOST_AUDIENCE_PRIORITY:
        if _audience_in_cooldown(conn, audience):
            print(f"[orchestrator/boost] {audience} in 7-day cooldown — skipping")
            continue
        if _audience_sent_today(conn, audience):
            print(f"[orchestrator/boost] {audience} already sent today — skipping")
            continue
        print(f"[orchestrator/boost] Selected emergency slot: {audience} (~${est_rev}/send)")
        return {
            "date":             today,
            "content_type":     "klaviyo_campaign",
            "audience":         audience,
            "topic_angle":      topic + " [BOOST]",
            "send_time_est":    "15:00",
            "priority":         "high",
            "revenue_estimate": float(est_rev),
            "needs_page":       False,
            "rationale":        f"BOOST emergency slot — {audience} cooldown-free, est ${est_rev}/send",
            "goal_alignment":   "Emergency revenue recovery, pacing >20% behind target",
        }
    print("[orchestrator/boost] All BOOST_AUDIENCE_PRIORITY audiences are in cooldown — no extra slot added")
    return None


def _ease_drop_weakest(slots: list[dict]) -> tuple[list[dict], dict | None]:
    """
    EASE mode: if at or above cadence limit (3 sends), drop the single
    lowest-priority, lowest-revenue non-email slot to ease cadence pressure.
    Only drops if it won't take today below 2 sends.
    """
    # Count only email/campaign slots toward cadence (not SEO, sleep_audio, hive_mind)
    campaign_slots = [s for s in slots if s.get("content_type") in ("klaviyo_campaign", "sniper_followup", "sms_campaign")]
    if len(campaign_slots) < 3:
        return slots, None

    # Find the lowest-priority, lowest-revenue campaign slot
    low_pri = [s for s in campaign_slots if s.get("priority") in ("low", "medium", None)]
    if not low_pri:
        return slots, None

    weakest = min(low_pri, key=lambda s: float(s.get("revenue_estimate", 0) or 0))
    remaining = [s for s in slots if s is not weakest]
    print(
        f"[orchestrator/ease] Dropping {weakest.get('audience')}/{weakest.get('content_type')} "
        f"(${weakest.get('revenue_estimate', 0)} est) — EASE mode, at cadence limit"
    )
    return remaining, weakest


def _mark(conn, decision_id, slot, status, notes="", klaviyo_campaign_id=None):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO calendar_executions "
            "(decision_id, slot_date, content_type, audience, topic_angle, status, notes, klaviyo_campaign_id, is_preliminary) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (decision_id, slot["date"], slot.get("content_type"), slot.get("audience",""),
             slot.get("topic_angle",""), status, notes, klaviyo_campaign_id,
             True if klaviyo_campaign_id else None)
        )
    conn.commit()


def _handle_seo_blog(slot):
    from workers.seo_blog import run as seo_run
    result = seo_run(slot)
    return "published:" + result.get("url", "?")


def _handle_campaign(slot):
    """Tier 1: generate copy + create Klaviyo DRAFT campaign automatically."""
    from workers.beezy_campaign import run as campaign_run
    result = campaign_run(slot)
    return "klaviyo_draft:" + result.get("campaign_id","?")


def _handle_flow_experiment(slot):
    post_draft(
        title="Flow Experiment -- " + slot["date"],
        summary_lines=[
            "Task:     " + slot.get("topic_angle","?"),
            "Audience: " + slot.get("audience","?"),
            "Priority: " + slot.get("priority","?"),
        ],
        body="*Rationale:* " + slot.get("rationale","") + "\n\n*Goal:* " + slot.get("goal_alignment","") + "\n\n_Implement in Klaviyo flows._",
    )
    return "slack_notified"


def _handle_sleep_audio(slot):
    post_draft(
        title="Sleep Audio Slot -- " + slot["date"],
        summary_lines=[
            "Topic:     " + slot.get("topic_angle","?"),
            "Audience:  " + slot.get("audience","?"),
            "Rev. Est.: $" + str(int(slot.get("revenue_estimate",0))),
        ],
        body=(
            "*Checklist:*\n"
            "1. Check *#beezy-new-episodes* -- is an episode ready?\n"
            "2. If yes -> open episode deployer Claude chat: 'deploy latest episode'\n"
            "3. If no -> open sleep-audio-platform chat and produce: _" + slot.get("topic_angle","") + "_"
        ),
    )
    return "slack_notified"


def _handle_sms(slot):
    """Full autonomous SMS pipeline."""
    from workers.sms_campaign import run_sms_campaign
    return run_sms_campaign(slot)


HANDLERS = {
    "seo_blog":         _handle_seo_blog,
    "klaviyo_campaign": _handle_campaign,
    "sniper_followup":  _handle_campaign,
    "flow_experiment":  _handle_flow_experiment,
    "sleep_audio":      _handle_sleep_audio,
    "sms_campaign":     _handle_sms,
    "hive_mind":        lambda s: "skipped:hive_mind_cron_owns_this",
}


def run_daily():
    print("[orchestrator] Daily dispatch starting...")
    with get_conn() as conn:
        if not _is_approved(conn):
            print("[orchestrator] Week not approved -- skipping.")
            post_draft(
                title="Orchestrator Paused -- Approval Pending",
                summary_lines=["Date: " + date.today().isoformat(), "Status: WAITING FOR APPROVAL"],
                body="Run the weekly brief approval command from Slack.",
            )
            return

        priority_mode = _today_priority_mode(conn)
        print(f"[orchestrator] Priority mode: {priority_mode}")

        decision_id, all_slots = _latest_calendar(conn)
        if not decision_id:
            print("[orchestrator] No calendar plan for this month.")
            return

        today_slots = _todays_slots(all_slots)
        boost_slot   = None
        dropped_slot = None

        # BOOST / PUSH: sort highest-RPR first so top earners run even if later ones fail
        if priority_mode in ("boost", "push"):
            today_slots = sorted(today_slots, key=lambda s: float(s.get("revenue_estimate", 0) or 0), reverse=True)

        # BOOST only: inject an emergency extra send if any audience is cooldown-free
        if priority_mode == "boost":
            boost_slot = _boost_candidate_slot(conn)
            if boost_slot:
                today_slots.append(boost_slot)
                print(f"[orchestrator] BOOST slot added: {boost_slot['audience']}")

        # EASE: drop the weakest campaign slot if we're at cadence limit
        if priority_mode == "ease":
            today_slots, dropped_slot = _ease_drop_weakest(today_slots)

        print("[orchestrator] " + str(len(today_slots)) + " slot(s) today (" + date.today().isoformat() + ")")
        if not today_slots:
            return

        dispatched, skipped, failed = [], [], []
        for slot in today_slots:
            ct    = slot.get("content_type","unknown")
            label = ct + "/" + slot.get("audience","?")
            if _already_ran(conn, decision_id, slot):
                skipped.append(label)
                continue
            handler = HANDLERS.get(ct)
            if not handler:
                _mark(conn, decision_id, slot, "skipped", "no handler for " + ct)
                skipped.append(label)
                continue
            try:
                print("[orchestrator]   -> " + label)
                notes = handler(slot)
                # Extract Klaviyo campaign_id from handler return value
                klaviyo_id = None
                if isinstance(notes, str) and notes.startswith("klaviyo_draft:"):
                    klaviyo_id = notes[len("klaviyo_draft:"):]
                elif isinstance(notes, dict) and notes.get("campaign_id"):
                    klaviyo_id = notes["campaign_id"]
                if isinstance(notes, dict):
                    notes = "klaviyo_draft:" + notes.get("campaign_id", "?")
                _mark(conn, decision_id, slot, "dispatched", notes, klaviyo_campaign_id=klaviyo_id)
                dispatched.append(label + ":" + (notes or ""))
            except Exception as e:
                err = str(e)
                print("[orchestrator]   FAIL " + label + ": " + err)
                _mark(conn, decision_id, slot, "failed", err)
                failed.append(label + ":" + err)
                notify_failure(source="orchestrator/" + ct, error=err)

        # Build Slack summary
        mode_badge = MODE_LABELS.get(priority_mode, priority_mode.upper())
        title = f"Daily Dispatch [{mode_badge}] — {date.today().strftime('%b %d, %Y')}"

        lines = [
            "Date: " + date.today().isoformat() + "  |  Mode: " + mode_badge,
            "Dispatched: " + str(len(dispatched)) + "  Skipped: " + str(len(skipped)) + "  Failed: " + str(len(failed)),
        ]
        if boost_slot:
            lines.append(
                "BOOST INJECT: " + boost_slot["audience"] +
                " @ 3pm — est $" + str(int(boost_slot["revenue_estimate"])) + "/send"
            )
        if dropped_slot:
            lines.append(
                "EASE DROP: " + dropped_slot.get("audience","?") + "/" +
                dropped_slot.get("content_type","?") +
                " — est $" + str(int(dropped_slot.get("revenue_estimate", 0) or 0)) + " (cadence limit)"
            )
        if dispatched:
            lines.append("OK " + " | ".join(dispatched[:6]))
        if failed:
            lines.append("FAIL " + " | ".join(failed))

        post_draft(
            title=title,
            summary_lines=lines,
            body="Calendar plan ID: " + str(decision_id),
        )
    print("[orchestrator] Done.")


if __name__ == "__main__":
    run_daily()
