"""
workers/welcome_video.py
Personalized welcome-video worker — non-blocking.

Called every cron tick via app/main.py _run_cron_jobs.
Each tick:
  1. Watchdog: reset rows stuck in 'processing' > WATCHDOG_TIMEOUT
  2. For up to 10 in-flight HeyGen renders, one-shot status check; finish or fail
  3. Claim ONE pending row and submit_video (do not block on render)

A row stays in 'processing' across many ticks while HeyGen renders;
the cron loop drains it via check_status().
On failure: retries up to 3 times, then marks 'dead' and pings Slack.
"""

import os
import json
import requests
from datetime import datetime, timedelta, timezone
from psycopg.rows import dict_row

from lib.heygen import submit_video, check_status, HeyGenError
from lib import slack
from db import get_conn


KLAVIYO_API_KEY     = os.environ["KLAVIYO_API_KEY"]
KLAVIYO_PROFILE_URL = "https://a.klaviyo.com/api/profiles"
SLACK_CHANNEL       = "C0B3DEUJS9G"  # #beezy-agents

MAX_ATTEMPTS        = 3
WATCHDOG_TIMEOUT    = timedelta(minutes=15)
IN_FLIGHT_LIMIT     = 10


def _reset_stuck_jobs(conn) -> int:
    cutoff = datetime.now(timezone.utc) - WATCHDOG_TIMEOUT
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE welcome_video_jobs
               SET status = 'pending',
                   locked_at = NULL,
                   last_error = 'watchdog reset (stuck >15min)',
                   updated_at = NOW()
             WHERE status = 'processing'
               AND locked_at < %s
            """,
            (cutoff,),
        )
        return cur.rowcount


def _fetch_in_flight_jobs(conn) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT *
              FROM welcome_video_jobs
             WHERE status = 'processing'
               AND heygen_video_id IS NOT NULL
             ORDER BY locked_at ASC
             LIMIT %s
            """,
            (IN_FLIGHT_LIMIT,),
        )
        return cur.fetchall()


def _claim_next_job(conn) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE welcome_video_jobs
               SET status     = 'processing',
                   locked_at  = NOW(),
                   attempts   = attempts + 1,
                   updated_at = NOW()
             WHERE id = (
                 SELECT id
                   FROM welcome_video_jobs
                  WHERE status = 'pending'
                    AND attempts < %s
                  ORDER BY created_at ASC
                  LIMIT 1
                  FOR UPDATE SKIP LOCKED
             )
             RETURNING *
            """,
            (MAX_ATTEMPTS,),
        )
        return cur.fetchone()


def _set_heygen_video_id(conn, job_id: int, heygen_video_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE welcome_video_jobs
               SET heygen_video_id = %s,
                   updated_at      = NOW()
             WHERE id = %s
            """,
            (heygen_video_id, job_id),
        )


def _write_klaviyo_profile(email: str, video_url: str) -> None:
    headers = {
        "Authorization": f"Klaviyo-API-Key {KLAVIYO_API_KEY}",
        "revision": "2024-10-15",
        "accept": "application/json",
        "content-type": "application/json",
    }

    lookup = requests.get(
        f"{KLAVIYO_PROFILE_URL}/?filter=equals(email,\"{email}\")",
        headers=headers,
        timeout=15,
    )
    lookup.raise_for_status()
    profiles = lookup.json().get("data", [])

    if not profiles:
        create = requests.post(
            KLAVIYO_PROFILE_URL + "/",
            json={
                "data": {
                    "type": "profile",
                    "attributes": {
                        "email": email,
                        "properties": {"welcome_video_url": video_url},
                    },
                }
            },
            headers=headers,
            timeout=15,
        )
        create.raise_for_status()
        return

    profile_id = profiles[0]["id"]

    update = requests.patch(
        f"{KLAVIYO_PROFILE_URL}/{profile_id}/",
        json={
            "data": {
                "type": "profile",
                "id": profile_id,
                "attributes": {
                    "properties": {"welcome_video_url": video_url},
                },
            }
        },
        headers=headers,
        timeout=15,
    )
    update.raise_for_status()


def _mark_complete(conn, job_id: int, heygen_video_id: str, video_url: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE welcome_video_jobs
               SET status          = 'complete',
                   heygen_video_id = %s,
                   video_url       = %s,
                   locked_at       = NULL,
                   updated_at      = NOW()
             WHERE id = %s
            """,
            (heygen_video_id, video_url, job_id),
        )


def _mark_failed_or_dead(conn, job_id: int, attempts: int, error: str) -> str:
    new_status = "dead" if attempts >= MAX_ATTEMPTS else "pending"
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE welcome_video_jobs
               SET status     = %s,
                   locked_at  = NULL,
                   last_error = %s,
                   updated_at = NOW()
             WHERE id = %s
            """,
            (new_status, error[:1000], job_id),
        )
    return new_status


def _notify_success(first_name: str, email: str) -> None:
    slack._post(
        SLACK_CHANNEL,
        f"✅ Welcome video sent to *{first_name}* ({email})",
    )


def _notify_dead(first_name: str, email: str, error: str) -> None:
    slack._post(
        SLACK_CHANNEL,
        f"🚨 *Welcome video FAILED 3x — manual intervention needed*\n"
        f"Customer: {first_name} ({email})\n"
        f"Last error: `{error[:500]}`",
    )


def run_once() -> dict:
    result = {
        "watchdog_reset": 0,
        "in_flight_checked": 0,
        "completed": 0,
        "failed": 0,
        "submitted": 0,
    }

    with get_conn() as conn:
        # 1. Watchdog
        result["watchdog_reset"] = _reset_stuck_jobs(conn)
        conn.commit()

        # 2. Drain in-flight renders
        in_flight = _fetch_in_flight_jobs(conn)
        result["in_flight_checked"] = len(in_flight)

        for job in in_flight:
            try:
                status = check_status(job["heygen_video_id"])
            except HeyGenError:
                # Transient status-API error — leave the row alone; next tick retries.
                # Truly stuck rows are caught by the watchdog.
                continue

            s = status.get("status")

            if s == "completed":
                url = status.get("video_url")
                if not url:
                    # Completed but no URL — treat as still rendering and try next tick.
                    continue
                try:
                    _write_klaviyo_profile(job["email"], url)
                except requests.HTTPError as e:
                    new_status = _mark_failed_or_dead(
                        conn, job["id"], job["attempts"],
                        f"Klaviyo write failed: {e}",
                    )
                    conn.commit()
                    result["failed"] += 1
                    if new_status == "dead":
                        _notify_dead(job["first_name"], job["email"], f"Klaviyo write failed: {e}")
                    continue

                _mark_complete(conn, job["id"], job["heygen_video_id"], url)
                conn.commit()
                _notify_success(job["first_name"], job["email"])
                result["completed"] += 1

            elif s == "failed":
                err = str(status.get("error") or "HeyGen render failed (no detail)")
                new_status = _mark_failed_or_dead(
                    conn, job["id"], job["attempts"], err,
                )
                conn.commit()
                result["failed"] += 1
                if new_status == "dead":
                    _notify_dead(job["first_name"], job["email"], err)

            # else: pending/processing/waiting — still rendering, skip.

        # 3. Claim and submit one pending job
        job = _claim_next_job(conn)
        conn.commit()

        if not job:
            return result

        result["job_id"] = job["id"]
        result["email"]  = job["email"]

        try:
            video_id = submit_video(job["first_name"])
        except HeyGenError as e:
            new_status = _mark_failed_or_dead(conn, job["id"], job["attempts"], str(e))
            conn.commit()
            result["submit_failed"] = True
            if new_status == "dead":
                _notify_dead(job["first_name"], job["email"], str(e))
            return result

        _set_heygen_video_id(conn, job["id"], video_id)
        conn.commit()
        result["submitted"] = 1
        result["heygen_video_id"] = video_id
        return result


if __name__ == "__main__":
    out = run_once()
    print(json.dumps(out, indent=2, default=str))
