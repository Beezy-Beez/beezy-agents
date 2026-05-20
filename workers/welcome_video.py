"""
workers/welcome_video.py
Personalized welcome-video worker.

Called every cron tick via app/main.py _run_cron_jobs.
Picks ONE pending welcome_video_jobs row, renders via HeyGen,
writes the URL to Klaviyo, marks the job complete.
On failure: retries up to 3 times, then marks 'dead' and pings Slack.
"""

import os
import json
import requests
from datetime import datetime, timedelta, timezone
from psycopg.rows import dict_row

from lib.heygen import render_personalized_video, HeyGenError
from lib import slack
from db import get_conn


KLAVIYO_API_KEY     = os.environ["KLAVIYO_API_KEY"]
KLAVIYO_PROFILE_URL = "https://a.klaviyo.com/api/profiles"
SLACK_CHANNEL       = "C0B3DEUJS9G"  # #beezy-agents

MAX_ATTEMPTS        = 3
WATCHDOG_TIMEOUT    = timedelta(minutes=10)


def _reset_stuck_jobs(conn) -> int:
    cutoff = datetime.now(timezone.utc) - WATCHDOG_TIMEOUT
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE welcome_video_jobs
               SET status = 'pending',
                   locked_at = NULL,
                   last_error = 'watchdog reset (stuck >10min)',
                   updated_at = NOW()
             WHERE status = 'processing'
               AND locked_at < %s
            """,
            (cutoff,),
        )
        return cur.rowcount


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


def run_once() -> dict:
    result = {"watchdog_reset": 0, "processed": False, "status": None}

    with get_conn() as conn:
        result["watchdog_reset"] = _reset_stuck_jobs(conn)
        conn.commit()

        job = _claim_next_job(conn)
        conn.commit()

        if not job:
            return result

        result["processed"] = True
        result["job_id"]    = job["id"]
        result["email"]     = job["email"]

        try:
            heygen_video_id, video_url = render_personalized_video(job["first_name"])
        except HeyGenError as e:
            status = _mark_failed_or_dead(conn, job["id"], job["attempts"], str(e))
            conn.commit()
            result["status"] = status
            if status == "dead":
                slack._post(
                    SLACK_CHANNEL,
                    f"🚨 *Welcome video FAILED 3x — manual intervention needed*\n"
                    f"Customer: {job['first_name']} ({job['email']})\n"
                    f"Last error: `{str(e)[:500]}`",
                )
            return result

        try:
            _write_klaviyo_profile(job["email"], video_url)
        except requests.HTTPError as e:
            status = _mark_failed_or_dead(
                conn, job["id"], job["attempts"],
                f"Klaviyo write failed: {e}",
            )
            conn.commit()
            result["status"] = status
            return result

        _mark_complete(conn, job["id"], heygen_video_id, video_url)
        conn.commit()
        slack._post(
            SLACK_CHANNEL,
            f"✅ Welcome video sent to *{job['first_name']}* ({job['email']})",
        )

        result["status"]    = "complete"
        result["video_url"] = video_url
        return result


if __name__ == "__main__":
    out = run_once()
    print(json.dumps(out, indent=2, default=str))
