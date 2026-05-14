from __future__ import annotations
import json
from datetime import date, timedelta

from db.connection import get_conn
from lib.slack import notify_failure, post_draft


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


def _already_ran(conn, decision_id, slot):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM calendar_executions WHERE decision_id = %s AND slot_date = %s AND content_type = %s AND audience = %s AND status != 'failed' LIMIT 1",
            (decision_id, slot["date"], slot.get("content_type"), slot.get("audience", ""))
        )
        return cur.fetchone() is not None


def _mark(conn, decision_id, slot, status, notes=""):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO calendar_executions (decision_id, slot_date, content_type, audience, topic_angle, status, notes) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (decision_id, slot["date"], slot.get("content_type"), slot.get("audience",""), slot.get("topic_angle",""), status, notes)
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
    post_draft(
        title="SMS Brief -- " + slot["date"],
        summary_lines=["Audience: " + slot.get("audience","?"), "Rev. Est.: $" + str(int(slot.get("revenue_estimate",0)))],
        body="*Angle:* " + slot.get("topic_angle","") + "\n\n*Rationale:* " + slot.get("rationale","") + "\n\n_Build and deploy via Klaviyo SMS. Max 2x/month._",
    )
    return "slack_draft"


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
            week_start = date.today() - timedelta(days=date.today().weekday())
            print("[orchestrator] Week not approved -- skipping.")
            post_draft(
                title="Orchestrator Paused -- Approval Pending",
                summary_lines=["Date: " + date.today().isoformat(), "Status: WAITING FOR APPROVAL"],
                body="Run the weekly brief approval command from Slack.",
            )
            return

        decision_id, all_slots = _latest_calendar(conn)
        if not decision_id:
            print("[orchestrator] No calendar plan for this month.")
            return

        today_slots = _todays_slots(all_slots)
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
                _mark(conn, decision_id, slot, "dispatched", notes)
                dispatched.append(label + ":" + notes)
            except Exception as e:
                err = str(e)
                print("[orchestrator]   FAIL " + label + ": " + err)
                _mark(conn, decision_id, slot, "failed", err)
                failed.append(label + ":" + err)
                notify_failure(source="orchestrator/" + ct, error=err)

        lines = [
            "Date: " + date.today().isoformat(),
            "Dispatched: " + str(len(dispatched)) + "  Skipped: " + str(len(skipped)) + "  Failed: " + str(len(failed)),
        ]
        if dispatched: lines.append("OK " + " | ".join(dispatched[:6]))
        if failed:     lines.append("FAIL " + " | ".join(failed))
        post_draft(
            title="Daily Dispatch -- " + date.today().strftime("%b %d, %Y"),
            summary_lines=lines,
            body="Calendar plan ID: " + str(decision_id),
        )
    print("[orchestrator] Done.")


if __name__ == "__main__":
    run_daily()
