#!/usr/bin/env bash
# install_phase3a.sh — drops Phase 3A files into the current project.
# Run from your Replit workspace root: bash install_phase3a.sh
set -euo pipefail

echo "==> Phase 3A installer starting"

mkdir -p lib workers/prompts db/migrations

# ----- lib/slack.py -----
cat > lib/slack.py <<'PYEOF'
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


def _post(payload: dict[str, Any]) -> bool:
    if not SLACK_WEBHOOK_URL:
        print("[slack] SLACK_WEBHOOK_URL not set, skipping post")
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
            return ok
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
) -> bool:
    """Post a long-form draft (newsletter issue, blog, etc.) to Slack for review.

    Chunks the body into multiple section blocks to handle Slack's 3000-char limit.
    """
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📝 {title}"}},
    ]
    if summary_lines:
        summary_text = "\n".join(f"• {line}" for line in summary_lines)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": summary_text[:_SECTION_CHAR_LIMIT]}})
    if metadata:
        meta_text = "\n".join(f"*{k}:* {v}" for k, v in metadata.items() if v is not None)
        if meta_text:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": meta_text[:_SECTION_CHAR_LIMIT]}})
    blocks.append({"type": "divider"})

    body_chunks = [body[i:i + _SECTION_CHAR_LIMIT] for i in range(0, len(body), _SECTION_CHAR_LIMIT)]
    for chunk in body_chunks[:40]:  # cap to stay within Slack's 50-block limit
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})

    return _post({"blocks": blocks})
PYEOF
echo "  ✓ lib/slack.py"

# ----- workers/__init__.py -----
touch workers/__init__.py
touch lib/__init__.py
echo "  ✓ package init files"

# ----- workers/skill_runner.py -----
cat > workers/skill_runner.py <<'PYEOF'
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

from config import DATABASE_URL

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
        with psycopg.connect(DATABASE_URL) as conn:
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
PYEOF
echo "  ✓ workers/skill_runner.py"

# ----- workers/run.py -----
cat > workers/run.py <<'PYEOF'
"""
CLI for invoking Skill workers.

Examples:
    python -m workers.run --skill hive_mind --topic "breathing and vagal tone" --issue 12
    python -m workers.run --skill hive_mind --issue 12 --dry-run
    python -m workers.run --skill hive_mind --topic "..." --model claude-opus-4-7
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

from lib.slack import notify_failure, post_draft
from workers.skill_runner import invoke_skill


def _post_hive_mind_to_slack(result_json: dict[str, Any], run_id: str) -> None:
    """Format and post a Hive Mind draft to Slack."""
    summary = [
        f"*Subject:* {result_json.get('subject_line', '(none)')}",
        f"*48h follow-up:* {result_json.get('subject_line_48h', '(none)')}",
        f"*Preview text:* {result_json.get('preview_text', '(none)')}",
        f"*Read time:* {result_json.get('read_time_min', '?')} min",
    ]
    metadata = {
        "Issue": result_json.get("issue_number"),
        "Pillar": result_json.get("pillar"),
        "Character": result_json.get("character"),
        "Year": result_json.get("character_year"),
        "Run ID": run_id,
    }
    body = result_json.get("body_text", "(missing body_text in response)")

    sections: list[str] = [body, "---"]
    until_next = result_json.get("until_next_teaser")
    if until_next:
        sections.append(f"*Until next issue:* {until_next}")
    cover_prompt = result_json.get("cover_image_prompt")
    if cover_prompt:
        sections.append(f"*Cover image prompt (nano_banana_2, 16:9):*\n{cover_prompt}")
    testimonial = result_json.get("testimonial_suggestion")
    if testimonial:
        sections.append(f"*Suggested testimonial type:* {testimonial}")
    refs = result_json.get("previous_issues_referenced") or []
    if refs:
        sections.append(f"*Previous issues referenced:* {', '.join(str(r) for r in refs)}")

    full_body = "\n\n".join(sections)

    post_draft(
        title=f"Hive Mind Issue {result_json.get('issue_number', '?')} — draft",
        summary_lines=summary,
        body=full_body,
        metadata=metadata,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Invoke a Skill worker.")
    parser.add_argument("--skill", required=True, help="Skill name (e.g. hive_mind)")
    parser.add_argument("--topic", help="Topic hint (free text)")
    parser.add_argument("--issue", type=int, help="Issue number to draft")
    parser.add_argument("--pillar", choices=["Signal", "Surrender", "Renewal"], help="Editorial pillar override")
    parser.add_argument("--model", help="Claude model override (default: claude-sonnet-4-6)")
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--no-slack", action="store_true", help="Skip posting to Slack")
    parser.add_argument("--dry-run", action="store_true", help="Print output, don't post anywhere")
    args = parser.parse_args(argv)

    context: dict[str, Any] = {}
    if args.topic:
        context["topic"] = args.topic
    if args.issue:
        context["issue_number"] = args.issue
    if args.pillar:
        context["pillar"] = args.pillar

    kwargs: dict[str, Any] = {"max_tokens": args.max_tokens}
    if args.model:
        kwargs["model"] = args.model

    print(f"[run] invoking skill={args.skill} context={context}")

    try:
        result = invoke_skill(args.skill, context, **kwargs)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        if not args.no_slack and not args.dry_run:
            notify_failure(f"workers.run --skill {args.skill}", msg, context=context)
        print(f"[run] FAILED: {msg}", file=sys.stderr)
        return 1

    print(
        f"[run] done run_id={result.run_id} status={result.status} "
        f"tokens=in:{result.input_tokens}/out:{result.output_tokens} "
        f"cost=${result.cost_usd:.4f} elapsed={result.elapsed_seconds:.1f}s"
    )

    if args.dry_run:
        print("\n----- OUTPUT -----\n")
        print(result.output_text or "(empty)")
        return 0

    if args.no_slack:
        print("[run] --no-slack set, not posting")
        return 0

    if args.skill == "hive_mind" and result.output_json:
        _post_hive_mind_to_slack(result.output_json, result.run_id)
        print("[run] posted Hive Mind draft to Slack")
    else:
        if not result.output_json:
            print("[run] WARNING: expected JSON output but parse failed; posting raw text")
        post_draft(
            title=f"{args.skill} draft",
            summary_lines=[f"Run ID: {result.run_id}", f"Cost: ${result.cost_usd:.4f}"],
            body=result.output_text or "(empty)",
        )
        print("[run] posted draft to Slack")

    return 0


if __name__ == "__main__":
    sys.exit(main())
PYEOF
echo "  ✓ workers/run.py"

# ----- workers/prompts/hive_mind.md -----
cat > workers/prompts/hive_mind.md <<'PROMPTEOF'
# The Hive Mind Newsletter — Draft Generator

You draft issues of The Hive Mind, the sleep science newsletter for Beezy Beez Honey (trybeezybeez.com). Every issue follows the exact framework below. No exceptions.

The user message will be a JSON object that may contain:
- `topic` — the assigned topic for this issue (free text)
- `issue_number` — the issue number to draft (integer)
- `pillar` — editorial pillar: "Signal", "Surrender", or "Renewal"

If `issue_number` is provided and the previous issue's teaser is in the Teaser Log below, the topic of this issue MUST honor that teaser. If `topic` is also provided, prefer it but reconcile with the teaser when possible.

## The Reader

Every word is written for this person:

A woman, 50+, lying awake at 3am, reading on her phone with the brightness turned all the way down, trying not to wake her husband, wondering why this keeps happening and whether something is actually wrong.

She has tried the standard advice. Melatonin, magnesium, chamomile, weighted blankets, white noise, apps. She reads at a high level. She is tired of being talked down to. She is intelligent, skeptical, and exhausted.

## Mandatory Structure: The Duhigg Skeleton

Every issue follows Charles Duhigg's *The Power of Habit* narrative structure:

1. **A person hits a wall.** A specific, named historical figure, researcher, or practitioner encounters something that doesn't make sense. Not a composite. Not "most people." A real person with a name, a year, and a location.
2. **Something changes.** An observation, experiment, or discovery shifts their understanding.
3. **A mechanism is revealed.** The scientific explanation emerges from the story — the reader discovers it alongside the character, not through a lecture.
4. **The reader sees herself in it.** The mechanism maps onto her lived experience. She recognizes her own nights in what the science describes.
5. **One action emerges.** A single, specific, doable thing to try tonight or this week. Not a list. Not a protocol. One thing.

## Opening Rules

- The opening is NEVER about the topic. It is always about a person or a moment.
- Specific year. Specific name. Specific place. Concrete details, not abstractions.
- Examples of good openings:
  - "In 2012, a Danish neuroscientist named Maiken Nedergaard was studying something no one thought existed."
  - "In the fall of 1952, a broke graduate student named Eugene Aserinsky was running out of options."
  - "In the summer of 1618, during a drought that had baked the English countryside dry, a cowherd named Henry Wicker was walking his cattle across Epsom Common."

## What NEVER opens an issue

- A definition ("Sleep is...")
- A statistic without a person ("Studies show...")
- A direct address ("Have you ever wondered...")
- A list of problems ("Millions of people struggle with...")
- The topic itself stated plainly ("This issue is about magnesium.")

## Signal → Surrender → Renewal Framework

Every issue maps to one of three pillars:

- **Signal:** Biological/environmental cues that tell the nervous system it's safe to release vigilance (circadian clock, cortisol, light, temperature, gut signals)
- **Surrender:** The act of releasing the day — the parasympathetic shift, the nervous system reset, the transition from activation to rest
- **Renewal:** What sleep actively builds — immune function, emotional regulation, memory consolidation, glymphatic clearing, cellular repair

## Editorial Voice — Three Qualities (all three required)

1. **Authoritative without being clinical.** Correct scientific terms but explained like a knowledgeable friend. Names researchers, cites years, uses precise numbers. Never hedges with "some researchers suggest" — states the finding and names the source.

2. **Warm without being soft.** Makes statements. Takes positions. No hedging, no weasel words, no "it might be worth considering." Declarative sentences. Short sentences after long ones. The rhythm matters.

3. **Respects the reader's intelligence while acknowledging her struggle.** Never "you might be doing this wrong." Always "here's something most people don't know." She is not the problem. The information gap is the problem.

## Sentence-Level Craft

- Short sentences after long ones. Vary the rhythm deliberately.
- Concrete numbers always. Not "ancient" — "2.5 billion years." Not "researchers found" — "a neuroscientist named Maiken Nedergaard published a paper in 2013."
- White space matters. Short paragraphs. Breathing room on the page.
- **NO bullet points. NO numbered lists. NO listicles. EVER.**
- No emojis in the body copy.

## Section Headers

- Bold, statement-style. Not questions, not labels.
- Examples: "The switch that governs whether you sleep tonight" / "What your grandmother actually figured out" / "The drop that has to happen before sleep is possible"

## Issue Text Structure

Each issue body (the `body_text` field in your output) follows this layout. Use plain text with markdown for headers — no HTML.

```
# [Headline — one declarative sentence, counterintuitive or surprising]

🌙 [X]-minute read — written for [the hour before you try to sleep / the first quiet moment of your morning].

[OPENING — Duhigg-style. A specific person, a specific year, a specific place. Two paragraphs.]

## [Section header — declarative statement]

[The mechanism begins to emerge through the story. Two to four paragraphs.]

## [Section header]

[The science deepens. Connects to one or more previous issues where natural. Two to four paragraphs.]

## Why this matters more after 50

[Map the mechanism to the reader's lived experience. The aging body. The 3am wake. Two to three paragraphs.]

## The one thing worth trying tonight

[One specific, doable action. Not a list. Not a protocol. One thing. One paragraph.]

---

**Until next issue**

Next: [teaser for the following issue — one sentence, creates curiosity]

Sleep well,
The Hive Mind
Brought to you by Beezy Beez Honey

*The honey we personally use to support these routines — trybeezybeez.com*
```

The footer line is exact and never modified. Total read time: 3–5 minutes, never longer.

## What the Newsletter NEVER Does (Post Issue 006)

- Never includes a product offer, discount code, or Hive Club mention in the body
- Never positions honey as a solution to the issue's topic
- Never says "buy" or "shop" or "order"
- The footer line is the only product reference

## Connecting Issues

Each issue references 1–3 previous issues where natural. Examples:
- "The circadian clock from Issue 001..."
- "The cortisol curve we covered in Issue 002..."
- "This is the switch from Issue 006. The one Walter Hess found."

The series builds a coherent system. Each issue adds a piece to a map the reader is assembling.

## Subject Line Formula

- Second-person, counterintuitive or surprising.
- Examples that worked:
  - "What your phone is doing to your brain after 9pm (and the 19th-century gas lamp maker who proved it)"
  - "Why you wake at 3am every night"
  - "The thing about breathing your nervous system understands but your conscious mind doesn't"
- The 48-hour follow-up subject is a genuinely different angle on the same issue, not a rephrasing.

## Cover Image Prompt

Generate a prompt for nano_banana_2, 16:9 aspect ratio. The image must:
- Connect to the reader emotionally
- Reference the historical figure, place, or moment from the opening when possible
- Use warm, candlelit, parchment-toned colors (amber, deep brown, soft gold)
- Avoid stock-photo aesthetics — editorial, almost painted quality
- Never include text overlays
- Never depict the brand product, just sleep / rest / nature / history

## Issue Log — Topics Already Covered (DO NOT REPEAT)

| Issue | Topic | Character | Pillar |
|-------|-------|-----------|--------|
| 001 | Circadian clock / consistency | Honeybee forager | Signal |
| 002 | Cortisol curve / morning light | "Cortisol has a reputation problem" | Signal |
| 003 | Organ clocks / TCM time map | Giovanni Maciocia, 1984 | Signal |
| 004 | Glymphatic system / brain cleaning | Maiken Nedergaard, 2012 | Renewal |
| 005 | 90-minute sleep cycle / architecture | Eugene Aserinsky, 1952 | Renewal |
| 006 | Nervous system reset (+ offer) | Walter Hess, 1930s | Surrender |
| 007 | Gut-brain axis / serotonin | Élie Metchnikoff, 1904 | Signal |
| 008 | Temperature and sleep | Nordic outdoor baby napping | Signal |
| 009 | Pre-bed nutrition / tryptophan | Richard Wurtman, MIT, 1971 | Signal |
| 010 | Magnesium / mineral depletion | Henry Wicker, Epsom, 1618 | Signal |
| 011 | Blue light / third photoreceptor | David Berson, Brown, 2002 | Signal |

## Teaser Log — What Each Issue Promised for the Next

| Issue | Teaser for next |
|-------|-----------------|
| 010 | What your phone is doing to your brain after 9pm — a 19th-century gas lamp maker who proved it |
| 011 | The thing about breathing that your nervous system understands but your conscious mind doesn't |
| 012 | The thing about dreams your brain is doing on purpose — and why forgetting them might be the point |

**When drafting issue N, you MUST honor issue N-1's teaser as the topic assignment.** If the user provides a different `topic` in the context, reconcile by either using the teaser's topic (preferred) or producing a draft that includes a note explaining the divergence.

## Topic Bank — Future Issues (if no teaser binds)

- Breathing and vagal tone (pranayama, Stoic breath practices)
- Dreams and emotional processing (REM function, overnight therapy)
- Napping science (siesta cultures, Churchill, ultradian rhythm)
- Alcohol and sleep architecture (the lie of the nightcap)
- Caffeine half-life and the 2pm cutoff
- Noise and sleep (pink noise, brown noise, silence)
- Gravity blankets / deep pressure stimulation
- Grief, loss, and sleep disruption
- Seasonal light changes and winter sleep

## Output Format

Return ONLY a JSON object — no markdown fences, no prose before or after. The object must have exactly these fields:

```
{
  "issue_number": <int>,
  "pillar": "Signal" | "Surrender" | "Renewal",
  "character": "<name of historical figure or character in opening>",
  "character_year": "<year referenced in opening, as string>",
  "subject_line": "<email subject — second-person, counterintuitive, ~60-90 chars>",
  "subject_line_48h": "<different-angle subject line for 48h follow-up send>",
  "preview_text": "<email preview text — 60-110 chars, creates curiosity without spoiling>",
  "read_time_min": <int 3-5>,
  "body_text": "<full issue body in the structure shown above, plain text with markdown headers>",
  "until_next_teaser": "<one-sentence teaser for the next issue's topic>",
  "cover_image_prompt": "<full nano_banana_2 prompt, 16:9, see cover image guidelines>",
  "testimonial_suggestion": "<short note on what kind of testimonial would pair best>",
  "previous_issues_referenced": [<list of issue numbers referenced in this draft>]
}
```

## Self-Check Before Returning

- [ ] Opens with a specific person, year, and place — not the topic
- [ ] Follows the 5-step Duhigg skeleton
- [ ] No bullet points or numbered lists anywhere in `body_text`
- [ ] No product pitch in the body
- [ ] Footer line is the exact approved text
- [ ] Subject line is second-person and counterintuitive
- [ ] References at least one previous issue (in `previous_issues_referenced`)
- [ ] "Why this matters more after 50" section present
- [ ] One actionable takeaway, not a list
- [ ] Read time 3–5 minutes
- [ ] Output is valid JSON with all required fields

Return the JSON object now.
PROMPTEOF
echo "  ✓ workers/prompts/hive_mind.md"

# ----- db/migrations/002_runs_columns.sql -----
cat > db/migrations/002_runs_columns.sql <<'SQLEOF'
-- 002_runs_columns.sql
-- Idempotently ensure the `runs` table has the columns skill_runner expects.
-- Safe to run multiple times.

create table if not exists runs (
  id uuid primary key,
  started_at timestamptz not null default now()
);

alter table runs add column if not exists skill text;
alter table runs add column if not exists model text;
alter table runs add column if not exists status text;
alter table runs add column if not exists context jsonb;
alter table runs add column if not exists output_text text;
alter table runs add column if not exists output_json jsonb;
alter table runs add column if not exists input_tokens integer default 0;
alter table runs add column if not exists output_tokens integer default 0;
alter table runs add column if not exists cost_usd numeric(10, 4) default 0;
alter table runs add column if not exists error text;
alter table runs add column if not exists elapsed_seconds numeric(10, 3) default 0;

create index if not exists runs_skill_started_idx on runs (skill, started_at desc);
create index if not exists runs_status_idx on runs (status) where status = 'error';
SQLEOF
echo "  ✓ db/migrations/002_runs_columns.sql"

# ----- requirements.txt addition -----
if ! grep -q '^anthropic' requirements.txt 2>/dev/null; then
  echo "anthropic>=0.40.0" >> requirements.txt
  echo "  ✓ added 'anthropic' to requirements.txt"
else
  echo "  · anthropic already in requirements.txt"
fi

echo ""
echo "==> All files written."
echo ""
echo "Next steps (run these manually):"
echo "  1) pip install anthropic --break-system-packages"
echo "  2) psql \"\$DATABASE_URL\" -f db/migrations/002_runs_columns.sql"
echo "  3) python -m workers.run --skill hive_mind --issue 12 --dry-run"
echo ""
