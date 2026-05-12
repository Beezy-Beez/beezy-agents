"""Generic Skill invoker.

A worker is a thin wrapper that:
  1. Loads the per-Skill system prompt from `workers/prompts/<skill>.md`.
  2. Calls the Anthropic API with that prompt + caller-supplied context.
  3. Returns the produced artifact + run metadata (tokens, latency, model, etc.).

The same runner is reused for every Skill — Hive Mind newsletter, sleep audio,
SEO blog, campaign email, SMS, flow tuning. Cron picks the Skill name and
context; the runner doesn't know or care what the Skill produces.
"""

from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(skill: str) -> str:
    """Read the system prompt for the named Skill from workers/prompts/<skill>.md."""
    raise NotImplementedError(
        "Load workers/prompts/<skill>.md and return its contents. "
        "Raise a clear error if the file is missing or empty."
    )


def run(skill: str, context: dict[str, Any]) -> dict[str, Any]:
    """Invoke a Skill via the Anthropic API.

    Args:
        skill: Skill name matching a file in workers/prompts/ (e.g. "hive_mind").
        context: Caller-supplied inputs (topic, recent performance, calendar slot, etc.)
                 to be rendered into the user message.

    Returns:
        {
          "artifact": <produced content>,
          "metadata": {
            "skill": skill,
            "model": ...,
            "input_tokens": ...,
            "output_tokens": ...,
            "latency_ms": ...,
          },
        }
    """
    raise NotImplementedError(
        "skill_runner.run: load_prompt(skill), build the user message from context, "
        "call Anthropic (Sonnet by default), capture artifact + metadata, return both."
    )
