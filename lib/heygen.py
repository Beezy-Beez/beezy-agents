"""
lib/heygen.py
HeyGen API wrapper for personalized welcome videos.

Single responsibility: take a first name, return a video URL.
The {first_name} substitution happens here, before the API call.
"""

import os
import time
import requests
from typing import Optional


# === Config ===

HEYGEN_API_BASE = "https://api.heygen.com"


def _heygen_api_key() -> str:
    return os.environ["HEYGEN_API_KEY"]


def _heygen_avatar_id() -> str:
    return os.environ["HEYGEN_AVATAR_ID"]


def _heygen_voice_id() -> str:
    return os.environ["HEYGEN_VOICE_ID"]

# The welcome script. {first_name} gets replaced per customer.
WELCOME_SCRIPT = (
    "Hey {first_name} — I'm Alan, from Beezy Beez, and I just saw your order come in. "
    "I wanted to jump on real quick and personally say thank you. "
    "This isn't a big corporation. It's me, a small team, and a whole lot of obsession over getting your sleep right. "
    "You made a good choice. Give it a few nights, be consistent, and pay attention to how you feel in the morning. "
    "We're here if you need anything — reply to any email and you'll get a real human. "
    "Welcome to the hive. We're glad you're here."
)


# === Errors ===

class HeyGenError(Exception):
    """HeyGen API returned an error."""
    pass


# === Internals ===

def _headers() -> dict:
    return {
        "X-Api-Key": os.environ.get("HEYGEN_API_KEY"),
        "Content-Type": "application/json",
    }


def _build_script(first_name: str) -> str:
    """Substitute the customer's first name into the welcome script."""
    safe_name = first_name.strip().title()  # 'sarah' -> 'Sarah'
    return WELCOME_SCRIPT.format(first_name=safe_name)


# === Public API ===

def submit_video(first_name: str) -> str:
    """
    Submit a personalized video render to HeyGen.
    Returns HeyGen's video_id (use poll_until_ready to get final URL).
    """
    script = _build_script(first_name)

    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": os.environ.get("HEYGEN_AVATAR_ID"),
                    "avatar_style": "normal",
                },
                "voice": {
                    "type": "text",
                    "input_text": script,
                    "voice_id": os.environ.get("HEYGEN_VOICE_ID"),
                    "speed": 1.0,
                },
            }
        ],
        "dimension": {"width": 1080, "height": 1920},
    }

    resp = requests.post(
        f"{HEYGEN_API_BASE}/v2/video/generate",
        json=payload,
        headers=_headers(),
        timeout=30,
    )

    if resp.status_code != 200:
        raise HeyGenError(f"submit_video failed: {resp.status_code} {resp.text}")

    data = resp.json()
    video_id = data.get("data", {}).get("video_id")
    if not video_id:
        raise HeyGenError(f"submit_video returned no video_id: {data}")

    return video_id


def check_status(video_id: str) -> dict:
    """One-shot status check, no polling. Returns dict with status, video_url, error."""
    resp = requests.get(
        f"{HEYGEN_API_BASE}/v1/video_status.get",
        params={"video_id": video_id},
        headers=_headers(),
        timeout=15,
    )
    if resp.status_code != 200:
        raise HeyGenError(f"status check failed: {resp.status_code} {resp.text}")
    data = resp.json().get("data", {})
    return {
        "status": data.get("status"),
        "video_url": data.get("video_url"),
        "error": data.get("error"),
    }


def poll_until_ready(
    video_id: str,
    timeout_seconds: int = 240,
    poll_interval: int = 5,
) -> str:
    """
    Poll HeyGen until the video is ready. Returns the final video URL.
    Raises HeyGenError on timeout or render failure.
    """
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        resp = requests.get(
            f"{HEYGEN_API_BASE}/v1/video_status.get",
            params={"video_id": video_id},
            headers=_headers(),
            timeout=15,
        )

        if resp.status_code != 200:
            raise HeyGenError(f"status check failed: {resp.status_code} {resp.text}")

        data = resp.json().get("data", {})
        status = data.get("status")

        if status == "completed":
            url = data.get("video_url")
            if not url:
                raise HeyGenError(f"completed but no video_url: {data}")
            return url

        if status == "failed":
            raise HeyGenError(f"HeyGen render failed: {data.get('error', 'unknown')}")

        # statuses: pending, processing, waiting — keep polling
        time.sleep(poll_interval)

    raise HeyGenError(f"timed out after {timeout_seconds}s waiting for video {video_id}")


def render_personalized_video(first_name: str) -> tuple[str, str]:
    """
    The one function the worker calls.
    Submits + polls + returns (heygen_video_id, final_video_url).
    Total time: typically 60–120 seconds.
    """
    video_id = submit_video(first_name)
    url = poll_until_ready(video_id)
    return video_id, url
