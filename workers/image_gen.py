"""Cover image generation via Higgsfield REST API at platform.higgsfield.ai.

Bypasses the higgsfield-client SDK (whose documented model paths are stale).
Reads HIGGSFIELD_API_KEY and HIGGSFIELD_SECRET from environment, sends them
as `Authorization: Key {key}:{secret}` per the official docs.

Default model: higgsfield-ai/soul/standard (Soul Standard, painterly/editorial)
Other tested model paths: reve/text-to-image

Full model catalog: https://cloud.higgsfield.ai (Models Gallery)
API docs: https://docs.higgsfield.ai/docs

Usage:
    from workers.image_gen import generate_cover
    result = generate_cover("editorial illustration of...")
    print(result.url)
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx


BASE_URL = "https://platform.higgsfield.ai"

DEFAULT_MODEL = os.environ.get(
    "HIGGSFIELD_IMAGE_MODEL",
    "higgsfield-ai/soul/standard",
)
DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_RESOLUTION = "720p"


@dataclass
class ImageGenResult:
    url: str
    model: str
    request_id: str
    elapsed_seconds: float
    raw: dict[str, Any]


def _auth_header() -> str:
    key = os.environ.get("HIGGSFIELD_API_KEY")
    secret = os.environ.get("HIGGSFIELD_SECRET")
    if not key or not secret:
        raise RuntimeError(
            "HIGGSFIELD_API_KEY and HIGGSFIELD_SECRET must be set in Replit Secrets."
        )
    return f"Key {key}:{secret}"


def generate_cover(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    resolution: Optional[str] = DEFAULT_RESOLUTION,
    extra_args: Optional[dict[str, Any]] = None,
    poll_interval_seconds: float = 3.0,
    timeout_seconds: float = 180.0,
) -> ImageGenResult:
    """Submit a text-to-image generation and block until done.

    Returns ImageGenResult with the public URL of the generated image.
    Raises RuntimeError on failure (4xx, 5xx, nsfw, failed status).
    """
    headers = {
        "Authorization": _auth_header(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    body: dict[str, Any] = {"prompt": prompt}
    if aspect_ratio:
        body["aspect_ratio"] = aspect_ratio
    if resolution:
        body["resolution"] = resolution
    if extra_args:
        body.update(extra_args)

    submit_url = f"{BASE_URL}/{model.lstrip('/')}"
    print(f"[image_gen] POST {submit_url} aspect={aspect_ratio} resolution={resolution}")

    started = time.time()
    with httpx.Client(timeout=30.0) as client:
        # Submit
        try:
            resp = client.post(submit_url, headers=headers, json=body)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            elapsed = time.time() - started
            body_preview = e.response.text[:400] if e.response.text else "(no body)"
            raise RuntimeError(
                f"Higgsfield submit failed ({e.response.status_code}) after {elapsed:.1f}s: {body_preview}"
            ) from e
        except httpx.RequestError as e:
            raise RuntimeError(f"Higgsfield submit network error: {e}") from e

        submit_data = resp.json()
        request_id = submit_data.get("request_id")
        status_url = submit_data.get("status_url")
        if not request_id or not status_url:
            raise RuntimeError(f"Submit returned malformed response: {submit_data}")
        print(f"[image_gen] queued request_id={request_id}, polling...")

        # Poll
        deadline = started + timeout_seconds
        while True:
            if time.time() > deadline:
                raise RuntimeError(
                    f"Higgsfield generation timed out after {timeout_seconds}s (request_id={request_id})"
                )
            time.sleep(poll_interval_seconds)

            try:
                poll_resp = client.get(status_url, headers=headers)
                poll_resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                body_preview = e.response.text[:400] if e.response.text else "(no body)"
                raise RuntimeError(
                    f"Higgsfield poll failed ({e.response.status_code}): {body_preview}"
                ) from e

            poll_data = poll_resp.json()
            status = poll_data.get("status")

            if status == "completed":
                elapsed = time.time() - started
                images = poll_data.get("images") or []
                if not images:
                    raise RuntimeError(f"Completed but no images in response: {poll_data}")
                url = images[0].get("url") if isinstance(images[0], dict) else None
                if not url:
                    raise RuntimeError(f"No URL in first image entry: {images[0]}")
                print(f"[image_gen] done in {elapsed:.1f}s url={url[:80]}...")
                return ImageGenResult(
                    url=url,
                    model=model,
                    request_id=request_id,
                    elapsed_seconds=elapsed,
                    raw=poll_data,
                )

            if status in ("failed", "nsfw"):
                raise RuntimeError(
                    f"Higgsfield generation {status} (request_id={request_id}): {poll_data}"
                )

            # queued or in_progress — keep polling
            print(f"[image_gen] status={status}, continuing to poll...")
