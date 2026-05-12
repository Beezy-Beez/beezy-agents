"""Klaviyo performance ingestion via the Reporting API.

For a (since, until) window, runs Campaign Values Reports and Flow Values
Reports against `conversion_metric_id = X93gjq` (Placed Order) and converts each
returned grouping into N `performance` rows (one per metric in `STATISTICS`).

Endpoints used (revision `KLAVIYO_API_REVISION`):
  POST /api/campaign-values-reports/   — campaign stats grouped by campaign+channel
  POST /api/flow-values-reports/       — flow stats grouped by flow+message+channel
  GET  /api/campaigns/{id}/            — hydrate name, send_time, audiences
  GET  /api/flows/{id}/                — hydrate name, trigger_type

The values-report endpoints already restrict results to entities that *sent*
inside the timeframe, so we don't need a separate "list campaigns sent in
window" step.

This module does NOT touch the database. `ingestion.sync` owns the transaction.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import requests

import config

logger = logging.getLogger(__name__)

# Klaviyo pins API behavior to a revision date sent in the `revision` header.
# Bump when adopting a newer stable revision; revisions are supported for ~12 months.
KLAVIYO_API_REVISION = "2025-10-15"

KLAVIYO_BASE = "https://a.klaviyo.com/api"
KLAVIYO_CONVERSION_METRIC_ID = "X93gjq"  # Placed Order — drives conversions + conversion_value

HTTP_TIMEOUT_SECONDS = 30
MAX_RETRIES = 6
# Pace the two values-report calls — they share a tight burst limit (~1/s) and
# we'd rather space them than thrash 429s. Hydration GETs are cheap and skip this.
REPORT_PACING_SECONDS = 1.5

# Statistics requested from values-reports. The API returns a dict keyed by these
# names; STAT_TO_METRIC then renames them to our canonical metric_names (e.g.
# delivered -> deliveries, bounced -> bounces).
STATISTICS = [
    "recipients",
    "delivered",
    "opens",
    "opens_unique",
    "clicks",
    "clicks_unique",
    "bounced",
    "unsubscribes",
    "conversions",
    "conversion_value",
    "revenue_per_recipient",
]

STAT_TO_METRIC = {
    "recipients": "recipients",
    "delivered": "deliveries",
    "opens": "opens",
    "opens_unique": "opens_unique",
    "clicks": "clicks",
    "clicks_unique": "clicks_unique",
    "bounced": "bounces",
    "unsubscribes": "unsubscribes",
    "conversions": "conversions",
    "conversion_value": "conversion_value",
    "revenue_per_recipient": "revenue_per_recipient",
}


class KlaviyoAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class CampaignMeta:
    id: str
    name: str
    send_time: str | None  # ISO 8601, from /campaigns/{id}.attributes.send_time
    segment_ids: list[str]  # audiences.included


@dataclass(frozen=True)
class FlowMeta:
    id: str
    name: str
    trigger_type: str | None


def _headers() -> dict[str, str]:
    if not config.KLAVIYO_API_KEY:
        raise RuntimeError("KLAVIYO_API_KEY is not set in env / Replit Secrets")
    return {
        "Authorization": f"Klaviyo-API-Key {config.KLAVIYO_API_KEY}",
        "revision": KLAVIYO_API_REVISION,
        "accept": "application/vnd.api+json",
        "content-type": "application/vnd.api+json",
    }


def _request(method: str, path: str, *, json_body: dict | None = None) -> dict[str, Any]:
    """HTTP wrapper with 429/Retry-After and 5xx backoff."""
    url = f"{KLAVIYO_BASE}{path}"
    headers = _headers()

    for attempt in range(1, MAX_RETRIES + 1):
        resp = requests.request(
            method, url, headers=headers, json=json_body, timeout=HTTP_TIMEOUT_SECONDS
        )

        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", "2"))
            logger.warning(
                "Klaviyo 429 on %s %s — sleeping %.1fs (attempt %d/%d)",
                method, path, retry_after, attempt, MAX_RETRIES,
            )
            time.sleep(retry_after)
            continue

        if resp.status_code >= 500:
            backoff = min(2 ** attempt, 30)
            logger.warning(
                "Klaviyo %d on %s %s — sleeping %.1fs (attempt %d/%d)",
                resp.status_code, method, path, backoff, attempt, MAX_RETRIES,
            )
            time.sleep(backoff)
            continue

        if resp.status_code >= 400:
            raise KlaviyoAPIError(
                f"Klaviyo HTTP {resp.status_code} on {method} {path}: {resp.text[:500]}"
            )

        return resp.json()

    raise KlaviyoAPIError(f"Klaviyo {method} {path} exhausted {MAX_RETRIES} retries")


def _iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _post_values_report(
    report_type: str,  # "campaign-values-report" | "flow-values-report"
    since: datetime,
    until: datetime,
) -> list[dict[str, Any]]:
    """POST a values-report; return the list of grouping/statistics result objects."""
    path = f"/{report_type}s/"
    payload = {
        "data": {
            "type": report_type,
            "attributes": {
                "statistics": STATISTICS,
                "timeframe": {"start": _iso_utc(since), "end": _iso_utc(until)},
                "conversion_metric_id": KLAVIYO_CONVERSION_METRIC_ID,
            },
        }
    }
    body = _request("POST", path, json_body=payload)
    results = ((body.get("data") or {}).get("attributes") or {}).get("results") or []
    logger.info("Klaviyo %s: %d results", report_type, len(results))
    return results


def _get_campaign_meta(campaign_id: str) -> CampaignMeta:
    body = _request("GET", f"/campaigns/{campaign_id}/")
    attrs = ((body.get("data") or {}).get("attributes") or {})
    audiences = attrs.get("audiences") or {}
    return CampaignMeta(
        id=campaign_id,
        name=attrs.get("name") or "",
        send_time=attrs.get("send_time"),
        segment_ids=list(audiences.get("included") or []),
    )


def _get_flow_meta(flow_id: str) -> FlowMeta:
    body = _request("GET", f"/flows/{flow_id}/")
    attrs = ((body.get("data") or {}).get("attributes") or {})
    return FlowMeta(
        id=flow_id,
        name=attrs.get("name") or "",
        trigger_type=attrs.get("trigger_type"),
    )


def _stats_to_rows(
    *,
    stats: dict[str, Any],
    dimensions: dict[str, Any],
    since: datetime,
    until: datetime,
) -> list[dict[str, Any]]:
    """Convert a single results.statistics dict into N performance rows."""
    rows: list[dict[str, Any]] = []
    for stat_key, metric_name in STAT_TO_METRIC.items():
        if stat_key not in stats:
            continue
        value = stats[stat_key]
        if value is None:
            continue
        rows.append({
            "source": "klaviyo",
            "metric_name": metric_name,
            "metric_value": Decimal(str(value)),
            "dimensions": dimensions,
            "window_start": since,
            "window_end": until,
        })
    return rows


def pull_campaigns(since: datetime, until: datetime) -> list[dict[str, Any]]:
    """Run the Campaign Values Report and hydrate per-campaign metadata.

    Returns performance rows ready for INSERT. One row per (campaign, channel, metric).
    """
    results = _post_values_report("campaign-values-report", since, until)

    unique_ids = sorted({
        (r.get("groupings") or {}).get("campaign_id")
        for r in results
        if (r.get("groupings") or {}).get("campaign_id")
    })
    meta_cache: dict[str, CampaignMeta] = {}
    for cid in unique_ids:
        try:
            meta_cache[cid] = _get_campaign_meta(cid)
        except KlaviyoAPIError as e:
            logger.warning("Could not hydrate campaign %s: %s", cid, e)
            meta_cache[cid] = CampaignMeta(id=cid, name="", send_time=None, segment_ids=[])

    rows: list[dict[str, Any]] = []
    for result in results:
        groupings = result.get("groupings") or {}
        stats = result.get("statistics") or {}
        cid = groupings.get("campaign_id")
        if not cid:
            continue
        meta = meta_cache.get(cid) or CampaignMeta(id=cid, name="", send_time=None, segment_ids=[])
        dimensions = {
            "kind": "campaign",
            "entity_id": cid,
            "entity_name": meta.name,
            "send_channel": groupings.get("send_channel"),
            "campaign_message_id": groupings.get("campaign_message_id"),
            "send_time": meta.send_time,
            "segment_ids": meta.segment_ids,
        }
        rows.extend(_stats_to_rows(stats=stats, dimensions=dimensions, since=since, until=until))
    return rows


def pull_flows(since: datetime, until: datetime) -> list[dict[str, Any]]:
    """Run the Flow Values Report and hydrate per-flow metadata.

    Returns performance rows ready for INSERT. One row per (flow, message, channel, metric).
    """
    # Pace so we don't slam two values-reports back to back.
    time.sleep(REPORT_PACING_SECONDS)

    results = _post_values_report("flow-values-report", since, until)

    unique_ids = sorted({
        (r.get("groupings") or {}).get("flow_id")
        for r in results
        if (r.get("groupings") or {}).get("flow_id")
    })
    meta_cache: dict[str, FlowMeta] = {}
    for fid in unique_ids:
        try:
            meta_cache[fid] = _get_flow_meta(fid)
        except KlaviyoAPIError as e:
            logger.warning("Could not hydrate flow %s: %s", fid, e)
            meta_cache[fid] = FlowMeta(id=fid, name="", trigger_type=None)

    rows: list[dict[str, Any]] = []
    for result in results:
        groupings = result.get("groupings") or {}
        stats = result.get("statistics") or {}
        fid = groupings.get("flow_id")
        if not fid:
            continue
        meta = meta_cache.get(fid) or FlowMeta(id=fid, name="", trigger_type=None)
        dimensions = {
            "kind": "flow",
            "entity_id": fid,
            "entity_name": meta.name,
            "send_channel": groupings.get("send_channel"),
            "flow_message_id": groupings.get("flow_message_id"),
            "trigger_type": meta.trigger_type,
        }
        rows.extend(_stats_to_rows(stats=stats, dimensions=dimensions, since=since, until=until))
    return rows


def pull_all(since: datetime, until: datetime) -> list[dict[str, Any]]:
    """Run both campaign + flow reports for the window. Returns combined rows."""
    rows = pull_campaigns(since, until)
    rows.extend(pull_flows(since, until))
    return rows
