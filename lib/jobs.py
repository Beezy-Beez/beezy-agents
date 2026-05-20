"""
Control-plane job tracking — the `run_job` context manager.

ONE row in `jobs` per discrete dispatched job. The wrapper is deliberately
dumb: it knows nothing about claim gates or time gates. The dispatcher decides
*whether* to enter run_job at all (see app/main.py). Inside the block:

    enter            → INSERT a 'running' row
    clean exit       → UPDATE 'succeeded' (+ finished_at, duration_ms, detail)
    job.skip(reason) → UPDATE 'skipped'   (ran, intentionally did nothing)
    exception        → UPDATE 'failed'    (+ error), then RE-RAISE

`skipped` NEVER means "lost the claim" or "time gate not met" — those paths
never enter run_job in the first place. It means the job ran and chose to
no-op (e.g. orchestrator with nothing approved).

Telemetry must never change job behavior: if the `jobs` table is unreachable,
the job still runs and still raises exactly as before. Every DB touch here —
the enter INSERT *and* the exit UPDATE — is wrapped so a telemetry-write
failure is printed and swallowed, never raised.
"""
import time
import traceback
from contextlib import contextmanager

_ERROR_MAX = 8000  # cap stored traceback text


class _Job:
    """Handle yielded by run_job. Mutate `.detail`; call `.skip()` to no-op."""

    def __init__(self, row_id):
        self.id = row_id          # jobs.id, or None if the INSERT failed
        self.detail = {}          # JSONB result payload, e.g. {"issue": 19}
        self._skipped = False
        self._skip_reason = None

    def skip(self, reason: str) -> None:
        """Mark this run as an intentional no-op. Does not raise or exit."""
        self._skipped = True
        self._skip_reason = reason


def _insert_running(job_name: str, trigger: str, attempt: int):
    """INSERT the running row. Returns jobs.id, or None on any DB failure."""
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "INSERT INTO jobs (job_name, trigger, attempt, status) "
                "VALUES (%s, %s, %s, 'running') RETURNING id",
                (job_name, trigger, attempt),
            ).fetchone()
            conn.commit()
        return row[0] if row else None
    except Exception as e:
        print(f"[jobs] could not INSERT running row for {job_name!r}: {e}")
        return None


def _finalize(row_id, status: str, started_monotonic: float,
              detail: dict, error: str | None) -> None:
    """
    UPDATE the row to a terminal state. Self-contained and total: every step
    (duration math, json encode, DB write) lives inside the try, so this
    function never raises — a telemetry failure is printed and swallowed.
    """
    if row_id is None:
        return
    try:
        import json
        from db.connection import get_conn
        duration_ms = int((time.monotonic() - started_monotonic) * 1000)
        with get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET status=%s, finished_at=now(), "
                "duration_ms=%s, detail=%s, error=%s WHERE id=%s",
                (status, duration_ms, json.dumps(detail or {}),
                 error, row_id),
            )
            conn.commit()
    except Exception as e:
        print(f"[jobs] could not finalize job id={row_id} "
              f"({status}): {e}")


@contextmanager
def run_job(job_name: str, trigger: str = "cron", attempt: int = 1):
    """
    Wrap a single discrete job dispatch.

        with run_job("publish_and_index", trigger="cron") as job:
            job.detail = {"issues_published": cron_publish_and_index()}

    Skip path (ran, intentionally did nothing):

        with run_job("orchestrator") as job:
            if not approved:
                job.skip("no approved week")
            else:
                job.detail = run_daily()
    """
    started = time.monotonic()
    row_id = _insert_running(job_name, trigger, attempt)
    job = _Job(row_id)
    try:
        yield job
    except Exception:
        err = traceback.format_exc()[-_ERROR_MAX:]
        # Telemetry never gates plumbing: the finalize is belt-and-braces
        # guarded so it can never mask or replace the original exception.
        try:
            _finalize(row_id, "failed", started, job.detail, err)
        except Exception as e:               # pragma: no cover — defensive
            print(f"[jobs] finalize(failed) raised, ignoring: {e}")
        raise                                # behavior unchanged — job fails
    else:
        try:
            if job._skipped:
                _finalize(row_id, "skipped", started, job.detail,
                          job._skip_reason)
            else:
                _finalize(row_id, "succeeded", started, job.detail, None)
        except Exception as e:               # pragma: no cover — defensive
            print(f"[jobs] finalize(clean exit) raised, ignoring: {e}")
