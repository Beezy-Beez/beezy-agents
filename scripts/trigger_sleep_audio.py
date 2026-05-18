"""
Manually trigger one sleep_audio episode end-to-end.
Runs the full beezy-agents pipeline: script → image → Shopify page → TTS dispatch.

Usage (from workspace root):
    python3 -m scripts.trigger_sleep_audio

Slot defaults to today + a hardcoded topic. Edit SLOT below to change.
"""
from __future__ import annotations
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date

SLOT = {
    "date":             date.today().isoformat(),
    "content_type":     "sleep_audio",
    "episode_type":     "sleep_story",
    "topic_angle":      "Why sleep is the original medicine — a bedtime walk through ancient healing traditions",
    "duration_minutes": 25,
    "tone_notes":       "philosophical, warm, unhurried",
}


def main() -> None:
    print(f"[trigger] Running sleep_audio pipeline")
    print(f"[trigger] Topic: {SLOT['topic_angle']!r}")
    print(f"[trigger] Date:  {SLOT['date']}")
    print()

    from workers.sleep_audio_producer import run_sleep_audio_slot
    result = run_sleep_audio_slot(SLOT)
    print(f"\n[trigger] Done — result: {result}")


if __name__ == "__main__":
    main()
