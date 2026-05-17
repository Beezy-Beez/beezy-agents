"""
src/generation/routes.py  — add to sleep-audio-platform

POST /api/v1/generate — called by beezy-agents when a sleep_audio calendar slot fires.

Receives the Claude-generated script, runs ElevenLabs TTS, uploads to Buzzsprout,
and posts episode metadata JSON to #beezy-new-episodes so the Replit watcher
auto-creates Klaviyo campaigns and updates the Shopify page with the audio player.

New Replit Secrets needed in sleep-audio-platform:
    GENERATE_API_KEY        — shared secret with beezy-agents (set same value there as SLEEP_AUDIO_API_KEY)
    BUZZSPROUT_PODCAST_ID   — numeric podcast ID from buzzsprout.com
    BUZZSPROUT_API_TOKEN    — Buzzsprout API token

New Replit Secrets needed in beezy-agents:
    SLEEP_AUDIO_API_URL     — sleep-audio-platform deployment URL, e.g. https://sleep-audio-platform.replit.app
    SLEEP_AUDIO_API_KEY     — same value as GENERATE_API_KEY above

Also add to app/main.py in sleep-audio-platform:
    from src.generation.routes import router as generation_router
    app.include_router(generation_router)

ffmpeg must be available on the Replit host. Add to replit.nix if not already present:
    pkgs.ffmpeg
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx
import structlog
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel

from src.db import get_session
from src.generation.tts import TTSPipeline
from src.profiles.loader import load_profile
from src.profiles.voice_selector import select_voice

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["Generation"])

# In-memory run state — survives for the lifetime of the process.
# Keys: run_id (str). Values: dict with status, episode_id, title, timestamps.
_run_states: dict[str, dict] = {}

_API_KEY          = os.environ.get("GENERATE_API_KEY", "")
_BUZZSPROUT_POD   = os.environ.get("BUZZSPROUT_PODCAST_ID", "")
_BUZZSPROUT_TOKEN = os.environ.get("BUZZSPROUT_API_TOKEN", "")
_SLACK_TOKEN      = os.environ.get("SLACK_BOT_TOKEN", "")
_NEW_EPISODES_CH  = "C0B3S0CM2JV"   # #beezy-new-episodes (locked)

_VOICE_CATALOG = Path(__file__).resolve().parents[2] / "configs" / "voice_catalog.yaml"


# ── Request / Response models ─────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    episode_id: str
    title: str
    topic: str
    script_text: str          # Claude-generated script from beezy-agents
    episode_type: str = "sleep_story"
    duration_minutes: int = 25
    profile: str = "sleep_story_philosophical"
    description_short: str = ""
    shopify_page_url: str = ""
    suggested_send_date: str = ""


class GenerateResponse(BaseModel):
    status: str
    run_id: str


class StatusResponse(BaseModel):
    run_id: str
    status: str                   # accepted | tts_running | buzzsprout_uploading | slack_posting | completed | failed
    episode_id: Optional[str] = None
    title: Optional[str] = None
    accepted_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/generate", response_model=GenerateResponse, status_code=202)
async def generate(
    request: GenerateRequest,
    background_tasks: BackgroundTasks,
    x_api_key: str = Header(default=""),
) -> GenerateResponse:
    if _API_KEY and x_api_key != _API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")

    run_id = str(uuid.uuid4())
    _run_states[run_id] = {
        "run_id":      run_id,
        "status":      "accepted",
        "episode_id":  request.episode_id,
        "title":       request.title,
        "accepted_at": time.time(),
        "completed_at": None,
        "error":       None,
    }
    log.info("generate_accepted", episode_id=request.episode_id, title=request.title, run_id=run_id)
    background_tasks.add_task(_run_pipeline, request, run_id)
    return GenerateResponse(status="accepted", run_id=run_id)


@router.get("/status/{run_id}", response_model=StatusResponse)
async def status(
    run_id: str,
    x_api_key: str = Header(default=""),
) -> StatusResponse:
    if _API_KEY and x_api_key != _API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    state = _run_states.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"run_id {run_id!r} not found — may have been lost on restart")
    return StatusResponse(**state)


# ── Pipeline (background task) ────────────────────────────────────────────────

async def _run_pipeline(request: GenerateRequest, run_id: str) -> None:
    def _set_status(status: str) -> None:
        if run_id in _run_states:
            _run_states[run_id]["status"] = status

    log.info("pipeline_start", episode_id=request.episode_id, run_id=run_id)
    try:
        _set_status("tts_running")
        profile = load_profile(request.profile)

        # Voice selection
        if profile.voice_selection.voice_id:
            voice_id = profile.voice_selection.voice_id
        else:
            voice_id, _, _ = select_voice(profile.voice_selection.tags, _VOICE_CATALOG)

        slug = re.sub(r"[^a-z0-9]+", "-", request.title.lower()).strip("-")[:40]

        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)

            pipeline = await TTSPipeline.from_secrets()

            # Body TTS
            async with get_session() as session:
                result = await pipeline.generate(
                    script_text=request.script_text,
                    voice_id=voice_id,
                    profile=profile,
                    output_dir=run_dir,
                    session=session,
                    run_id=run_id,
                    output_filename=f"{slug}-voice.mp3",
                )
                await session.commit()

            log.info("tts_complete",
                     duration_min=round(result.total_duration_seconds / 60, 1),
                     cost_usd=result.total_cost_usd,
                     chunks=len(result.chunks))

            # Intro / outro (non-fatal if missing)
            if getattr(profile.tts, "intro_text", None):
                try:
                    await pipeline.synthesize_text(
                        text=profile.tts.intro_text,
                        profile=profile,
                        output_path=run_dir / f"{slug}-intro.mp3",
                    )
                except Exception as exc:
                    log.warning("intro_tts_failed", error=str(exc))

            if getattr(profile.tts, "outro_text", None):
                try:
                    await pipeline.synthesize_text(
                        text=profile.tts.outro_text,
                        profile=profile,
                        output_path=run_dir / f"{slug}-outro.mp3",
                    )
                except Exception as exc:
                    log.warning("outro_tts_failed", error=str(exc))

            # Upload body audio to Buzzsprout (must happen before tmpdir is deleted)
            _set_status("buzzsprout_uploading")
            buzzsprout_url = await _upload_to_buzzsprout(
                audio_path=result.audio_path,
                title=request.title,
                description=request.description_short,
            )
            log.info("buzzsprout_uploaded", url=buzzsprout_url)

        # tmpdir cleaned up — post metadata to Slack
        _set_status("slack_posting")
        episode_meta = {
            "episode_id":          request.episode_id,
            "title":               request.title,
            "episode_type":        request.episode_type,
            "buzzsprout_url":      buzzsprout_url,
            "shopify_page_url":    request.shopify_page_url,
            "suggested_send_date": request.suggested_send_date,
            "duration_minutes":    int(result.total_duration_seconds / 60),
        }
        await _post_to_new_episodes(episode_meta)

        _run_states[run_id]["status"]       = "completed"
        _run_states[run_id]["completed_at"] = time.time()
        log.info("pipeline_complete", episode_id=request.episode_id, run_id=run_id)

    except Exception as exc:
        log.exception("pipeline_failed", episode_id=request.episode_id, run_id=run_id)
        if run_id in _run_states:
            _run_states[run_id]["status"] = "failed"
            _run_states[run_id]["error"]  = str(exc)


# ── Buzzsprout upload ─────────────────────────────────────────────────────────

async def _upload_to_buzzsprout(audio_path: Path, title: str, description: str) -> str:
    api_url = f"https://www.buzzsprout.com/api/{_BUZZSPROUT_POD}/episodes.json"
    headers = {"Authorization": f"Token token={_BUZZSPROUT_TOKEN}"}
    with open(audio_path, "rb") as fh:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                api_url,
                headers=headers,
                data={
                    "title":        title,
                    "description":  description,
                    "private":      "0",
                    "episode_type": "full",
                },
                files={"audio_file": (audio_path.name, fh, "audio/mpeg")},
            )
    resp.raise_for_status()
    return resp.json().get("audio_url", "")


# ── Slack post to #beezy-new-episodes ─────────────────────────────────────────

async def _post_to_new_episodes(episode_meta: dict) -> None:
    text = f"```{json.dumps(episode_meta, ensure_ascii=False)}```"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {_SLACK_TOKEN}"},
            json={"channel": _NEW_EPISODES_CH, "text": text, "unfurl_links": False},
        )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack post failed: {data.get('error')}")
