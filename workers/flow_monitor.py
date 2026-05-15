"""
Flow Performance Monitor — weekly health check on all live Klaviyo flows.

Runs weekly (Sunday 9:15pm, after the campaign learning review).
Pulls 30-day performance for every live flow.
Flags: zero-revenue flows, underperforming welcome series, broken triggers,
high-engagement-no-conversion anomalies.
Posts actionable recommendations to Slack.

Also exports `fix_flow(analysis)`:
  For zero-revenue flows with >50 recipients, generates new email copy via the
  Anthropic API, creates a new Klaviyo template, and posts to Slack with a
  one-click "Apply Fix" button. The button triggers POST /api/slack/interactive
  which applies the template to the flow message.

Usage:
    from workers.flow_monitor import run_flow_check, fix_flow
    run_flow_check()
    fix_flow(analysis_dict)
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

import httpx
from config import KLAVIYO_REVISION

CONVERSION_METRIC_ID = "X93gjq"

# RPR benchmarks by flow type (minimum acceptable)
FLOW_BENCHMARKS = {
    "welcome":           {"min_rpr": 1.50, "min_or": 0.40},
    "abandoned_checkout": {"min_rpr": 2.00, "min_or": 0.35},
    "abandoned_cart":    {"min_rpr": 1.00, "min_or": 0.35},
    "browse_abandonment": {"min_rpr": 0.50, "min_or": 0.30},
    "replenishment":     {"min_rpr": 0.50, "min_or": 0.30},
    "winback":           {"min_rpr": 0.20, "min_or": 0.25},
    "post_purchase":     {"min_rpr": 0.50, "min_or": 0.30},
    "membership":        {"min_rpr": 0.50, "min_or": 0.25},
    "default":           {"min_rpr": 0.10, "min_or": 0.20},
}

# Map flow IDs to types for benchmarking
FLOW_TYPE_MAP = {
    "RByGDp": "welcome",           # First-Time Buyer Welcome Series
    "UU8eEK": "abandoned_checkout", # Abandoned Checkout Flow
    "SXhgap": "abandoned_checkout", # Abandoned Checkout - SMS Only
    "RM265B": "abandoned_cart",     # Abandoned Cart Reminder
    "W8AarU": "browse_abandonment", # Browse Abandonment
    "RUzx4x": "replenishment",     # 1→2 replenishment flow
    "WLY4yj": "replenishment",     # Repeat Customers (2 → 3 Orders)
    "SHX3Ss": "winback",           # Winback
    "SmECWv": "winback",           # Lapsed Customer Check-In
    "RRMe5p": "post_purchase",     # Delayed Shipment
    "XLT2F6": "membership",        # Beehive Club
    "S97LdZ": "membership",        # Started Subscription (Hive Club)
    "UvZWwJ": "membership",        # Subscription Upgrade Flow
}


def _klaviyo_headers() -> dict:
    return {
        "Authorization": "Klaviyo-API-Key " + os.environ.get("KLAVIYO_API_KEY", ""),
        "revision": KLAVIYO_REVISION,
        "Content-Type": "application/json",
    }


def _get_live_flows() -> list[dict]:
    """Pull all live flows from Klaviyo."""
    resp = httpx.get(
        "https://a.klaviyo.com/api/flows",
        headers=_klaviyo_headers(),
        params={
            "filter": 'equals(status,"live")',
            "fields[flow]": "name,status,trigger_type",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"[flow_monitor] Failed to get flows: {resp.status_code}")
        return []
    return resp.json().get("data", [])


def _get_flow_performance() -> list[dict]:
    """Pull 30-day flow performance from Klaviyo reporting API.

    Returns a list of dicts with keys: flow_id, statistics (dict).
    Aggregates per-message rows up to the flow level.
    """
    url = "https://a.klaviyo.com/api/flow-values-reports/"
    payload = {
        "data": {
            "type": "flow-values-report",
            "attributes": {
                "statistics": ["recipients", "open_rate", "click_rate", "conversion_rate", "conversion_value"],
                "timeframe": {"key": "last_30_days"},
                "conversion_metric_id": CONVERSION_METRIC_ID,
                "group_by": ["flow_id", "flow_message_id"],
            }
        }
    }
    resp = httpx.post(url, headers=_klaviyo_headers(), json=payload, timeout=30)
    if resp.status_code != 200:
        print(f"[flow_monitor] Failed to get flow report: {resp.status_code}")
        return []

    rows = resp.json().get("data", {}).get("attributes", {}).get("results", [])

    # Aggregate per-message rows up to flow level
    by_flow: dict = {}
    for row in rows:
        flow_id = row.get("groupings", {}).get("flow_id", "")
        if not flow_id:
            continue
        stats = row.get("statistics", {})
        if flow_id not in by_flow:
            by_flow[flow_id] = {"recipients": 0.0, "conversion_value": 0.0,
                                "open_rate_sum": 0.0, "click_rate_sum": 0.0,
                                "conversion_rate_sum": 0.0, "msg_count": 0}
        e = by_flow[flow_id]
        e["recipients"] += float(stats.get("recipients", 0))
        e["conversion_value"] += float(stats.get("conversion_value", 0))
        e["open_rate_sum"] += float(stats.get("open_rate", 0))
        e["click_rate_sum"] += float(stats.get("click_rate", 0))
        e["conversion_rate_sum"] += float(stats.get("conversion_rate", 0))
        e["msg_count"] += 1

    result = []
    for flow_id, agg in by_flow.items():
        mc = max(agg["msg_count"], 1)
        rec = agg["recipients"]
        result.append({
            "flow_id": flow_id,
            "statistics": {
                "recipients": rec,
                "conversion_value": agg["conversion_value"],
                "revenue_per_recipient": agg["conversion_value"] / rec if rec > 0 else 0.0,
                "open_rate": agg["open_rate_sum"] / mc,
                "click_rate": agg["click_rate_sum"] / mc,
                "conversion_rate": agg["conversion_rate_sum"] / mc,
            },
        })
    return result


def _classify_flow(flow_id: str) -> str:
    """Get the flow type for benchmarking."""
    return FLOW_TYPE_MAP.get(flow_id, "default")


def _analyze_flow(flow_data: dict) -> dict:
    """Analyze a single flow against benchmarks. Returns analysis dict."""
    flow_id = flow_data.get("flow_id", "")
    stats = flow_data.get("statistics", {})
    name = flow_data.get("name", flow_id)

    flow_type = _classify_flow(flow_id)
    benchmarks = FLOW_BENCHMARKS.get(flow_type, FLOW_BENCHMARKS["default"])

    revenue = float(stats.get("conversion_value", 0))
    recipients = int(stats.get("recipients", 0))
    rpr = float(stats.get("revenue_per_recipient", 0))
    open_rate = float(stats.get("open_rate", 0))
    click_rate = float(stats.get("click_rate", 0))
    conversion_rate = float(stats.get("conversion_rate", 0))

    issues = []
    severity = "ok"  # ok, warn, critical

    # Check: zero revenue
    if recipients > 20 and revenue == 0:
        issues.append("ZERO revenue on " + str(recipients) + " recipients — likely broken CTA or missing product links")
        severity = "critical"

    # Check: very low volume (might be broken trigger)
    if recipients < 10:
        issues.append("Only " + str(recipients) + " recipients in 30d — trigger may be broken or too restrictive")
        severity = max(severity, "warn", key=["ok", "warn", "critical"].index)

    # Check: RPR below benchmark
    if recipients > 20 and rpr < benchmarks["min_rpr"]:
        issues.append(f"RPR ${rpr:.2f} below {flow_type} benchmark ${benchmarks['min_rpr']:.2f}")
        severity = max(severity, "warn", key=["ok", "warn", "critical"].index)

    # Check: open rate below benchmark
    if recipients > 20 and open_rate < benchmarks["min_or"]:
        issues.append(f"Open rate {open_rate:.1%} below benchmark {benchmarks['min_or']:.0%}")
        severity = max(severity, "warn", key=["ok", "warn", "critical"].index)

    # Check: high engagement, no conversion
    if recipients > 50 and open_rate > 0.40 and conversion_rate == 0:
        issues.append("High opens (" + f"{open_rate:.0%}" + ") but ZERO conversions — CTA or offer problem")
        severity = "critical"

    # Check: high clicks, no conversion
    if recipients > 50 and click_rate > 0.05 and conversion_rate < 0.005:
        issues.append("Good clicks (" + f"{click_rate:.1%}" + ") but near-zero conversion — landing page or checkout issue")
        severity = max(severity, "warn", key=["ok", "warn", "critical"].index)

    return {
        "flow_id": flow_id,
        "name": name,
        "flow_type": flow_type,
        "revenue": revenue,
        "recipients": recipients,
        "rpr": rpr,
        "open_rate": open_rate,
        "click_rate": click_rate,
        "conversion_rate": conversion_rate,
        "issues": issues,
        "severity": severity,
    }


def run_flow_check() -> str:
    """
    Main entry point. Pull all live flows, analyze performance,
    flag issues, post to Slack.
    """
    from lib.slack import post_draft

    print("[flow_monitor] Running weekly flow health check...")

    # Pull live flow names
    live_flows = _get_live_flows()
    flow_names = {f["id"]: f.get("attributes", {}).get("name", f["id"]) for f in live_flows}

    # Pull performance data
    flow_perf = _get_flow_performance()
    if not flow_perf:
        return "no_data"

    # Merge names into performance rows
    for fp in flow_perf:
        fp["name"] = flow_names.get(fp["flow_id"], fp["flow_id"])

    # Analyze each flow
    analyses = [_analyze_flow(f) for f in flow_perf]

    # Sort: critical first, then by revenue descending
    severity_order = {"critical": 0, "warn": 1, "ok": 2}
    analyses.sort(key=lambda a: (severity_order.get(a["severity"], 3), -a["revenue"]))

    # Build Slack report
    total_revenue = sum(a["revenue"] for a in analyses)
    critical = [a for a in analyses if a["severity"] == "critical"]
    warns = [a for a in analyses if a["severity"] == "warn"]
    healthy = [a for a in analyses if a["severity"] == "ok"]

    lines = [
        "*Weekly Flow Health Check*",
        "",
        f"💰 *Total flow revenue (30d):* ${total_revenue:,.0f}",
        f"🔴 Critical: {len(critical)}  🟡 Warning: {len(warns)}  🟢 Healthy: {len(healthy)}",
        "",
    ]

    if critical:
        lines.append("*🔴 CRITICAL — needs immediate attention:*")
        for a in critical:
            lines.append(f"  *{a['name']}* — ${a['revenue']:,.0f} rev, {a['recipients']} recip, ${a['rpr']:.2f} RPR")
            for issue in a["issues"]:
                lines.append(f"    → {issue}")
        lines.append("")

    if warns:
        lines.append("*🟡 WARNING — underperforming:*")
        for a in warns:
            lines.append(f"  *{a['name']}* — ${a['revenue']:,.0f} rev, ${a['rpr']:.2f} RPR")
            for issue in a["issues"]:
                lines.append(f"    → {issue}")
        lines.append("")

    if healthy:
        lines.append("*🟢 HEALTHY:*")
        for a in healthy[:5]:  # top 5 only
            lines.append(f"  {a['name']} — ${a['revenue']:,.0f} rev, ${a['rpr']:.2f} RPR, {a['open_rate']:.0%} OR")

    # Recommendations
    lines.append("")
    lines.append("*Recommendations:*")
    if any(a["name"] == "First-Time Buyer Welcome Series" and a["severity"] != "ok" for a in analyses):
        lines.append("  → Welcome Series is underperforming — rewrite first email with stronger offer + product education")
    if any(a["revenue"] == 0 and a["recipients"] > 50 for a in analyses):
        lines.append("  → One or more flows have ZERO revenue — check CTAs, product links, and offer copy")
    if not critical and not warns:
        lines.append("  → All flows healthy. No action needed.")

    report = "\n".join(lines)

    post_draft(
        title="🔄 Weekly Flow Health — " + date.today().strftime("%b %d, %Y"),
        summary_lines=[report],
        body="",
    )

    # Store in DB for learning loop
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO strategies (component, strategy_text, approved_by, is_active, created_at)
                   VALUES ('flow_monitor', %s, 'system', true, NOW())""",
                (json.dumps({
                    "type": "flow_health_check",
                    "date": date.today().isoformat(),
                    "total_revenue_30d": total_revenue,
                    "critical_count": len(critical),
                    "warn_count": len(warns),
                    "analyses": analyses,
                }),)
            )
            conn.commit()
    except Exception as e:
        print(f"[flow_monitor] DB save failed: {e}")

    # Auto-fix: for zero-revenue critical flows with >50 recipients, generate copy + Slack button
    for a in critical:
        if a.get("revenue") == 0 and a.get("recipients", 0) > 50:
            try:
                fix_flow(a)
            except Exception as fe:
                print(f"[flow_monitor] fix_flow failed for {a.get('name')}: {fe}")

    print(f"[flow_monitor] Done. {len(critical)} critical, {len(warns)} warnings.")
    return report


# ── Flow fix: generate copy → create template → post Slack approval ───────────

_FIX_SYSTEM = (
    "You are a high-converting email copywriter for Beezy Beez Honey "
    "(trybeezybeez.com), a DTC CBN/CBD honey brand for women 50+ seeking better sleep. "
    "Brand voice: warm, science-backed, empowering. "
    "You are rewriting a triggered flow email that has ZERO revenue despite real recipients. "
    "The goal: fix the subject line, preview, and body so it drives click-throughs to buy. "
    "Rules: compelling subject (≤60 chars), emotional preview (≤90 chars), "
    "2–3 short body paragraphs, clear CTA button text (4–6 words). "
    "Output ONLY valid JSON. Schema: "
    '{"subject":"...","preview_text":"...","body_html":"<p>...</p>","cta_text":"..."}'
)


def _generate_fix_copy(flow_name: str, flow_type: str) -> dict:
    """Call Anthropic to generate replacement email copy for a zero-revenue flow."""
    import anthropic as _anthropic

    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("BEEZY_ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    client = _anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=_FIX_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Flow name: {flow_name}\n"
                f"Flow type: {flow_type}\n"
                "Problem: zero revenue despite real recipients. "
                "Rewrite the email to drive purchases of Beezy Beez Honey CBN sleep blend."
            ),
        }],
    )
    raw = msg.content[0].text.strip()
    s, e = raw.find("{"), raw.rfind("}")
    return json.loads(raw[s : e + 1] if s != -1 else raw)


def _build_flow_email_html(copy: dict, flow_name: str) -> str:
    """Wrap generated copy in a minimal branded HTML template."""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{copy.get('subject','')}</title>
</head>
<body style="margin:0;padding:0;background:#fffdf7;font-family:Georgia,serif;">
<div style="max-width:600px;margin:0 auto;padding:32px 24px;">
  <p style="font-size:13px;color:#8b6914;text-align:center;margin-bottom:24px;">
    Beezy Beez Honey · CBN Sleep Blend
  </p>
  <div style="background:#fff;border-radius:8px;padding:32px;border:1px solid #f0e8d0;">
    {copy.get('body_html','')}
    <div style="text-align:center;margin-top:32px;">
      <a href="https://trybeezybeez.com/pages/bf-collection"
         style="background:#8b4513;color:#fff;padding:14px 32px;border-radius:6px;
                text-decoration:none;font-size:16px;font-weight:bold;display:inline-block;">
        {copy.get('cta_text','Shop Now')}
      </a>
    </div>
  </div>
  <p style="font-size:11px;color:#999;text-align:center;margin-top:24px;">
    © Beezy Beez Honey · {{{{ organization.address }}}}
    · {{% unsubscribe 'Unsubscribe' %}}
  </p>
</div></body></html>"""


def _create_klaviyo_template(name: str, html: str) -> str:
    """Create a new Klaviyo email template. Returns template_id."""
    api_key = os.environ.get("KLAVIYO_API_KEY", "")
    resp = httpx.post(
        "https://a.klaviyo.com/api/templates/",
        headers=_klaviyo_headers(),
        json={"data": {
            "type": "template",
            "attributes": {"name": name, "html": html, "editor_type": "CODE"},
        }},
        timeout=30,
    )
    if not resp.is_success:
        raise RuntimeError(f"Klaviyo create template {resp.status_code}: {resp.text[:300]}")
    return resp.json()["data"]["id"]


def _get_flow_message_ids(flow_id: str) -> list[str]:
    """Return all flow message IDs for a flow (for apply-fix routing)."""
    resp = httpx.get(
        f"https://a.klaviyo.com/api/flows/{flow_id}/flow-messages/",
        headers=_klaviyo_headers(),
        params={"fields[flow-message]": "id"},
        timeout=20,
    )
    if not resp.is_success:
        return []
    return [item["id"] for item in resp.json().get("data", [])]


def fix_flow(analysis: dict) -> dict:
    """
    For a zero-revenue flow with >50 recipients:
      1. Generate replacement email copy via Anthropic.
      2. Create a new Klaviyo template with the copy.
      3. Post a Slack message with an "Apply Fix" button.

    The Slack button value encodes `template_id:flow_id` so the interactive
    endpoint at POST /api/slack/interactive can apply it on click.

    Returns {template_id, flow_id, skipped: bool}.
    """
    from lib.slack import _post as slack_post

    flow_id    = analysis.get("flow_id", "")
    flow_name  = analysis.get("name", flow_id)
    flow_type  = analysis.get("flow_type", "default")
    recipients = analysis.get("recipients", 0)
    revenue    = analysis.get("revenue", 0)

    if not (recipients > 50 and revenue == 0):
        return {"skipped": True, "reason": "Criteria not met (need >50 recip + $0 revenue)"}

    print(f"[fix_flow] Generating fix copy for: {flow_name}")
    copy = _generate_fix_copy(flow_name, flow_type)

    html         = _build_flow_email_html(copy, flow_name)
    template_name = f"[AUTO-FIX] {flow_name[:60]} — {date.today().isoformat()}"
    template_id  = _create_klaviyo_template(template_name, html)
    print(f"[fix_flow]   Created template: {template_id}")

    button_value = f"{template_id}:{flow_id}"
    admin_url    = f"https://www.klaviyo.com/flow/{flow_id}/edit"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🔧 Flow Fix Ready for Review"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Flow:* {flow_name}\n"
                    f"*Problem:* {recipients} recipients, $0 revenue in 30d\n"
                    f"*Fix:* New template created — `{template_id}`\n\n"
                    f"*New subject:* {copy.get('subject','')}\n"
                    f"*Preview:* {copy.get('preview_text','')}"
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Apply Fix to Flow"},
                    "style": "primary",
                    "action_id": "apply_flow_fix",
                    "value": button_value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🔍 View Flow"},
                    "url": admin_url,
                    "action_id": "view_flow",
                    "value": flow_id,
                },
            ],
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn",
                          "text": f"Template ID: `{template_id}` · Flow: `{flow_id}`"}],
        },
    ]

    slack_post({"blocks": blocks})
    print(f"[fix_flow]   Posted Slack approval for {flow_name}")

    return {
        "template_id": template_id,
        "flow_id":     flow_id,
        "flow_name":   flow_name,
        "subject":     copy.get("subject", ""),
        "skipped":     False,
    }
