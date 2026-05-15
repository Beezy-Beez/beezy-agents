"""Global dry-run switch for the daily pipeline.

When BEEZY_DRY_RUN=1 the pipeline runs end-to-end with REAL copy generation
and REAL validator checks, but performs NO outward side effects:
  - no Klaviyo template/campaign/discount/list creation
  - no Shopify page/image writes
  - no calendar_executions rows written (no phantom rows)
  - Slack payloads are printed to stdout instead of posted, UNLESS
    BEEZY_DRY_RUN_POST_SLACK=1 (then they post for real, prefixed [DRY RUN]).

Set via scripts/dry_run_pipeline.py — never in normal deployment.
"""
from __future__ import annotations

import os


def is_dry_run() -> bool:
    return os.environ.get("BEEZY_DRY_RUN") == "1"


def post_slack_in_dry_run() -> bool:
    return os.environ.get("BEEZY_DRY_RUN_POST_SLACK") == "1"


def dry_banner() -> str:
    return "🧪 *[DRY RUN — no real sends]* " if is_dry_run() else ""
