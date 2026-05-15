"""
Deliverability Monitor — checks Klaviyo bounce and spam complaint rates.
Runs daily after ingestion. Posts Slack alert if thresholds exceeded.

Thresholds (ISP best practice):
  Hard bounce rate  > 2%   → ALERT
  Spam complaint rate > 0.1% → ALERT (Gmail threshold for bulk senders)
  Unsubscribe rate  > 0.5% → WARN

Pulls from Klaviyo campaign-values-reports for the last 30 days.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import httpx
from config import KLAVIYO_REVISION

_BOUNCE_THRESHOLD = 0.02   # 2%
_SPAM_THRESHOLD   = 0.001  # 0.1%
_UNSUB_THRESHOLD  = 0.005  # 0.5%
_WINDOW_DAYS      = 30
_MAX_RETRIES      = 5

# Statistics confirmed valid in ingestion/klaviyo.py + deliverability-specific additions.
# hard_bounced / marked_as_spam are not in the core STATISTICS list but are valid
# Klaviyo report fields; _compute_rates falls back to `bounced` if they come back zero.
_STATISTICS = [
    "recipients",
    "delivered",
    "bounced",
    "hard_bounced",
    "soft_bounced",
    "marked_as_spam",
    "unsubscribes",   # Klaviyo field name — NOT "unsubscribed"
    "opens",
    "clicks",
]


def _headers() -> dict:
    return {
        "Authorization": "Klaviyo-API-Key " + os.environ.get("KLAVIYO_API_KEY", ""),
        "revision": KLAVIYO_REVISION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _post_with_retry(url: str, payload: dict) -> dict | None:
    """POST to Klaviyo with 429/5xx retry. Returns parsed JSON or None on failure."""
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = httpx.post(url, headers=_headers(), json=payload, timeout=30)
        except Exception as exc:
            backoff = min(2 ** attempt, 30)
            print(f"[deliverability] request error (attempt {attempt}/{_MAX_RETRIES}): {exc} — retrying in {backoff}s")
            time.sleep(backoff)
            continue

        if resp.status_code == 429:
            wait = float(resp.headers.get("Retry-After", "2"))
            print(f"[deliverability] 429 rate-limited — sleeping {wait}s (attempt {attempt}/{_MAX_RETRIES})")
            time.sleep(wait)
            continue

        if resp.status_code >= 500:
            backoff = min(2 ** attempt, 30)
            print(f"[deliverability] HTTP {resp.status_code} (attempt {attempt}/{_MAX_RETRIES}) — retrying in {backoff}s")
            time.sleep(backoff)
            continue

        if not resp.is_success:
            print(f"[deliverability] HTTP {resp.status_code}: {resp.text[:300]}")
            return None

        return resp.json()

    print(f"[deliverability] exhausted {_MAX_RETRIES} retries")
    return None


def _pull_deliverability_metrics() -> dict:
    """Pull aggregate campaign stats for the last 30 days from Klaviyo."""
    now   = datetime.now(timezone.utc)
    start = (now - timedelta(days=_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end   = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    payload = {
        "data": {
            "type": "campaign-values-report",
            "attributes": {
                "statistics": _STATISTICS,
                "timeframe": {"start": start, "end": end},
                "conversion_metric_id": "X93gjq",
            },
        }
    }

    body = _post_with_retry("https://a.klaviyo.com/api/campaign-values-reports/", payload)
    if body is None:
        return {"error": "Klaviyo pull failed after retries"}

    results = (body.get("data") or {}).get("attributes", {}).get("results", [])
    if not results:
        return {"error": "no results in response"}

    totals: dict[str, int] = {}
    for row in results:
        for k, v in (row.get("statistics") or {}).items():
            totals[k] = totals.get(k, 0) + (v or 0)
    return totals


def _compute_rates(totals: dict) -> dict:
    delivered  = max(totals.get("delivered", 0), 1)
    recipients = max(totals.get("recipients", 0), delivered)

    # hard_bounced preferred; fall back to total bounced if Klaviyo didn't return it
    hard_bounced   = totals.get("hard_bounced") or totals.get("bounced", 0)
    marked_as_spam = totals.get("marked_as_spam", 0)
    unsubscribed   = totals.get("unsubscribes", 0)   # Klaviyo field is "unsubscribes"
    bounced        = totals.get("bounced", 0)

    return {
        "recipients":       recipients,
        "delivered":        delivered,
        "hard_bounce_rate": hard_bounced   / recipients,
        "spam_rate":        marked_as_spam / recipients,
        "unsub_rate":       unsubscribed   / delivered,
        "bounce_rate":      bounced        / recipients,
        "hard_bounced":     hard_bounced,
        "marked_as_spam":   marked_as_spam,
        "unsubscribed":     unsubscribed,
    }


def _post_slack(msg: str) -> None:
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook:
        return
    try:
        httpx.post(webhook, json={"text": msg}, timeout=10)
    except Exception as exc:
        print(f"[deliverability] Slack post failed: {exc}")


def run_deliverability_check() -> dict:
    """
    Pull 30-day deliverability metrics, evaluate against thresholds,
    post Slack alert if any threshold exceeded.
    Returns a summary dict for logging.
    """
    if not os.environ.get("KLAVIYO_API_KEY", ""):
        print("[deliverability] No KLAVIYO_API_KEY — skipping")
        return {"skipped": True}

    totals = _pull_deliverability_metrics()
    if "error" in totals:
        print(f"[deliverability] Pull error: {totals['error']}")
        return totals

    rates  = _compute_rates(totals)
    alerts = []
    warns  = []

    if rates["hard_bounce_rate"] > _BOUNCE_THRESHOLD:
        alerts.append(
            f"🚨 *Hard bounce rate: {rates['hard_bounce_rate']:.2%}* "
            f"({rates['hard_bounced']:,} hard bounces / {rates['recipients']:,} recipients) "
            f"— threshold {_BOUNCE_THRESHOLD:.0%}"
        )

    if rates["spam_rate"] > _SPAM_THRESHOLD:
        alerts.append(
            f"🚨 *Spam complaint rate: {rates['spam_rate']:.3%}* "
            f"({rates['marked_as_spam']:,} complaints / {rates['recipients']:,} recipients) "
            f"— threshold {_SPAM_THRESHOLD:.1%}. *Gmail may suppress delivery.*"
        )

    if rates["unsub_rate"] > _UNSUB_THRESHOLD:
        warns.append(
            f"⚠️ *Unsubscribe rate: {rates['unsub_rate']:.2%}* "
            f"({rates['unsubscribed']:,} unsubs / {rates['delivered']:,} delivered) "
            f"— threshold {_UNSUB_THRESHOLD:.1%}"
        )

    if alerts or warns:
        lines = (
            [f"*Deliverability Alert — last {_WINDOW_DAYS} days*"]
            + alerts
            + warns
            + [
                f"_Delivered: {rates['delivered']:,} / Sent: {rates['recipients']:,}_",
                "_Review list hygiene and sending frequency immediately if bounces/spam are elevated._",
            ]
        )
        _post_slack("\n".join(lines))
        print(f"[deliverability] Alert posted: {len(alerts)} alerts, {len(warns)} warnings")
    else:
        print(
            f"[deliverability] All clear — "
            f"bounces={rates['hard_bounce_rate']:.2%} "
            f"spam={rates['spam_rate']:.3%} "
            f"unsubs={rates['unsub_rate']:.2%}"
        )

    return {
        "alerts": len(alerts),
        "warns":  len(warns),
        "rates":  {k: round(v, 5) if isinstance(v, float) else v for k, v in rates.items()},
    }
