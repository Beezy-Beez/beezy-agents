"""Slack helpers — failure alerts and long-form content drafts.

Reads SLACK_WEBHOOK_URL from environment. Graceful no-op if unset.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Optional

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
_SECTION_CHAR_LIMIT = 2800  # Slack section blocks cap at 3000; leave headroom.
_webhook_down = False  # suppress repeated DNS-failure spam


def _post(payload: dict[str, Any]) -> bool:
    global _webhook_down
    if not SLACK_WEBHOOK_URL:
        return False
    try:
        req = urllib.request.Request(
            SLACK_WEBHOOK_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = 200 <= resp.status < 300
            if not ok:
                print(f"[slack] non-2xx status: {resp.status}")
            elif _webhook_down:
                print("[slack] Webhook connection restored.")
                _webhook_down = False
            return ok
    except OSError as e:
        # OSError covers socket.gaierror ([Errno -2]) and connection refused.
        # Log once; stay silent on every subsequent failure until it recovers.
        if not _webhook_down:
            print(f"[slack] Webhook unreachable: {e}. Suppressing further errors.")
            _webhook_down = True
        return False
    except Exception as e:
        print(f"[slack] post failed: {e}")
        return False


def notify_failure(
    source: str,
    error: str,
    *,
    context: Optional[dict[str, Any]] = None,
) -> bool:
    """Post a failure alert to Slack."""
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"❌ {source} failed"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"```{error[:_SECTION_CHAR_LIMIT]}```"}},
    ]
    if context:
        ctx_text = "\n".join(f"*{k}:* {v}" for k, v in context.items())
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": ctx_text[:_SECTION_CHAR_LIMIT]}})
    return _post({"blocks": blocks})


def post_draft(
    title: str,
    summary_lines: list[str],
    body: str,
    *,
    metadata: Optional[dict[str, Any]] = None,
    image_url: Optional[str] = None,
    image_alt: str = "Cover image",
) -> bool:
    """Post a long-form draft (newsletter issue, blog, etc.) to Slack for review.

    Chunks the body into multiple section blocks to handle Slack's 3000-char limit.
    Includes an image block if image_url is provided.
    """
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📝 {title}"}},
    ]

    if image_url:
        blocks.append({
            "type": "image",
            "image_url": image_url,
            "alt_text": image_alt[:1990],
        })

    if summary_lines:
        summary_text = "\n".join(f"• {line}" for line in summary_lines)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": summary_text[:_SECTION_CHAR_LIMIT]}})
    if metadata:
        meta_text = "\n".join(f"*{k}:* {v}" for k, v in metadata.items() if v is not None)
        if meta_text:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": meta_text[:_SECTION_CHAR_LIMIT]}})
    blocks.append({"type": "divider"})

    body_chunks = [body[i:i + _SECTION_CHAR_LIMIT] for i in range(0, len(body), _SECTION_CHAR_LIMIT)]
    for chunk in body_chunks[:40]:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})

    return _post({"blocks": blocks})
