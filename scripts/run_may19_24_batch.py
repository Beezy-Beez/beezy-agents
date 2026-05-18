"""
May 19-24 batch runner.

Builds:
  - May 21  klaviyo_campaign  lapsed_30d   "Just checking in: how have you been sleeping?"
  - May 22  klaviyo_campaign  vip          "Your next jar is waiting"  (product, no code)
  - May 24  klaviyo_campaign  inner_circle "Hive Club: the math every night"  (membership)

Triggers:
  - May 20  sleep_audio  guided_meditation  "Releasing the day"
  - May 23  sleep_audio  soundscape         "Summer thunderstorm"
  - May 21  Hive Mind Issue 016 — already built; posts Slack confirmation
  - May 24  Hive Mind Issue 017 — draft + page + Klaviyo campaign
"""
from __future__ import annotations

import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.connection import get_conn
from lib.slack import post_draft, notify_failure

DECISION_ID = "36e381fe-a8cd-4161-bf4c-74829159dfbd"

# ── Step 1: inject approved campaign slots ────────────────────────────────────

CUSTOM_SLOTS = [
    {
        "slot_date":    "2026-05-21",
        "content_type": "klaviyo_campaign",
        "audience":     "lapsed_30d",
        "topic_angle":  "Just checking in: how have you been sleeping?",
        "notes": json.dumps({
            "send_time_est":    "14:00",
            "discount_code":    "HEY20",
            "discount_pct":     20,
            "revenue_estimate": 966,
            "needs_page":       False,
            "priority":         "high",
            "hierarchy_tier":   1,
        }),
    },
    {
        "slot_date":    "2026-05-22",
        "content_type": "klaviyo_campaign",
        "audience":     "vip",
        "topic_angle":  "Your next jar is waiting",
        "notes": json.dumps({
            "send_time_est":    "10:00",
            "discount_code":    "",
            "discount_pct":     None,
            "revenue_estimate": 873,
            "needs_page":       False,
            "priority":         "high",
            "hierarchy_tier":   1,
        }),
    },
    {
        "slot_date":    "2026-05-24",
        "content_type": "klaviyo_campaign",
        "audience":     "inner_circle",
        "topic_angle":  "Hive Club: the math every night",
        "notes": json.dumps({
            "send_time_est":    "14:00",
            "discount_code":    "",
            "discount_pct":     None,
            "revenue_estimate": 534,
            "needs_page":       False,
            "priority":         "high",
            "hierarchy_tier":   1,
        }),
    },
]


def inject_approved_slots() -> None:
    with get_conn() as conn:
        for slot in CUSTOM_SLOTS:
            label = f"{slot['slot_date']} {slot['content_type']}/{slot['audience']}"
            try:
                cur = conn.execute(
                    "SELECT 1 FROM calendar_executions "
                    "WHERE slot_date = %s AND content_type = %s AND audience = %s "
                    "AND status NOT IN ('failed', 'skipped') LIMIT 1",
                    (slot["slot_date"], slot["content_type"], slot["audience"]),
                )
                if cur.fetchone():
                    print(f"[inject] {label} — already exists, skipping")
                    continue

                conn.execute(
                    "INSERT INTO calendar_executions "
                    "(decision_id, slot_date, content_type, audience, topic_angle, status, notes) "
                    "VALUES (%s, %s, %s, %s, %s, 'approved', %s)",
                    (
                        DECISION_ID,
                        slot["slot_date"],
                        slot["content_type"],
                        slot["audience"],
                        slot["topic_angle"],
                        slot["notes"],
                    ),
                )
                conn.commit()
                print(f"[inject] Seeded: {label}")
            except Exception as exc:
                print(f"[inject] ERROR seeding {label}: {exc}")
                traceback.print_exc()


# ── Step 2: build customer campaigns ─────────────────────────────────────────

def build_campaigns() -> None:
    from workers.calendar_campaign_builder import build_pending
    with get_conn() as conn:
        results = build_pending(conn, horizon_hours=200)
    n_ok  = sum(1 for r in results if r["status"] == "dispatched")
    n_bad = len(results) - n_ok
    print(f"[campaigns] Done — dispatched={n_ok} other={n_bad}")


# ── Step 3: sleep audio ───────────────────────────────────────────────────────

SLEEP_AUDIO_SLOTS = [
    {
        "date":             "2026-05-20",
        "content_type":     "sleep_audio",
        "audience":         "active_seal",
        "topic_angle":      "Guided meditation: Releasing the day — shoulder-to-shoulder release",
        "episode_type":     "guided_meditation",
        "duration_minutes": 25,
        "send_time_est":    "20:00",
        "priority":         "high",
    },
    {
        "date":             "2026-05-23",
        "content_type":     "sleep_audio",
        "audience":         "active_seal",
        "topic_angle":      "Soundscape: Summer thunderstorm rolling across a wheat field",
        "episode_type":     "soundscape",
        "duration_minutes": 30,
        "send_time_est":    "20:00",
        "priority":         "high",
    },
]


def run_sleep_audio() -> None:
    from workers.sleep_audio_producer import run_sleep_audio_slot
    for slot in SLEEP_AUDIO_SLOTS:
        label = f"sleep_audio {slot['date']} ({slot['episode_type']})"
        try:
            print(f"[sleep_audio] Starting: {slot['topic_angle']}")
            result = run_sleep_audio_slot(slot)
            print(f"[sleep_audio] Done: {result}")
        except Exception as exc:
            print(f"[sleep_audio] ERROR — {label}: {exc}")
            traceback.print_exc()
            notify_failure(label, str(exc))


# ── Step 4: Hive Mind ─────────────────────────────────────────────────────────

def run_hive_mind() -> None:
    # Issue 016: already fully built — post Slack confirmation
    post_draft(
        title="Hive Mind Issue 016 — Ready for May 21",
        summary_lines=[
            "✅ *Issue 016 already complete* — Shopify page + Klaviyo draft ready",
            "*Subject:* Your body has to cool down before your brain will let go",
            "*Page:* https://trybeezybeez.com/pages/body-temperature-drop-sleep-onset-circadian",
            "*Campaign ID:* `01KRNYZRSAZDR681PMVX4Y5VAD`",
            "Schedule the Klaviyo send for *May 21 at 8pm ET* when ready.",
        ],
        body="",
    )

    # Issue 017: draft + page + Klaviyo campaign
    print("[hive_mind] Drafting Issue 017 via workers.run...")
    try:
        from workers.run import main as hive_main
        exit_code = hive_main(["--skill", "hive_mind", "--issue", "17"])
        if exit_code != 0:
            print(f"[hive_mind] workers.run returned exit_code={exit_code} — aborting campaign creation")
            return
        print("[hive_mind] Draft complete — creating Shopify page + Klaviyo campaign...")
    except Exception as exc:
        print(f"[hive_mind] Draft failed: {exc}")
        traceback.print_exc()
        notify_failure("hive_mind_issue_017_draft", str(exc))
        return

    try:
        from workers.klaviyo_campaign import create_campaign_for_issue
        result = create_campaign_for_issue(17)
        print(f"[hive_mind] Issue 017 campaign created: {result.get('campaign_id', '?')[:16]}...")
    except Exception as exc:
        print(f"[hive_mind] Campaign creation failed: {exc}")
        traceback.print_exc()
        notify_failure("hive_mind_issue_017_campaign", str(exc))


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("STEP 1: Injecting approved campaign slots")
    print("=" * 60)
    inject_approved_slots()

    print()
    print("=" * 60)
    print("STEP 2: Building customer campaigns (may take ~15 min)")
    print("=" * 60)
    build_campaigns()

    print()
    print("=" * 60)
    print("STEP 3: Sleep audio pipelines")
    print("=" * 60)
    run_sleep_audio()

    print()
    print("=" * 60)
    print("STEP 4: Hive Mind Issue 016 confirm + Issue 017 pipeline")
    print("=" * 60)
    run_hive_mind()

    print()
    print("=" * 60)
    print("ALL DONE")
    print("=" * 60)
