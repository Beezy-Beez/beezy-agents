"""
Local TTS pipeline for sleep audio episodes.

Runs entirely inside beezy-agents — no external HTTP call to sleep-audio-platform.

Pipeline:
  script_text → chunk → ElevenLabs TTS (per chunk) → ffmpeg concat → Buzzsprout → #beezy-new-episodes

The Slack watcher (_process_new_episodes in agents/slack_agent.py) picks up the
#beezy-new-episodes post and creates Klaviyo campaigns automatically.

Required Replit Secrets (add to beezy-agents — copy from sleep-audio-platform):
    ELEVENLABS_API_KEY    — ElevenLabs Pro API key
    BUZZSPROUT_PODCAST_ID — numeric podcast ID from buzzsprout.com
    BUZZSPROUT_API_TOKEN  — Buzzsprout API token
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

_ELEVENLABS_KEY   = os.environ.get("ELEVENLABS_API_KEY", "")
_BUZZSPROUT_POD   = os.environ.get("BUZZSPROUT_PODCAST_ID", "")
_BUZZSPROUT_TOKEN = os.environ.get("BUZZSPROUT_API_TOKEN", "")
_SLACK_TOKEN      = os.environ.get("SLACK_BOT_TOKEN", "")
_NEW_EPISODES_CH  = "C0B3S0CM2JV"   # #beezy-new-episodes (locked)

# Margaret / Luna — confirmed voice from CLAUDE.md
_VOICE_ID       = "v7t81zh1sAZvDEPx2B8A"
_MODEL_ID       = "eleven_multilingual_v2"
_CHUNK_MAX_CHARS = 4_500   # safe limit per ElevenLabs request
_VOICE_SETTINGS  = {
    "stability":        0.45,
    "similarity_boost": 0.80,
    "style":            0.30,
    "use_speaker_boost": True,
}


# ── Text chunking ─────────────────────────────────────────────────────────────

def _chunk_script(text: str) -> list[str]:
    """Split at paragraph boundaries, then sentence boundaries, keeping chunks ≤ _CHUNK_MAX_CHARS."""
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > _CHUNK_MAX_CHARS:
            # Para itself exceeds limit — split at sentence boundaries
            if current:
                chunks.append(current)
                current = ""
            for sentence in re.split(r"(?<=[.!?])\s+", para):
                if len(current) + len(sentence) + 1 <= _CHUNK_MAX_CHARS:
                    current = (current + " " + sentence).strip() if current else sentence
                else:
                    if current:
                        chunks.append(current)
                    current = sentence
        elif current and len(current) + len(para) + 2 > _CHUNK_MAX_CHARS:
            chunks.append(current)
            current = para
        else:
            current = (current + "\n\n" + para).strip() if current else para
    if current:
        chunks.append(current)
    return chunks


# ── ElevenLabs TTS ────────────────────────────────────────────────────────────

def _synthesize_chunk(text: str, idx: int, total: int) -> bytes:
    """Call ElevenLabs TTS for one text chunk. Returns raw MP3 bytes."""
    print(f"[tts_pipeline] ElevenLabs chunk {idx+1}/{total} ({len(text):,} chars)")
    resp = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{_VOICE_ID}",
        headers={"xi-api-key": _ELEVENLABS_KEY, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": _MODEL_ID,
            "voice_settings": _VOICE_SETTINGS,
        },
        timeout=120,
    )
    if not resp.is_success:
        raise RuntimeError(f"ElevenLabs chunk {idx+1} → {resp.status_code}: {resp.text[:300]}")
    return resp.content


# ── Audio concat ─────────────────────────────────────────────────────────────

def _concatenate_mp3s(chunk_paths: list[Path], output_path: Path) -> None:
    """Concatenate MP3 chunks using ffmpeg concat demuxer (lossless copy)."""
    filelist = output_path.parent / "filelist.txt"
    filelist.write_text("\n".join(f"file '{p.resolve()}'" for p in chunk_paths))
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(filelist), "-c", "copy", str(output_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed:\n{result.stderr[-600:]}")


# ── Buzzsprout upload ─────────────────────────────────────────────────────────

def _upload_to_buzzsprout(audio_path: Path, title: str, description: str) -> str:
    """Upload MP3 to Buzzsprout. Returns the audio_url."""
    api_url = f"https://www.buzzsprout.com/api/{_BUZZSPROUT_POD}/episodes.json"
    with open(audio_path, "rb") as fh:
        resp = httpx.post(
            api_url,
            headers={"Authorization": f"Token token={_BUZZSPROUT_TOKEN}"},
            data={
                "title": title,
                "description": description,
                "private": "0",
                "episode_type": "full",
            },
            files={"audio_file": (audio_path.name, fh, "audio/mpeg")},
            timeout=300,
        )
    if not resp.is_success:
        raise RuntimeError(f"Buzzsprout upload → {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("audio_url", "")


# ── Slack post ────────────────────────────────────────────────────────────────

def _post_to_new_episodes(episode_meta: dict) -> None:
    """Post episode metadata JSON to #beezy-new-episodes for the Slack watcher."""
    text = "```" + json.dumps(episode_meta, ensure_ascii=False) + "```"
    resp = httpx.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {_SLACK_TOKEN}"},
        json={"channel": _NEW_EPISODES_CH, "text": text, "unfurl_links": False},
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack post to #beezy-new-episodes failed: {data.get('error')}")


def _post_tts_error(title: str, episode_id: str, error: str) -> None:
    """Alert #beezy-agents when TTS thread fails."""
    from lib.slack import post_draft
    post_draft(
        title=f"❌ TTS failed — {title}",
        summary_lines=[
            f"*Episode:* {title}",
            f"*Episode ID:* `{episode_id}`",
            f"*Error:* {error[:400]}",
            "The script is in the DB — re-trigger manually or check logs.",
        ],
        body="",
    )


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_tts_pipeline(
    *,
    episode_id: str,
    title: str,
    script_text: str,
    episode_type: str = "sleep_story",
    duration_minutes: int = 25,
    description_short: str = "",
    shopify_page_url: str = "",
    suggested_send_date: str = "",
) -> str:
    """
    Full synchronous TTS pipeline. Intended to run in a background thread.

    script_text → chunk → ElevenLabs (per chunk) → ffmpeg concat → Buzzsprout → #beezy-new-episodes

    Returns the Buzzsprout audio_url on success. Raises on failure
    (caller should catch and post error to Slack).
    """
    if not _ELEVENLABS_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY not set — add to beezy-agents Replit Secrets")
    if not _BUZZSPROUT_POD or not _BUZZSPROUT_TOKEN:
        raise RuntimeError("BUZZSPROUT_PODCAST_ID / BUZZSPROUT_API_TOKEN not set in Replit Secrets")

    print(f"[tts_pipeline] Starting — {title!r} ({len(script_text):,} chars, {duration_minutes} min)")

    chunks = _chunk_script(script_text)
    print(f"[tts_pipeline] {len(chunks)} chunks to synthesize")

    with tempfile.TemporaryDirectory() as tmpdir:
        run_dir = Path(tmpdir)
        chunk_paths: list[Path] = []

        for idx, chunk in enumerate(chunks):
            audio_bytes = _synthesize_chunk(chunk, idx, len(chunks))
            p = run_dir / f"chunk_{idx:03d}.mp3"
            p.write_bytes(audio_bytes)
            chunk_paths.append(p)
            if idx < len(chunks) - 1:
                time.sleep(0.3)   # avoid ElevenLabs rate limits

        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
        final_path = run_dir / f"{slug}.mp3"

        if len(chunk_paths) == 1:
            final_path = chunk_paths[0]
        else:
            print(f"[tts_pipeline] Concatenating {len(chunk_paths)} chunks with ffmpeg")
            _concatenate_mp3s(chunk_paths, final_path)

        file_mb = final_path.stat().st_size / (1024 * 1024)
        print(f"[tts_pipeline] Audio ready — {file_mb:.1f} MB — uploading to Buzzsprout")
        buzzsprout_url = _upload_to_buzzsprout(final_path, title, description_short)

    print(f"[tts_pipeline] Buzzsprout URL: {buzzsprout_url}")

    episode_meta = {
        "episode_id":          episode_id,
        "title":               title,
        "episode_type":        episode_type,
        "buzzsprout_url":      buzzsprout_url,
        "shopify_page_url":    shopify_page_url,
        "suggested_send_date": suggested_send_date,
        "duration_minutes":    duration_minutes,
    }
    _post_to_new_episodes(episode_meta)
    print(f"[tts_pipeline] Posted to #beezy-new-episodes — Slack watcher will deploy campaigns")

    return buzzsprout_url
