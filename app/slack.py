"""Slack webhook handlers.

Two surfaces:
  - POST /slack/events       — Slack Events API (URL verification, mentions)
  - POST /slack/interactions — Block Kit button clicks for Tier-2 approvals and retro decisions
"""

from fastapi import APIRouter, Request

router = APIRouter(prefix="/slack", tags=["slack"])


@router.post("/events")
async def slack_events(request: Request):
    raise NotImplementedError(
        "Slack Events handler: verify signing secret, handle url_verification "
        "challenge, route message events (e.g. mentions of the bot)."
    )


@router.post("/interactions")
async def slack_interactions(request: Request):
    raise NotImplementedError(
        "Slack interactions handler: parse Block Kit payload, verify signing "
        "secret, dispatch Approve/Reject for Tier-2 task approvals and for "
        "weekly retro strategy updates. On approval, flip the appropriate "
        "`tasks.approval_status` or `strategies.is_active` row."
    )
