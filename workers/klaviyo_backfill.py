"""
Klaviyo → calendar_executions backfill.

Pulls manually-sent Klaviyo campaigns for a given month, maps Klaviyo segment IDs
to internal audience names, and inserts finalized rows into calendar_executions so the
learning loop has real data.

Usage:
    python -m workers.klaviyo_backfill                         # default: current month
    python -m workers.klaviyo_backfill --month 2026-05         # May 2026
    python -m workers.klaviyo_backfill --dry-run               # print, don't write

    from workers.klaviyo_backfill import backfill_month
    backfill_month("2026-05")
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx

KLAVIYO_BASE     = "https://a.klaviyo.com/api"
KLAVIYO_REVISION = "2025-10-15"
CONVERSION_METRIC_ID = "X93gjq"  # Placed Order

# ── Klaviyo segment/list ID → internal audience name ──────────────────────────
# Add new IDs here as you discover them from campaign audience data.
SEGMENT_ID_TO_AUDIENCE: dict[str, str] = {
    # Confirmed May 2026 IDs (from CLAUDE.md)
    "UEQD6k": "lapsed_30d",
    "UfARWm": "lapsed_60d",
    "XuS7rY": "lapsed_90d",
    "W98qh3": "lapsed_180d",
    "RArtzN": "vip",
    "RvtHdn": "engaged_customers",
    "UBFUcH": "active_seal",
    "VAUD58": "whales",
    "Xrp3ha": "engaged_prospects",
    "Sme9Nq": "super_engaged",
    "QHV2s5": "inner_circle",
    "Y6VSre": "hive_mind_prospects",
    "XFSxZt": "all_customers",
    # Lists (for campaign audience matching)
    "Y6VSre": "hive_mind_prospects",
}

# Campaign name patterns → content_type (checked in order; first match wins)
_NAME_PATTERNS: list[tuple[str, str]] = [
    ("hive mind",        "newsletter"),
    ("newsletter",       "newsletter"),
    ("sleep story",      "sleep_story"),
    ("sleep guide",      "sleep_story"),
    ("blog",             "seo_blog"),
    ("seo",              "seo_blog"),
    ("winback",          "win_back"),
    ("win back",         "win_back"),
    ("replenish",        "replenishment"),
    ("reorder",          "replenishment"),
    ("welcome",          "welcome"),
    ("vip",              "product_feature"),
    ("launch",           "product_feature"),
    ("product",          "product_feature"),
    ("sale",             "promotional"),
    ("promo",            "promotional"),
    ("deal",             "promotional"),
    ("bundle",           "promotional"),
]


def _kv_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Klaviyo-API-Key {api_key}",
        "revision": KLAVIYO_REVISION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _get_campaigns_for_month(api_key: str, year: int, month: int) -> list[dict]:
    """Return all campaigns that sent in the given calendar month."""
    from calendar import monthrange
    days_in_month = monthrange(year, month)[1]
    since = datetime(year, month, 1, tzinfo=timezone.utc)
    until = datetime(year, month, days_in_month, 23, 59, 59, tzinfo=timezone.utc)

    # Fetch campaigns updated in the month window (status=sent)
    campaigns: list[dict] = []
    url = f"{KLAVIYO_BASE}/campaigns/"
    params = {
        "filter": f"equals(messages.channel,'email'),equals(status,'sent')",
        "fields[campaign]": "name,status,send_time,audiences",
        "sort": "-send_time",
        "page[size]": "50",
    }
    while url:
        resp = httpx.get(url, headers=_kv_headers(api_key), params=params, timeout=30)
        if not resp.is_success:
            print(f"[backfill] Klaviyo campaigns list {resp.status_code}: {resp.text[:200]}")
            break
        body  = resp.json()
        items = body.get("data", [])
        for item in items:
            attrs     = item.get("attributes", {})
            send_time = attrs.get("send_time") or ""
            try:
                dt = datetime.fromisoformat(send_time.replace("Z", "+00:00"))
            except Exception:
                continue
            if since <= dt <= until:
                campaigns.append(item)
            elif dt < since:
                # Items are sorted by send_time desc — once we fall below the window, stop
                return campaigns
        # Paginate
        next_link = body.get("links", {}).get("next")
        url    = next_link if next_link else None
        params = {}  # next link already includes params
        time.sleep(0.5)

    return campaigns


def _get_campaign_performance(api_key: str, campaign_id: str) -> dict | None:
    """Pull recipients + revenue + RPR for one campaign via the reporting API."""
    payload = {
        "data": {
            "type": "campaign-values-report",
            "attributes": {
                "statistics": ["recipients", "open_rate", "conversion_value", "revenue_per_recipient"],
                "timeframe": {"key": "last_365_days"},
                "conversion_metric_id": CONVERSION_METRIC_ID,
                "filter": f'equals(campaign_id,"{campaign_id}")',
            },
        }
    }
    resp = httpx.post(
        f"{KLAVIYO_BASE}/campaign-values-reports/",
        headers=_kv_headers(api_key), json=payload, timeout=30,
    )
    if not resp.is_success:
        print(f"[backfill] Report failed for {campaign_id}: {resp.status_code}")
        return None

    results = resp.json().get("data", {}).get("attributes", {}).get("results", [])
    if not results:
        return None

    # Aggregate across send channels
    total_recip   = 0.0
    total_revenue = 0.0
    or_sum        = 0.0
    row_count     = 0
    for row in results:
        stats = row.get("statistics", {})
        total_recip   += float(stats.get("recipients", 0) or 0)
        total_revenue += float(stats.get("conversion_value", 0) or 0)
        or_sum        += float(stats.get("open_rate", 0) or 0)
        row_count += 1

    rpr = total_revenue / total_recip if total_recip > 0 else 0.0
    return {
        "recipients": int(total_recip),
        "revenue":    round(total_revenue, 2),
        "rpr":        round(rpr, 4),
        "open_rate":  round(or_sum / max(row_count, 1), 4),
    }


def _infer_audience(segment_ids: list[str]) -> str:
    """Map a list of Klaviyo segment/list IDs to a best-guess internal audience name."""
    if not segment_ids:
        return "unknown"
    for sid in segment_ids:
        if sid in SEGMENT_ID_TO_AUDIENCE:
            return SEGMENT_ID_TO_AUDIENCE[sid]
    return f"klaviyo:{segment_ids[0]}"


def _infer_content_type(name: str) -> str:
    """Infer content_type from campaign name using keyword patterns."""
    lower = (name or "").lower()
    for pattern, ct in _NAME_PATTERNS:
        if pattern in lower:
            return ct
    return "campaign"


def backfill_month(month_str: str, *, dry_run: bool = False) -> str:
    """
    Pull all sent campaigns for `month_str` (e.g. '2026-05'), get performance,
    and insert finalized rows into calendar_executions.

    Skips campaigns already in calendar_executions (by klaviyo_campaign_id).
    Returns a summary string.
    """
    import os
    from db.connection import get_conn

    api_key = os.environ.get("KLAVIYO_API_KEY", "")
    if not api_key:
        raise RuntimeError("KLAVIYO_API_KEY is not set.")

    year, mon = map(int, month_str.split("-"))
    print(f"[backfill] Pulling Klaviyo campaigns for {month_str}...")
    campaigns = _get_campaigns_for_month(api_key, year, mon)
    print(f"[backfill] Found {len(campaigns)} sent campaigns in {month_str}")

    if not campaigns:
        return f"no_campaigns_found_for_{month_str}"

    # Load existing klaviyo_campaign_ids to avoid duplicates
    with get_conn() as conn:
        existing_rows = conn.execute(
            "SELECT klaviyo_campaign_id FROM calendar_executions WHERE klaviyo_campaign_id IS NOT NULL"
        ).fetchall()
    existing_ids = {r[0] for r in existing_rows}

    inserted = skipped = failed = 0
    for campaign in campaigns:
        cid   = campaign["id"]
        attrs = campaign.get("attributes", {})
        name  = attrs.get("name", "")

        if cid in existing_ids:
            print(f"[backfill]   SKIP {name[:50]} — already in DB")
            skipped += 1
            continue

        send_time_str = attrs.get("send_time") or ""
        try:
            send_dt   = datetime.fromisoformat(send_time_str.replace("Z", "+00:00"))
            slot_date = send_dt.date()
        except Exception:
            print(f"[backfill]   SKIP {name[:50]} — bad send_time '{send_time_str}'")
            skipped += 1
            continue

        audiences  = attrs.get("audiences") or {}
        seg_ids    = list(audiences.get("included") or [])
        audience   = _infer_audience(seg_ids)
        ct         = _infer_content_type(name)

        print(f"[backfill]   {name[:50]} → {audience}/{ct} on {slot_date}")

        perf = _get_campaign_performance(api_key, cid)
        if not perf:
            print(f"[backfill]     No performance data — using zeros")
            perf = {"recipients": 0, "revenue": 0.0, "rpr": 0.0, "open_rate": 0.0}

        time.sleep(0.3)  # rate limit courtesy

        if dry_run:
            print(f"[backfill]     DRY RUN: would insert recip={perf['recipients']} "
                  f"rev=${perf['revenue']} rpr=${perf['rpr']}")
            inserted += 1
            continue

        try:
            with get_conn() as conn:
                conn.execute(
                    """INSERT INTO calendar_executions
                         (decision_id, slot_date, content_type, audience, topic_angle,
                          status, notes, executed_at, klaviyo_campaign_id,
                          recipients, actual_revenue, actual_rpr,
                          is_preliminary, finalized_at)
                       VALUES
                         (gen_random_uuid(), %s, %s, %s, %s,
                          'completed', %s, %s, %s,
                          %s, %s, %s,
                          false, NOW())
                       ON CONFLICT DO NOTHING""",
                    (
                        slot_date, ct, audience, name[:200],
                        f"backfill:{month_str}",
                        send_dt,
                        cid,
                        perf["recipients"],
                        Decimal(str(perf["revenue"])),
                        Decimal(str(perf["rpr"])),
                    ),
                )
                conn.commit()
            inserted += 1
        except Exception as exc:
            print(f"[backfill]     DB insert failed: {exc}")
            failed += 1

    summary = (
        f"backfill {month_str}: {inserted} inserted, {skipped} skipped, {failed} failed "
        f"out of {len(campaigns)} campaigns"
    )
    print(f"[backfill] {summary}")
    return summary


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m workers.klaviyo_backfill")
    parser.add_argument("--month", default=None,
                        help="Month in YYYY-MM format (default: current month)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be inserted without writing to DB")
    args = parser.parse_args(argv[1:])

    if args.month:
        month_str = args.month
    else:
        today = date.today()
        month_str = today.strftime("%Y-%m")

    result = backfill_month(month_str, dry_run=args.dry_run)
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
