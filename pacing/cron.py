"""Pacing cron — Phase 2A daily snapshot.

Workflow:
  1. For each active goal: compute_pacing_state(), insert a `pacing_state` row.
  2. Pull top-5 campaigns + top-5 flows from the last 7 days.
  3. Build a Block Kit Slack message summarizing the goal(s) + contributors.
  4. POST to SLACK_WEBHOOK_URL (skipped with --dry-run or when the webhook is unset).

This is intentionally LLM-free. Phase 2B will graft an Opus-driven priority
decision on top of the same data.

CLI:
    python -m pacing.cron daily            # compute + write + post
    python -m pacing.cron daily --dry-run  # compute + write, print Slack JSON
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import requests

import config
from db.connection import get_conn
from pacing.brain import (
    Contributor,
    Goal,
    PacingState,
    active_goals,
    compute_pacing_state,
    top_contributors,
)

logger = logging.getLogger(__name__)

STATUS_EMOJI = {
    "ahead": ":large_green_circle:",
    "on-track": ":large_yellow_circle:",
    "behind": ":red_circle:",
}
# Slack legacy `attachments[].color` hex stripe — green/yellow/red gives a
# fast visual cue without needing custom Block Kit color blocks.
STATUS_COLOR = {
    "ahead": "#2EB67D",
    "on-track": "#ECB22E",
    "behind": "#E01E5F",
}


def _money(value: Decimal) -> str:
    return f"${value:,.2f}"


def _insert_pacing_state(conn, state: PacingState) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into pacing_state
              (goal_id, measured_at, period_to_date_value, target_to_date_value,
               gap_pct, days_remaining, required_daily_rate)
            values
              (%s, %s, %s, %s, %s, %s, %s)
            returning id
            """,
            (
                state.goal_id,
                state.as_of,
                state.period_to_date_value,
                state.target_to_date_value,
                state.gap_pct,
                state.days_remaining,
                state.required_daily_rate,
            ),
        )
        return str(cur.fetchone()[0])


def _contributor_line(c: Contributor, idx: int) -> str:
    channel = f" ({c.send_channel})" if c.send_channel else ""
    return f"{idx}. *{c.entity_name or c.entity_id}*{channel} — {_money(c.conversion_value)}"


def build_slack_message(
    *,
    goal: Goal,
    state: PacingState,
    contributors: dict[str, list[Contributor]],
) -> dict[str, Any]:
    """Build a Slack webhook payload (Block Kit blocks inside a colored attachment)."""
    header_emoji = STATUS_EMOJI.get(state.status, ":white_circle:")
    period = f"{goal.period_start.isoformat()} → {goal.period_end.isoformat()}"

    fields = [
        {"type": "mrkdwn", "text": f"*Target*\n{_money(goal.target_value)}"},
        {"type": "mrkdwn", "text": f"*Period-to-date*\n{_money(state.period_to_date_value)}"},
        {"type": "mrkdwn", "text": f"*Target-to-date* (linear)\n{_money(state.target_to_date_value)}"},
        {"type": "mrkdwn", "text": f"*Gap*\n{state.gap_pct:+.2f}%"},
        {"type": "mrkdwn", "text": f"*Days*\n{state.days_elapsed}/{state.total_days} elapsed · {state.days_remaining} remaining"},
        {"type": "mrkdwn", "text": f"*Required daily rate*\n{_money(state.required_daily_rate)}"},
    ]

    campaign_lines = (
        [_contributor_line(c, i + 1) for i, c in enumerate(contributors["campaigns"])]
        or ["_no campaigns in the last 7 days_"]
    )
    flow_lines = (
        [_contributor_line(c, i + 1) for i, c in enumerate(contributors["flows"])]
        or ["_no flows in the last 7 days_"]
    )

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Pacing — {goal.title}"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{header_emoji} *{state.status.upper()}*  ·  {period}",
            },
        },
        {"type": "section", "fields": fields},
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Top campaigns (last 7d, by conversion value)*\n"
                + "\n".join(campaign_lines),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Top flows (last 7d, by conversion value)*\n" + "\n".join(flow_lines),
            },
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"as_of `{state.as_of.isoformat()}`"},
            ],
        },
    ]

    return {
        "attachments": [
            {
                "color": STATUS_COLOR.get(state.status, "#888888"),
                "blocks": blocks,
            }
        ]
    }


def _post_slack(payload: dict[str, Any]) -> None:
    if not config.SLACK_WEBHOOK_URL:
        logger.warning("SLACK_WEBHOOK_URL not set — skipping pacing digest POST")
        return
    try:
        resp = requests.post(config.SLACK_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code >= 300:
            logger.error("Slack webhook returned %d: %s", resp.status_code, resp.text[:200])
        else:
            logger.info("Posted pacing digest to Slack")
    except Exception:
        logger.exception("Failed to POST pacing digest to Slack")


def run_daily_pacing(*, dry_run: bool = False) -> list[dict[str, Any]]:
    """For each active goal: compute state, persist, and post (or print) a Slack digest.

    Returns a list of {goal_id, pacing_state_row_id, payload, state} dicts, one per goal.
    """
    as_of = datetime.now(timezone.utc).replace(microsecond=0)
    contributors = top_contributors(days=7)

    goals = active_goals()
    if not goals:
        logger.warning("No active goals — nothing to compute")
        return []

    results: list[dict[str, Any]] = []
    for goal in goals:
        state = compute_pacing_state(goal.id, as_of=as_of)

        with get_conn() as conn:
            with conn.transaction():
                row_id = _insert_pacing_state(conn, state)

        payload = build_slack_message(goal=goal, state=state, contributors=contributors)

        if dry_run:
            logger.info("DRY RUN — would POST to Slack for goal %s", goal.title)
        else:
            _post_slack(payload)

        results.append({
            "goal_id": goal.id,
            "goal_title": goal.title,
            "pacing_state_row_id": row_id,
            "payload": payload,
            "state": state,
            "contributors": contributors,
        })
    return results


def _json_default(o: Any) -> Any:
    """JSON serializer for Decimal + datetime + dataclasses — printing only."""
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"unserializable: {type(o)}")


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="python -m pacing.cron")
    parser.add_argument("command", choices=["daily"])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute + write pacing_state, print payload, skip Slack POST",
    )
    args = parser.parse_args(argv[1:])

    if args.command == "daily":
        results = run_daily_pacing(dry_run=args.dry_run)
        for r in results:
            print(json.dumps(
                {
                    "goal_id": r["goal_id"],
                    "goal_title": r["goal_title"],
                    "pacing_state_row_id": r["pacing_state_row_id"],
                    "payload": r["payload"],
                },
                indent=2,
                default=_json_default,
            ))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
