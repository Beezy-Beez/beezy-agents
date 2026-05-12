"""Ingestion orchestrator.

For each source: compute the (since, until) window from the last successful
ingestion_runs row, call the source's pull, and write performance rows +
the success ingestion_runs row in a single transaction.

CLI:
    python -m ingestion.sync shopify
    python -m ingestion.sync klaviyo
    python -m ingestion.sync all                          # shopify then klaviyo
    python -m ingestion.sync shopify --lookback-days 30   # one-off backfill
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from psycopg.types.json import Jsonb

import config
from db.connection import get_conn
from ingestion import klaviyo, shopify

logger = logging.getLogger(__name__)

BOOTSTRAP_LOOKBACK = timedelta(days=7)


def _notify_slack_failure(source: str, result: dict[str, Any]) -> None:
    """POST a failure summary to SLACK_WEBHOOK_URL. No-op if webhook unset.

    Called from main() after a sync returns non-success. Failures here are
    swallowed (logged) so a broken webhook never re-raises into the caller.
    """
    if not config.SLACK_WEBHOOK_URL:
        logger.warning(
            "SLACK_WEBHOOK_URL not set — skipping failure notification for %s (run_id=%s)",
            source, result.get("ingestion_run_id"),
        )
        return

    text = (
        f":rotating_light: *{source}* ingestion {result.get('status')}\n"
        f"Window: {result.get('since')} → {result.get('until')}\n"
        f"Run: `{result.get('ingestion_run_id')}`\n"
        f"Error: ```{result.get('error', 'no error message')}```"
    )
    try:
        resp = requests.post(config.SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)
        if resp.status_code >= 300:
            logger.error("Slack webhook returned %d: %s", resp.status_code, resp.text[:200])
    except Exception:
        logger.exception("Slack failure notification POST failed for %s", source)


def _maybe_notify(source: str, result: dict[str, Any]) -> None:
    """Notify Slack on any non-success status (covers 'failed', 'error', 'partial')."""
    if result.get("status") != "success":
        _notify_slack_failure(source, result)


def _last_successful_window_end(conn, source: str) -> datetime | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            select window_end
              from ingestion_runs
             where source = %s and status = 'success'
             order by created_at desc
             limit 1
            """,
            (source,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def _insert_performance_rows(conn, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into performance
              (run_id, source, metric_name, metric_value, dimensions, window_start, window_end)
            values
              (NULL, %(source)s, %(metric_name)s, %(metric_value)s, %(dimensions)s, %(window_start)s, %(window_end)s)
            """,
            [
                {**r, "dimensions": Jsonb(r["dimensions"])}
                for r in rows
            ],
        )


def _insert_ingestion_run(
    conn,
    *,
    source: str,
    window_start: datetime,
    window_end: datetime,
    records_ingested: int,
    status: str,
    error: str | None = None,
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into ingestion_runs
              (source, window_start, window_end, records_ingested, status, error)
            values
              (%s, %s, %s, %s, %s, %s)
            returning id
            """,
            (source, window_start, window_end, records_ingested, status, error),
        )
        return str(cur.fetchone()[0])


def run_shopify_sync(window_hours: int = 4, lookback_days: int | None = None) -> dict[str, Any]:
    """Pull Shopify orders since the last successful run; default bootstrap = 7d.

    `window_hours` is kept for signature stability (this function is invoked on a
    ~4h cadence) but the actual window is derived from `ingestion_runs`.

    `lookback_days`, when set, forces `since = until - lookback_days` and ignores
    the last-successful-run cursor. Use for one-off backfills only — production
    cadence relies on the cursor.

    Returns a summary dict: {since, until, orders, rows_inserted, status, ingestion_run_id}.
    """
    until = datetime.now(timezone.utc).replace(microsecond=0)

    if lookback_days is not None:
        since = until - timedelta(days=lookback_days)
    else:
        # Compute the window in its own short-lived connection so we don't hold a
        # transaction open across the (potentially slow) Shopify API calls.
        with get_conn() as conn:
            last_end = _last_successful_window_end(conn, "shopify")
        since = last_end or (until - BOOTSTRAP_LOOKBACK)
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    logger.info("Shopify sync window: %s -> %s", since.isoformat(), until.isoformat())

    try:
        orders = shopify.pull_orders(since, until)
        rows = shopify.to_performance_rows(orders, since, until)
        logger.info("Pulled %d orders -> %d performance rows", len(orders), len(rows))

        with get_conn() as conn:
            with conn.transaction():
                _insert_performance_rows(conn, rows)
                run_id = _insert_ingestion_run(
                    conn,
                    source="shopify",
                    window_start=since,
                    window_end=until,
                    records_ingested=len(rows),
                    status="success",
                )

        return {
            "since": since.isoformat(),
            "until": until.isoformat(),
            "orders": len(orders),
            "rows_inserted": len(rows),
            "status": "success",
            "ingestion_run_id": run_id,
        }

    except Exception as e:
        logger.exception("Shopify sync failed")
        # Failure row goes in its own transaction so the failure is recorded
        # even though the data writes were rolled back.
        with get_conn() as conn:
            with conn.transaction():
                run_id = _insert_ingestion_run(
                    conn,
                    source="shopify",
                    window_start=since,
                    window_end=until,
                    records_ingested=0,
                    status="error",
                    error=f"{type(e).__name__}: {e}",
                )
        return {
            "since": since.isoformat(),
            "until": until.isoformat(),
            "orders": 0,
            "rows_inserted": 0,
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
            "ingestion_run_id": run_id,
        }


def run_klaviyo_sync(window_hours: int = 4, lookback_days: int | None = None) -> dict[str, Any]:
    """Pull Klaviyo campaign + flow report stats since the last successful run.

    Same cadence/cursor semantics as `run_shopify_sync`. The window resolution
    falls back to BOOTSTRAP_LOOKBACK when there's no prior successful run.

    Returns a summary dict: {since, until, campaign_rows, flow_rows, rows_inserted,
    status, ingestion_run_id}.
    """
    until = datetime.now(timezone.utc).replace(microsecond=0)

    if lookback_days is not None:
        since = until - timedelta(days=lookback_days)
    else:
        with get_conn() as conn:
            last_end = _last_successful_window_end(conn, "klaviyo")
        since = last_end or (until - BOOTSTRAP_LOOKBACK)
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    logger.info("Klaviyo sync window: %s -> %s", since.isoformat(), until.isoformat())

    try:
        campaign_rows = klaviyo.pull_campaigns(since, until)
        flow_rows = klaviyo.pull_flows(since, until)
        rows = campaign_rows + flow_rows
        logger.info(
            "Pulled %d campaign rows + %d flow rows = %d performance rows",
            len(campaign_rows), len(flow_rows), len(rows),
        )

        with get_conn() as conn:
            with conn.transaction():
                _insert_performance_rows(conn, rows)
                run_id = _insert_ingestion_run(
                    conn,
                    source="klaviyo",
                    window_start=since,
                    window_end=until,
                    records_ingested=len(rows),
                    status="success",
                )

        return {
            "since": since.isoformat(),
            "until": until.isoformat(),
            "campaign_rows": len(campaign_rows),
            "flow_rows": len(flow_rows),
            "rows_inserted": len(rows),
            "status": "success",
            "ingestion_run_id": run_id,
        }

    except Exception as e:
        logger.exception("Klaviyo sync failed")
        with get_conn() as conn:
            with conn.transaction():
                run_id = _insert_ingestion_run(
                    conn,
                    source="klaviyo",
                    window_start=since,
                    window_end=until,
                    records_ingested=0,
                    status="error",
                    error=f"{type(e).__name__}: {e}",
                )
        return {
            "since": since.isoformat(),
            "until": until.isoformat(),
            "campaign_rows": 0,
            "flow_rows": 0,
            "rows_inserted": 0,
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
            "ingestion_run_id": run_id,
        }


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(argv) < 2:
        print("usage: python -m ingestion.sync <shopify|klaviyo|all> [--lookback-days N]", file=sys.stderr)
        return 2
    source = argv[1]

    lookback_days: int | None = None
    extra = argv[2:]
    if extra:
        if len(extra) == 2 and extra[0] == "--lookback-days":
            lookback_days = int(extra[1])
        else:
            print(f"unknown args: {extra}", file=sys.stderr)
            return 2

    if source == "shopify":
        result = run_shopify_sync(lookback_days=lookback_days)
        _maybe_notify("shopify", result)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "success" else 1
    if source == "klaviyo":
        result = run_klaviyo_sync(lookback_days=lookback_days)
        _maybe_notify("klaviyo", result)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "success" else 1
    if source == "all":
        shopify_result = run_shopify_sync(lookback_days=lookback_days)
        _maybe_notify("shopify", shopify_result)
        klaviyo_result = run_klaviyo_sync(lookback_days=lookback_days)
        _maybe_notify("klaviyo", klaviyo_result)
        combined = {"shopify": shopify_result, "klaviyo": klaviyo_result}
        print(json.dumps(combined, indent=2))
        return 0 if shopify_result["status"] == "success" and klaviyo_result["status"] == "success" else 1
    print(f"unknown source: {source}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
