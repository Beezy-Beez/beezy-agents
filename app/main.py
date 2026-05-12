"""FastAPI entrypoint.

Run locally:
    uvicorn app.main:app --reload --port 8000
"""

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from db.connection import get_conn

from . import slack

app = FastAPI(title="Beezy Multi-Agent System")
app.include_router(slack.router)

# Ingestion sources monitored by /healthz. Extend as new sources come online.
MONITORED_SOURCES = ("shopify", "klaviyo")

# More than this many hours since the last successful run means we've missed at
# least one 4h cron tick — return 503 so external monitors can page.
STALE_THRESHOLD_HOURS = 6


def _latest_successful_run(conn, source: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            select window_end, records_ingested, created_at
              from ingestion_runs
             where source = %s and status = 'success'
             order by created_at desc
             limit 1
            """,
            (source,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    window_end, records_ingested, created_at = row
    return {
        "window_end": window_end,
        "records": records_ingested,
        "created_at": created_at,
    }


@app.get("/healthz")
def healthz():
    """Return latest ingestion status per source; 503 if any source is stale."""
    now = datetime.now(timezone.utc)
    per_source: dict[str, dict] = {}
    stale = False

    with get_conn() as conn:
        for source in MONITORED_SOURCES:
            latest = _latest_successful_run(conn, source)
            if latest is None:
                per_source[source] = {
                    "last_success": None,
                    "age_hours": None,
                    "records": None,
                }
                stale = True
                continue

            age_hours = round((now - latest["created_at"]).total_seconds() / 3600, 2)
            per_source[source] = {
                "last_success": latest["created_at"].isoformat(),
                "age_hours": age_hours,
                "records": latest["records"],
            }
            if age_hours > STALE_THRESHOLD_HOURS:
                stale = True

    body = {"ok": not stale, "stale_threshold_hours": STALE_THRESHOLD_HOURS, **per_source}
    return JSONResponse(body, status_code=503 if stale else 200)
