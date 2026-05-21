"""
Generic Skill runner.

Loads a system prompt from workers/prompts/<skill>.md, calls Claude via the
Anthropic API with the prompt + a context payload, captures the response,
and logs the run to the `runs` table in Postgres.

Usage (from Python):
    from workers.skill_runner import invoke_skill
    result = invoke_skill("hive_mind", {"topic": "blue light", "issue_number": 11})

CLI: see workers/run.py
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import psycopg
from anthropic import Anthropic

from config import NEON_DATABASE_URL

PROMPTS_DIR = Path(__file__).parent / "prompts"
DEFAULT_MODEL = "claude-sonnet-4-6"

# Per-million-token pricing in USD. Keep in sync with anthropic.com/pricing.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (15.0, 75.0),
    "claude-opus-4-6": (15.0, 75.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}


@dataclass
class SkillResult:
    run_id: str
    skill: str
    model: str
    status: str  # 'success' | 'error'
    output_text: Optional[str]
    output_json: Optional[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: Optional[str]
    elapsed_seconds: float


def _load_prompt(skill: str) -> str:
    path = PROMPTS_DIR / f"{skill}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"No prompt file at {path}. Create workers/prompts/{skill}.md to define the skill."
        )
    return path.read_text(encoding="utf-8")


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    p_in, p_out = _MODEL_PRICING.get(model, (3.0, 15.0))
    return round(
        (input_tokens / 1_000_000) * p_in + (output_tokens / 1_000_000) * p_out,
        4,
    )


def _try_parse_json(text: str) -> Optional[dict[str, Any]]:
    """Extract a JSON object from text, tolerating markdown fences and prose."""
    if not text:
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    first = candidate.find("{")
    last = candidate.rfind("}")
    if first == -1 or last == -1 or last <= first:
        return None
    try:
        return json.loads(candidate[first : last + 1])
    except (json.JSONDecodeError, ValueError):
        return None


def _log_run(result: SkillResult, context: dict[str, Any]) -> None:
    """Insert a row into the runs table. Best-effort; logs but doesn't raise on failure."""
    try:
        with psycopg.connect(NEON_DATABASE_URL) as conn:
            conn.execute(
                """
                insert into runs
                  (id, skill, model, status, context, output_text, output_json,
                   input_tokens, output_tokens, cost_usd, error, elapsed_seconds, started_at)
                values (%s, %s, %s, %s, %s::jsonb, %s, %s::jsonb,
                        %s, %s, %s, %s, %s, now())
                """,
                (
                    result.run_id,
                    result.skill,
                    result.model,
                    result.status,
                    json.dumps(context),
                    result.output_text,
                    json.dumps(result.output_json) if result.output_json else None,
                    result.input_tokens,
                    result.output_tokens,
                    result.cost_usd,
                    result.error,
                    result.elapsed_seconds,
                ),
            )
    except Exception as e:
        print(f"[skill_runner] failed to log run to Postgres: {e}")


def invoke_skill(
    skill: str,
    context: dict[str, Any],
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 8192,
    expect_json: bool = True,
) -> SkillResult:
    """Invoke a Skill. Returns SkillResult on success, raises on API error."""
    run_id = str(uuid.uuid4())
    system_prompt = _load_prompt(skill)

    api_key = os.environ.get("BEEZY_ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "BEEZY_ANTHROPIC_API_KEY environment variable not set. "
            "Add it in Replit Secrets."
        )

    client = Anthropic(api_key=api_key)
    user_message = (
        json.dumps(context, indent=2)
        if isinstance(context, dict)
        else str(context)
    )

    started = time.time()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        elapsed = time.time() - started

        output_text = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )
        output_json = _try_parse_json(output_text) if expect_json else None

        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = _estimate_cost(model, input_tokens, output_tokens)

        result = SkillResult(
            run_id=run_id,
            skill=skill,
            model=model,
            status="success",
            output_text=output_text,
            output_json=output_json,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            error=None,
            elapsed_seconds=elapsed,
        )
        _log_run(result, context)
        return result

    except Exception as e:
        elapsed = time.time() - started
        result = SkillResult(
            run_id=run_id,
            skill=skill,
            model=model,
            status="error",
            output_text=None,
            output_json=None,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            error=f"{type(e).__name__}: {e}",
            elapsed_seconds=elapsed,
        )
        _log_run(result, context)
        raise
