"""
workers/watchdog.py — control-plane watchdog (Step 4).

Runs hourly from _run_cron_jobs, wrapped in run_job like every other job.
Reads the `jobs` table and checks every job in EXPECTED_SCHEDULE for three
conditions, emitting ONE consolidated message per tick (never one per job):

  • overdue — no succeeded/skipped run within cadence + grace
  • failed  — the most recent run is `failed`
  • stuck   — a row has been `running` past its max runtime

Plus a once-a-day heartbeat (the dispatcher gates it to the 11:00 ET tick, so
it reports the day's critical jobs). A *missing* heartbeat is itself the alarm.

Slack routing (lib/slack.py):
  • problem digest → _post() with a custom header that attributes the problem
    to the failing JOBS ("🔴 Watchdog — N job(s) need attention"). We do NOT
    use notify_failure() here: it renders "❌ watchdog failed", which would
    wrongly imply the watchdog itself broke.
  • watchdog's OWN crash → notify_failure(source="watchdog", ...) then re-raise
    so run_job also records the watchdog row failed. That is the only case
    where the "watchdog failed" wording is correct.
  • heartbeat → post_draft() (informational 📝).

`watchdog` is intentionally NOT in EXPECTED_SCHEDULE — the monitor does not
monitor itself; its liveness signal is the daily heartbeat's presence.
"""
from __future__ import annotations

import traceback
from datetime import datetime, timezone, timedelta

# cadence_min / grace_min in minutes; None ⇒ overdue check skipped.
# max_runtime_min drives the stuck check.
#
# `pattern` is informational. "claim" jobs are gated by _try_claim_today in
# the dispatcher. There is only ONE running instance (the Replit web server):
# that single instance wins the claim on the first eligible tick of the day,
# and every later tick in the catch-up window loses it (the agent_state value
# already equals today), so the job does not re-run. It therefore still
# produces exactly ONE row per day — so overdue logic for a "claim" job is
# identical to a plain daily "time" job. (The claim is restart/catch-up
# safety, not multi-instance arbitration — there is no second instance.)
# "watchdog" jobs are predicate-gated and legitimately silent for days →
# overdue is skipped for them (failed + stuck still apply).
EXPECTED_SCHEDULE: dict[str, dict] = {
    # daily time-gated
    "ingestion_sync":         {"pattern": "time",     "cadence_min": 240,   "grace_min": 120,  "max_runtime_min": 20},
    "pacing_cache_refresh":   {"pattern": "time",     "cadence_min": 1440,  "grace_min": 360,  "max_runtime_min": 15},
    "revenue_backfill":       {"pattern": "time",     "cadence_min": 1440,  "grace_min": 360,  "max_runtime_min": 20},
    "hive_mind_campaign":     {"pattern": "time",     "cadence_min": 1440,  "grace_min": 360,  "max_runtime_min": 30},
    "deliverability_check":   {"pattern": "time",     "cadence_min": 1440,  "grace_min": 360,  "max_runtime_min": 20},
    "hive_mind_status_sync":  {"pattern": "time",     "cadence_min": 1440,  "grace_min": 360,  "max_runtime_min": 15},
    # daily claim-gated (overdue logic == daily; see note above)
    "cron_pacing_brain":      {"pattern": "claim",    "cadence_min": 1440,  "grace_min": 360,  "max_runtime_min": 30},
    "cron_orchestrator":      {"pattern": "claim",    "cadence_min": 1440,  "grace_min": 360,  "max_runtime_min": 45},
    "cron_audience_health":   {"pattern": "claim",    "cadence_min": 1440,  "grace_min": 360,  "max_runtime_min": 20},
    "cron_morning_brief":     {"pattern": "claim",    "cadence_min": 1440,  "grace_min": 360,  "max_runtime_min": 15},
    "cron_publish_and_index": {"pattern": "claim",    "cadence_min": 1440,  "grace_min": 360,  "max_runtime_min": 30},
    # weekly (Sunday / Monday)
    "learning_loop_weekly":   {"pattern": "time",     "cadence_min": 10080, "grace_min": 1440, "max_runtime_min": 45},
    "weekly_brief":           {"pattern": "time",     "cadence_min": 10080, "grace_min": 1440, "max_runtime_min": 15},
    "flow_monitor":           {"pattern": "time",     "cadence_min": 10080, "grace_min": 1440, "max_runtime_min": 20},
    "approval_nudge":         {"pattern": "time",     "cadence_min": 10080, "grace_min": 1440, "max_runtime_min": 15},
    # monthly (specific calendar day) — 31d + 3d grace
    "calendar_generation":    {"pattern": "time",     "cadence_min": 44640, "grace_min": 4320, "max_runtime_min": 60},
    "learning_loop_biweekly": {"pattern": "time",     "cadence_min": 44640, "grace_min": 4320, "max_runtime_min": 45},
    "learning_loop_monthly":  {"pattern": "time",     "cadence_min": 44640, "grace_min": 4320, "max_runtime_min": 45},
    # predicate-gated watchdogs — NO overdue (legitimately silent); failed+stuck only
    "pending_schedules":      {"pattern": "watchdog", "cadence_min": None,  "grace_min": None, "max_runtime_min": 20},
    "tts_timeout_watchdog":   {"pattern": "watchdog", "cadence_min": None,  "grace_min": None, "max_runtime_min": 15},
}

# Curated daily-critical subset shown in the heartbeat's "last run" line.
KEY_JOBS = ["ingestion_sync", "cron_pacing_brain", "cron_orchestrator",
            "cron_publish_and_index", "hive_mind_campaign"]


def _hum(minutes: int) -> str:
    """Human cadence/grace: 240→'4h', 1440→'24h', 10080→'7d', 44640→'31d'."""
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def _fmt_age(minutes: float) -> str:
    m = int(minutes)
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h {m % 60:02d}m"


def _collect_problems(now: datetime) -> tuple[list[tuple[str, str]], dict]:
    """One DB pass. Returns ([(job_name, problem_line), ...], agg-by-job)."""
    from db.connection import get_conn
    names = list(EXPECTED_SCHEDULE)
    with get_conn() as conn:
        epoch = conn.execute("SELECT MIN(started_at) FROM jobs").fetchone()[0]
        rows = conn.execute(
            """SELECT job_name,
                      MAX(started_at) FILTER (WHERE status IN ('succeeded','skipped')) AS last_ok,
                      (ARRAY_AGG(status     ORDER BY started_at DESC))[1] AS latest_status,
                      (ARRAY_AGG(started_at ORDER BY started_at DESC))[1] AS latest_at
                 FROM jobs WHERE job_name = ANY(%s) GROUP BY job_name""",
            (names,),
        ).fetchall()
        agg = {r[0]: {"last_ok": r[1], "latest_status": r[2], "latest_at": r[3]}
               for r in rows}
        run_rows = conn.execute(
            """SELECT job_name, MIN(started_at)
                 FROM jobs WHERE job_name = ANY(%s) AND status = 'running'
                 GROUP BY job_name""",
            (names,),
        ).fetchall()
        oldest_running = {r[0]: r[1] for r in run_rows}

    problems: list[tuple[str, str]] = []
    for name, spec in EXPECTED_SCHEDULE.items():
        a = agg.get(name, {})
        last_ok       = a.get("last_ok")
        latest_status = a.get("latest_status")
        latest_at     = a.get("latest_at")

        # failed — most recent run is failed (all patterns)
        if latest_status == "failed":
            when = latest_at.strftime("%Y-%m-%d %H:%M UTC") if latest_at else "?"
            problems.append((name, f"*{name}* — failed: latest run {when}"))

        # stuck — a row running past max runtime (all patterns; also catches
        # an orphaned 'running' row from a hard crash before run_job finalized)
        oldest = oldest_running.get(name)
        if oldest is not None:
            age = (now - oldest).total_seconds() / 60
            if age > spec["max_runtime_min"]:
                problems.append((name,
                    f"*{name}* — stuck: running {_fmt_age(age)} "
                    f"(max {spec['max_runtime_min']}m)"))

        # overdue — skipped for watchdog-pattern (legitimately silent)
        cadence = spec["cadence_min"]
        if cadence is None:
            continue
        threshold = cadence + spec["grace_min"]
        if last_ok is not None:
            age = (now - last_ok).total_seconds() / 60
            if age > threshold:
                problems.append((name,
                    f"*{name}* — overdue: last ok {_fmt_age(age)} ago "
                    f"(cadence {_hum(cadence)} + grace {_hum(spec['grace_min'])})"))
        elif epoch is not None and (now - epoch).total_seconds() / 60 > threshold:
            # never succeeded, but the control plane has been online long
            # enough that we'd expect at least one run by now.
            problems.append((name,
                f"*{name}* — overdue: never run since control plane online "
                f"(expected every {_hum(cadence)})"))

    return problems, agg


def _post_problem_digest(problems: list[tuple[str, str]]) -> None:
    from lib.slack import _post
    n = len({p[0] for p in problems})
    header = f"🔴 Watchdog — {n} job{'' if n == 1 else 's'} need attention"
    text = "\n".join(line for _, line in problems)[:2800]
    _post({"blocks": [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
    ]})


def _post_heartbeat(now: datetime, agg: dict) -> None:
    from db.connection import get_conn
    from lib.slack import post_draft
    since = now - timedelta(hours=24)
    with get_conn() as conn:
        frows = conn.execute(
            "SELECT job_name, COUNT(*) FROM jobs "
            "WHERE status = 'failed' AND started_at > %s "
            "GROUP BY job_name ORDER BY job_name",
            (since,),
        ).fetchall()
    fail_total = sum(r[1] for r in frows)
    fail_desc = ", ".join(f"{r[0]}×{r[1]}" if r[1] > 1 else r[0]
                          for r in frows) or "none"
    key_lines = []
    for k in KEY_JOBS:
        la = agg.get(k, {}).get("latest_at")
        key_lines.append(f"{k} {la.strftime('%m-%d %H:%M') if la else 'never'}")
    post_draft(
        title="Watchdog heartbeat",
        summary_lines=[
            f"Jobs tracked: {len(EXPECTED_SCHEDULE)}",
            f"Failures last 24h: {fail_total} ({fail_desc})",
            "Last run (UTC) — " + " · ".join(key_lines),
        ],
        body="",
    )


def run_watchdog(emit_heartbeat: bool = False) -> dict:
    """Hourly control-plane check. Returns a summary dict for job.detail."""
    now = datetime.now(timezone.utc)
    try:
        problems, agg = _collect_problems(now)
        if problems:
            _post_problem_digest(problems)
        if emit_heartbeat:
            _post_heartbeat(now, agg)
        return {
            "problems": len(problems),
            "jobs": sorted({name for name, _ in problems}),
            "heartbeat": bool(emit_heartbeat),
        }
    except Exception:
        # A genuine watchdog crash — the ONLY case where "watchdog failed"
        # is the correct message. Re-raise so run_job records it failed too.
        from lib.slack import notify_failure
        notify_failure(source="watchdog",
                        error=traceback.format_exc()[-3000:])
        raise
