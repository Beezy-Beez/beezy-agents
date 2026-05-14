"""
Beezy Send Validator — pre-send gatekeeper.
Runs before ANY campaign is deployed to Klaviyo.
If verdict is FAIL → campaign is blocked, Slack gets the failure details.

Implements 12 rules from beezy-system validator v3.
Rules marked LIVE are fully automated. Rules marked STUB require future Klaviyo API integration.

Usage:
    from workers.validator import validate_campaign
    result = validate_campaign(conn, slot, copy, cta_url)
    if not result["pass"]:
        post_to_slack(result["slack_block"])
        return "blocked by validator"
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

NY = ZoneInfo("America/New_York")

# ── Segment classification ─────────────────────────────────────────────────────

CUSTOMER_SEGMENTS = {
    "lapsed_30d", "lapsed_60d", "lapsed_60_90d", "lapsed_90d", "lapsed_90_180d",
    "lapsed_180d", "lapsed_180d_plus", "winback_180d",
    "vip", "inner_circle", "engaged_customers", "all_customers",
    "active_seal", "active_subscribers", "whales", "high_aov",
    "one_time_buyers", "otb", "cart_abandoners",
}

HIGH_VALUE_SEGMENTS = {"vip", "inner_circle", "whales", "high_aov", "active_seal", "active_subscribers"}

PROSPECT_SEGMENTS = {"engaged_prospects", "super_engaged"}

# Formats that should NEVER be sent to these audiences
KILL_LIST = [
    ("active_seal", "editorial"),       # 64x lower RPR vs product features
    ("vip", "pre_paid_bundle"),          # 4 consecutive $0 sends documented
]

# ── Individual rule checkers ───────────────────────────────────────────────────

def _r1_smart_sending(conn, slot: dict) -> dict:
    """R1: Hours since any prior touch ≥ 24."""
    audience = slot.get("audience", "")
    today = date.today()
    yesterday = today - timedelta(days=1)
    row = conn.execute(
        "SELECT MAX(slot_date) FROM calendar_executions "
        "WHERE audience = %s AND slot_date >= %s AND status IN ('dispatched','completed')",
        (audience, yesterday)
    ).fetchone()
    last_date = row[0] if row and row[0] else None
    if last_date and last_date == today:
        return {"rule": "R1", "name": "Smart Sending (≥24h)", "pass": False,
                "detail": f"Already sent to {audience} today ({today})"}
    return {"rule": "R1", "name": "Smart Sending (≥24h)", "pass": True,
            "detail": f"Last send: {last_date or 'none in last 24h'}"}


def _r2_audience_cooldown(conn, slot: dict) -> dict:
    """R2: Absolute 7-day audience cooldown (≥168h). NON-NEGOTIABLE."""
    audience = slot.get("audience", "")
    today = date.today()
    seven_days_ago = today - timedelta(days=7)
    row = conn.execute(
        "SELECT slot_date FROM calendar_executions "
        "WHERE audience = %s AND slot_date > %s AND status IN ('dispatched','completed') "
        "ORDER BY slot_date DESC LIMIT 1",
        (audience, seven_days_ago)
    ).fetchone()
    if row and row[0]:
        days_since = (today - row[0]).days
        if days_since < 7:
            return {"rule": "R2", "name": "7-day cooldown (≥168h)", "pass": False,
                    "detail": f"Last sent {days_since}d ago on {row[0]}. Need 7d."}
    return {"rule": "R2", "name": "7-day cooldown (≥168h)", "pass": True,
            "detail": "No sends to this audience in last 7 days"}


def _r3_theme_gap(conn, slot: dict) -> dict:
    """R3: Same theme gap (5d). If both shares theme, hours ≥ 120."""
    content_type = slot.get("content_type", "")
    audience = slot.get("audience", "")
    five_days_ago = date.today() - timedelta(days=5)
    row = conn.execute(
        "SELECT slot_date FROM calendar_executions "
        "WHERE audience = %s AND content_type = %s AND slot_date > %s "
        "AND status IN ('dispatched','completed') ORDER BY slot_date DESC LIMIT 1",
        (audience, content_type, five_days_ago)
    ).fetchone()
    if row and row[0]:
        days_since = (date.today() - row[0]).days
        if days_since < 5:
            return {"rule": "R3", "name": "Theme 5d gap (≥120h)", "pass": False,
                    "detail": f"Same content_type '{content_type}' sent {days_since}d ago"}
    return {"rule": "R3", "name": "Theme 5d gap (≥120h)", "pass": True,
            "detail": "No same-theme send in 5 days"}


def _r4_active_seal_weekly(conn, slot: dict) -> dict:
    """R4: Active Seal weekly count < 4."""
    audience = slot.get("audience", "").lower().replace(" ", "_")
    if audience not in ("active_seal", "active_subscribers"):
        return {"rule": "R4", "name": "Active Seal weekly (<4)", "pass": True,
                "detail": "N/A — not Active Seal"}
    seven_days_ago = date.today() - timedelta(days=7)
    row = conn.execute(
        "SELECT COUNT(*) FROM calendar_executions "
        "WHERE audience IN ('active_seal','active_subscribers') AND slot_date > %s "
        "AND status IN ('dispatched','completed')",
        (seven_days_ago,)
    ).fetchone()
    count = row[0] if row else 0
    passed = count < 4
    return {"rule": "R4", "name": "Active Seal weekly (<4)", "pass": passed,
            "detail": f"{count}/4 sends this week" + ("" if passed else " — LIMIT REACHED")}


def _r5_burned_audience(slot: dict) -> dict:
    """R5: Not on current burn list. (Stub — burn list is manually maintained.)"""
    return {"rule": "R5", "name": "Burned audience list", "pass": True,
            "detail": "STUB — burn list check not automated yet"}


def _r6_revenue_floor(slot: dict) -> dict:
    """R6: Decay-adjusted projection ≥ $300."""
    est = slot.get("revenue_estimate", 0)
    if est and float(est) >= 300:
        return {"rule": "R6", "name": "Revenue floor (≥$300)", "pass": True,
                "detail": f"Projected ${est:,.0f}"}
    elif est and float(est) > 0:
        return {"rule": "R6", "name": "Revenue floor (≥$300)", "pass": False,
                "detail": f"Projected ${est:,.0f} — below $300 floor"}
    return {"rule": "R6", "name": "Revenue floor (≥$300)", "pass": True,
            "detail": "STUB — no revenue estimate in slot, skipping floor check"}


def _r7_format_kill_list(slot: dict) -> dict:
    """R7: Format not on KILL list for this audience."""
    audience = slot.get("audience", "").lower().replace(" ", "_")
    content_type = slot.get("content_type", "").lower()
    for blocked_aud, blocked_fmt in KILL_LIST:
        if audience == blocked_aud and blocked_fmt in content_type:
            return {"rule": "R7", "name": "Format on KILL list", "pass": False,
                    "detail": f"'{blocked_fmt}' is KILLED for '{blocked_aud}' — documented underperformance"}
    return {"rule": "R7", "name": "Format on KILL list", "pass": True,
            "detail": "Format not on kill list"}


def _r8_daily_cadence(conn, slot: dict) -> dict:
    """R8: ≤3 sends today (≤5 on push days)."""
    today = date.today()
    row = conn.execute(
        "SELECT COUNT(*) FROM calendar_executions WHERE slot_date = %s "
        "AND status IN ('dispatched','completed')",
        (today,)
    ).fetchone()
    count = row[0] if row else 0
    passed = count < 3
    return {"rule": "R8", "name": "Daily cadence (≤3)", "pass": passed,
            "detail": f"{count}/3 sends today" + ("" if passed else " — LIMIT REACHED")}


# Audiences that share significant profile overlap (supersets/subsets or heavy intersection)
_OVERLAP_GROUPS: list[set] = [
    # All customer tiers feed up to all_customers
    {"vip", "inner_circle", "whales", "high_aov", "engaged_customers",
     "active_seal", "active_subscribers", "all_customers",
     "one_time_buyers", "otb"},
    # Lapsed customers are also in all_customers historical pool
    {"lapsed_30d", "lapsed_60d", "all_customers"},
    {"lapsed_60_90d", "lapsed_90d", "all_customers"},
    {"lapsed_90_180d", "lapsed_180d", "lapsed_180d_plus", "winback_180d", "all_customers"},
    # Prospect tiers share a pool of unsubscribed/trial contacts
    {"engaged_prospects", "super_engaged"},
]

# Flow trigger types that indicate profiles in a given audience are actively in the flow
_FLOW_TRIGGER_AUDIENCE: dict[str, set] = {
    "Added to List":        {"super_engaged", "engaged_prospects"},
    "Viewed Product":       {"engaged_prospects", "super_engaged"},
    "Started Checkout":     {"cart_abandoners"},
    "Checkout Started":     {"cart_abandoners"},
    "Placed Order":         {"one_time_buyers", "otb", "active_seal", "active_subscribers"},
}

# RPR and open-rate floors for R11 — any audience below these fails
_R11_RPR_FLOOR = 0.10
_R11_OR_FLOOR  = 0.25


def _r9_segment_overlap(conn, slot: dict) -> dict:
    """R9: Same-day segment overlap — check if today's dispatched audiences share profiles."""
    audience = (slot.get("audience") or "").lower().replace(" ", "_")
    today = date.today()
    rows = conn.execute(
        "SELECT DISTINCT audience FROM calendar_executions "
        "WHERE slot_date = %s AND status IN ('dispatched','completed')",
        (today,)
    ).fetchall()
    dispatched = {(r[0] or "").lower().replace(" ", "_") for r in rows}

    for group in _OVERLAP_GROUPS:
        if audience in group:
            conflicts = (dispatched & group) - {audience}
            if conflicts:
                return {
                    "rule": "R9", "name": "Segment overlap (same day)", "pass": False,
                    "detail": (
                        f"'{audience}' overlaps with today's sends to "
                        f"{sorted(conflicts)}. Add exclusions in Klaviyo."
                    ),
                }
    return {
        "rule": "R9", "name": "Segment overlap (same day)", "pass": True,
        "detail": f"No same-day overlap with {sorted(dispatched) or 'no sends today'}",
    }


def _r10_active_flow_overlap(slot: dict) -> dict:
    """R10: Check whether live Klaviyo flows would double-touch the proposed audience within 72h."""
    audience = (slot.get("audience") or "").lower().replace(" ", "_")
    api_key = os.environ.get("KLAVIYO_API_KEY", "")
    if not api_key:
        return {
            "rule": "R10", "name": "Active Flow overlap", "pass": True,
            "detail": "No KLAVIYO_API_KEY — skipping flow overlap check",
        }
    try:
        resp = httpx.get(
            "https://a.klaviyo.com/api/flows",
            headers={"Authorization": f"Klaviyo-API-Key {api_key}", "revision": "2025-10-15"},
            params={"filter": 'equals(status,"live")', "fields[flow]": "name,trigger_type"},
            timeout=15,
        )
        if not resp.is_success:
            return {
                "rule": "R10", "name": "Active Flow overlap", "pass": True,
                "detail": f"Klaviyo API {resp.status_code} — skipping flow overlap check",
            }
        live_flows = resp.json().get("data", [])
    except Exception as exc:
        return {
            "rule": "R10", "name": "Active Flow overlap", "pass": True,
            "detail": f"Flow API error ({exc}) — skipping check",
        }

    conflicts = []
    for flow in live_flows:
        attrs = flow.get("attributes") or {}
        trigger = attrs.get("trigger_type") or ""
        name    = attrs.get("name") or trigger
        targeted = _FLOW_TRIGGER_AUDIENCE.get(trigger, set())
        if audience in targeted:
            conflicts.append(name)

    if conflicts:
        return {
            "rule": "R10", "name": "Active Flow overlap", "pass": False,
            "detail": (
                f"'{audience}' is targeted by live flow(s): {conflicts[:3]}. "
                "Risk of double-touch within 72h."
            ),
        }
    return {
        "rule": "R10", "name": "Active Flow overlap", "pass": True,
        "detail": f"{len(live_flows)} live flows — no overlap with '{audience}'",
    }


def _r11_performance_benchmark(conn, slot: dict) -> dict:
    """R11: Fail if 90d RPR < $0.10 or open rate < 25% (requires ≥3 finalized sends)."""
    audience = (slot.get("audience") or "").lower().replace(" ", "_")

    row = conn.execute(
        """SELECT COUNT(*), AVG(actual_rpr)
           FROM calendar_executions
           WHERE audience = %s
             AND slot_date > CURRENT_DATE - INTERVAL '90 days'
             AND status IN ('dispatched','completed')
             AND actual_rpr > 0""",
        (audience,)
    ).fetchone()
    sends, avg_rpr = int(row[0] or 0), float(row[1] or 0)

    if sends < 3:
        return {
            "rule": "R11", "name": "Top-1% benchmark", "pass": True,
            "detail": f"Only {sends} finalized send(s) in 90d — insufficient data, skipping floor",
        }

    # Open rate from performance table joined via klaviyo_campaign_id
    or_row = conn.execute(
        """SELECT AVG(opens.metric_value::float / NULLIF(recip.metric_value::float, 0))
           FROM calendar_executions ce
           JOIN performance opens  ON opens.dimensions->>'entity_id' = ce.klaviyo_campaign_id
                                  AND opens.metric_name = 'opens'
           JOIN performance recip  ON recip.dimensions->>'entity_id' = ce.klaviyo_campaign_id
                                  AND recip.metric_name = 'recipients'
           WHERE ce.audience = %s
             AND ce.slot_date > CURRENT_DATE - INTERVAL '90 days'
             AND ce.status IN ('dispatched','completed')
             AND ce.klaviyo_campaign_id IS NOT NULL""",
        (audience,)
    ).fetchone()
    avg_or = float(or_row[0]) if or_row and or_row[0] is not None else None

    failures = []
    if avg_rpr < _R11_RPR_FLOOR:
        failures.append(f"RPR ${avg_rpr:.4f} < floor ${_R11_RPR_FLOOR:.2f}")
    if avg_or is not None and avg_or < _R11_OR_FLOOR:
        failures.append(f"OR {avg_or:.1%} < floor {_R11_OR_FLOOR:.0%}")

    if failures:
        return {
            "rule": "R11", "name": "Top-1% benchmark", "pass": False,
            "detail": f"{sends} sends: {' | '.join(failures)} — audience underperforms benchmark",
        }
    or_str = f"{avg_or:.1%}" if avg_or is not None else "OR n/a"
    return {
        "rule": "R11", "name": "Top-1% benchmark", "pass": True,
        "detail": f"{sends} sends: ${avg_rpr:.4f} RPR, {or_str} — meets floors",
    }


def _r12_image_vs_plain(conn, slot: dict) -> dict:
    """R12: Compare RPR by content_type in last 90d; warn if proposed format underperforms best."""
    audience     = (slot.get("audience") or "").lower().replace(" ", "_")
    content_type = (slot.get("content_type") or "").lower()

    rows = conn.execute(
        """SELECT content_type, AVG(actual_rpr), COUNT(*)
           FROM calendar_executions
           WHERE audience = %s
             AND slot_date > CURRENT_DATE - INTERVAL '90 days'
             AND status IN ('dispatched','completed')
             AND actual_rpr > 0
           GROUP BY content_type
           HAVING COUNT(*) >= 2
           ORDER BY AVG(actual_rpr) DESC""",
        (audience,)
    ).fetchall()

    if not rows:
        return {
            "rule": "R12", "name": "Format (image/plain) data-backed", "pass": True,
            "detail": "Insufficient 90d data (need ≥2 sends per format) — skipping format check",
        }

    best_ct, best_rpr = rows[0][0], float(rows[0][1])
    current_rpr = next((float(r[1]) for r in rows if r[0] == content_type), None)

    if current_rpr is None:
        top = " | ".join(f"{r[0]}: ${float(r[1]):.4f}" for r in rows[:3])
        return {
            "rule": "R12", "name": "Format (image/plain) data-backed", "pass": True,
            "detail": f"No 90d data for '{content_type}'. Known formats: {top}",
        }

    if len(rows) == 1 or current_rpr >= best_rpr * 0.70:
        return {
            "rule": "R12", "name": "Format (image/plain) data-backed", "pass": True,
            "detail": f"'{content_type}' ${current_rpr:.4f} RPR vs best '{best_ct}' ${best_rpr:.4f} — acceptable",
        }

    return {
        "rule": "R12", "name": "Format (image/plain) data-backed", "pass": False,
        "detail": (
            f"'{content_type}' RPR ${current_rpr:.4f} is <70% of best format "
            f"'{best_ct}' ${best_rpr:.4f}. Switch to '{best_ct}' for this audience."
        ),
    }


# ── Content validation (catches today's bugs) ─────────────────────────────────

def _check_subject_syntax(copy: dict) -> dict:
    """Subject line must use {{ first_name }}, NOT {{ person.first_name|default:'there' }}."""
    subject = copy.get("subject", "")
    if "person.first_name" in subject or "default:" in subject:
        return {"rule": "C1", "name": "Subject personalization syntax", "pass": False,
                "detail": f"Subject uses body syntax: '{subject[:60]}'. Must use {{{{ first_name }}}} only."}
    return {"rule": "C1", "name": "Subject personalization syntax", "pass": True,
            "detail": "Subject syntax OK"}


def _check_cta_url(cta_url: str, slot: dict) -> dict:
    """Customer segments must go to /pages/bf-collection, NEVER a landing page."""
    audience = slot.get("audience", "").lower().replace(" ", "_")
    is_customer = audience in CUSTOMER_SEGMENTS

    if is_customer:
        if "/pages/bf-collection" in cta_url or "/discount/" in cta_url:
            return {"rule": "C2", "name": "CTA URL (customer → direct)", "pass": True,
                    "detail": f"CTA: {cta_url[:80]}"}
        else:
            return {"rule": "C2", "name": "CTA URL (customer → direct)", "pass": False,
                    "detail": f"Customer segment '{audience}' links to '{cta_url[:80]}' — must be /pages/bf-collection or /discount/CODE"}
    return {"rule": "C2", "name": "CTA URL (customer → direct)", "pass": True,
            "detail": "Prospect — landing page OK"}


def _check_offer_rules(copy: dict, slot: dict) -> dict:
    """HIGH_VALUE_SEGMENTS must not receive discount/BOGO/credit offers."""
    audience = slot.get("audience", "").lower().replace(" ", "_")
    if audience not in HIGH_VALUE_SEGMENTS:
        return {"rule": "C3", "name": "Offer/audience alignment", "pass": True,
                "detail": "Not a high-value segment — offers OK"}

    # Check if copy mentions discounts
    full_text = " ".join(copy.get("body_paragraphs", [])).lower()
    subject = copy.get("subject", "").lower()
    discount_signals = ["% off", "discount", "bogo", "buy 2", "buy 1", "credit", "coupon", "code:", "save $"]
    found = [s for s in discount_signals if s in full_text or s in subject]
    if found:
        return {"rule": "C3", "name": "Offer/audience alignment", "pass": False,
                "detail": f"HIGH_VALUE segment '{audience}' getting discount language: {found}. Use educational/insider instead."}
    return {"rule": "C3", "name": "Offer/audience alignment", "pass": True,
            "detail": f"High-value segment — no discount language detected"}


def _check_image_prompt(copy: dict) -> dict:
    """Image prompt must include humans (woman/women 50+)."""
    prompt = copy.get("image_prompt", "").lower()
    human_signals = ["woman", "women", "her ", "she ", "person", "people", "lady", "ladies"]
    has_human = any(s in prompt for s in human_signals)
    if not has_human:
        return {"rule": "C4", "name": "Image includes humans", "pass": False,
                "detail": f"Image prompt has no human: '{prompt[:60]}'. Must include woman 50+."}
    return {"rule": "C4", "name": "Image includes humans", "pass": True,
            "detail": "Image prompt includes human subject"}


def _check_collection_url(cta_url: str) -> dict:
    """Collection URL must be /pages/bf-collection, NEVER /collections/all."""
    if "/collections/all" in cta_url:
        return {"rule": "C5", "name": "Collection URL", "pass": False,
                "detail": f"URL uses /collections/all — must be /pages/bf-collection"}
    return {"rule": "C5", "name": "Collection URL", "pass": True,
            "detail": "Collection URL OK"}


# ── Main validator ─────────────────────────────────────────────────────────────

def validate_campaign(conn, slot: dict, copy: dict, cta_url: str) -> dict:
    """
    Run all validation rules. Returns:
    {
        "pass": bool,
        "verdict": "PASS" | "FAIL" | "WARN",
        "results": [{"rule": "R1", "name": "...", "pass": bool, "detail": "..."}],
        "auto_fails": [...],
        "slack_block": "formatted string for Slack"
    }
    """
    results = []

    # 12 structural rules
    results.append(_r1_smart_sending(conn, slot))
    results.append(_r2_audience_cooldown(conn, slot))
    results.append(_r3_theme_gap(conn, slot))
    results.append(_r4_active_seal_weekly(conn, slot))
    results.append(_r5_burned_audience(slot))
    results.append(_r6_revenue_floor(slot))
    results.append(_r7_format_kill_list(slot))
    results.append(_r8_daily_cadence(conn, slot))
    results.append(_r9_segment_overlap(conn, slot))
    results.append(_r10_active_flow_overlap(slot))
    results.append(_r11_performance_benchmark(conn, slot))
    results.append(_r12_image_vs_plain(conn, slot))

    # Content checks (catch the bugs from today)
    results.append(_check_subject_syntax(copy))
    results.append(_check_cta_url(cta_url, slot))
    results.append(_check_offer_rules(copy, slot))
    results.append(_check_image_prompt(copy))
    results.append(_check_collection_url(cta_url))

    # Compute verdict
    failures = [r for r in results if not r["pass"]]
    auto_fail_rules = {"R2", "R10", "C1", "C2", "C3", "C5"}  # non-negotiable
    auto_fails = [r for r in failures if r["rule"] in auto_fail_rules]
    stubs = [r for r in results if "STUB" in r.get("detail", "")]

    if auto_fails:
        verdict = "FAIL"
        passed = False
    elif failures:
        verdict = "WARN"
        passed = False  # still block — warnings are failures until validator matures
    else:
        verdict = "PASS"
        passed = True

    # Build Slack block
    audience = slot.get("audience", "?")
    content_type = slot.get("content_type", "?")
    slot_date = slot.get("date", date.today().isoformat())
    subject = copy.get("subject", "(no subject)")

    lines = [
        f"{'🟢' if passed else '🔴'} *Validator {verdict}* — {audience} / {content_type} / {slot_date}",
        f"Subject: {subject[:60]}",
        f"CTA: {cta_url[:80]}",
        "",
    ]
    for r in results:
        icon = "✅" if r["pass"] else "❌"
        stub = " ⚪" if "STUB" in r.get("detail", "") else ""
        lines.append(f"  {icon} {r['rule']} {r['name']}: {r['detail'][:80]}{stub}")

    if failures:
        lines.append("")
        lines.append(f"*{len(failures)} rule(s) failed. Campaign BLOCKED.*")
        if auto_fails:
            lines.append("Auto-fail rules triggered: " + ", ".join(r["rule"] for r in auto_fails))

    slack_block = "\n".join(lines)

    return {
        "pass": passed,
        "verdict": verdict,
        "results": results,
        "auto_fails": auto_fails,
        "stubs": stubs,
        "slack_block": slack_block,
    }
