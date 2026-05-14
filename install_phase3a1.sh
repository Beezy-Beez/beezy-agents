#!/usr/bin/env bash
# install_phase3a1.sh — Phase 3A.1 update.
#  - Adds issues table + backfill of 012-014
#  - Updates Hive Mind prompt: 3,500-4,500 word target, dual output (long-form + email teaser)
#  - Updates workers/run.py: reads issues table for next issue & previous teaser; writes draft back
#
# Run from your Replit workspace root: bash install_phase3a1.sh
set -euo pipefail

echo "==> Phase 3A.1 installer starting"

mkdir -p workers/prompts db/migrations

# ----- db/migrations/003_issues_table.sql -----
cat > db/migrations/003_issues_table.sql <<'SQLEOF'
-- 003_issues_table.sql
-- Track every Hive Mind issue: published, scheduled, or draft.
-- Single source of truth for what issue is next + what the previous teaser was.

create table if not exists issues (
  number integer primary key,
  subject_line text,
  subject_line_48h text,
  preview_text text,
  character_name text,
  character_year text,
  character_location text,
  pillar text check (pillar in ('Signal', 'Surrender', 'Renewal')),
  topic_summary text,
  page_url text,
  page_slug text,
  cover_image_url text,
  cover_image_prompt text,
  email_template_id text,
  campaign_id text,
  long_form_body text,
  email_teaser_body text,
  until_next_teaser text,
  previous_issues_referenced integer[],
  read_time_min integer,
  word_count_long_form integer,
  word_count_email_teaser integer,
  drafted_at timestamptz default now(),
  scheduled_send_at timestamptz,
  published_at timestamptz,
  status text default 'draft' check (status in ('draft', 'scheduled', 'published')),
  run_id uuid,
  notes text
);

create index if not exists issues_status_number_idx on issues (status, number desc);

-- Backfill issues 012–014. teaser for 014 → assignment for issue 015.
insert into issues (number, subject_line, character_name, character_year, pillar, page_url, until_next_teaser, status, published_at) values
  (12,
   'Your exhale controls whether your brain can calm down',
   'Otto Loewi', '1921', 'Surrender',
   'https://trybeezybeez.com/pages/breathing-vagus-nerve-sleep-technique',
   null,
   'published', null),
  (13,
   'The Cat that wasn''t supposed to move',
   'Michel Jouvet', '1959', 'Renewal',
   'https://trybeezybeez.com/pages/dreams-rem-sleep-emotional-processing',
   null,
   'published', null),
  (14,
   'The Night Yale broke the nightcap',
   'Richard B. Yules', '1966', 'Renewal',
   'https://trybeezybeez.com/pages/alcohol-sleep-architecture-rem-suppression',
   'the thing about dreams that your brain is doing on purpose — and why forgetting them might be the point.',
   'published', null)
on conflict (number) do update set
  subject_line = excluded.subject_line,
  character_name = excluded.character_name,
  character_year = excluded.character_year,
  pillar = excluded.pillar,
  page_url = excluded.page_url,
  until_next_teaser = excluded.until_next_teaser,
  status = excluded.status;
SQLEOF
echo "  ✓ db/migrations/003_issues_table.sql"

# ----- workers/prompts/hive_mind.md (replaces old) -----
cat > workers/prompts/hive_mind.md <<'PROMPTEOF'
# The Hive Mind Newsletter — Draft Generator

You draft issues of The Hive Mind, the sleep science newsletter for Beezy Beez Honey (trybeezybeez.com). Every issue follows the exact framework below. No exceptions.

Each call produces **TWO bodies** and the surrounding metadata:

1. **`long_form_body`** — the full issue (3,500–4,500 words) that lives on the Shopify page at `trybeezybeez.com/pages/<slug>`. This is what readers find when they click through from the email.
2. **`email_teaser_body`** — a 600–900 word teaser that becomes the Klaviyo email body. It opens the same way as the long-form, builds momentum, and **stops on a cliffhanger** before the mechanism is fully revealed — so the reader clicks through to finish.

The user message will be a JSON object containing:
- `target_issue_number` (int) — the issue number being drafted
- `previous_teaser` (string) — the "Until next issue" line from the most recent published issue. **This is the topic assignment for the issue you are drafting.** Honor it. The new issue must deliver on that teaser.
- `recent_issues` (array of `{number, character, character_year, pillar, topic_summary}`) — the most recently published issues. Do NOT repeat their characters, topics, or pillars unless the framing is genuinely fresh.
- `topic_override` (optional string) — if present, use this instead of `previous_teaser` as the topic.

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

## LONG-FORM BODY STRUCTURE — 3,500–4,500 WORDS

This is what lives on the Shopify page. It is a deep, immersive read — 14–18 minutes. Use plain text with markdown headers. No HTML.

```
# [Headline — one declarative sentence, counterintuitive or surprising]

🌙 [X]-minute read — written for [the hour before you try to sleep / the first quiet moment of your morning].

[OPENING — Duhigg-style. A specific person, a specific year, a specific place. 300–500 words. The character's story is rich, lived-in, with sensory detail. The reader is pulled in before any science is named.]

## [Section header — declarative statement; the mechanism begins to emerge]

[The character's discovery deepens. Historical context. The science emerging from the story. 500–700 words. End the section with a beat that propels the reader forward.]

## [Section header — the science deepens]

[Mechanism explained through concrete examples, named researchers, specific years, specific studies. Connect to one or more previous issues here. 500–700 words.]

## [Section header — a complication, counter-intuitive finding, or surprising consequence]

[The framework gets richer. The reader sees the mechanism from a new angle. Names another researcher, another year. 400–600 words.]

## [Section header — bridging from mechanism to reader experience]

[Sets up the personal section by translating the mechanism into something felt. 300–500 words.]

## Why this matters more after 50

[Map the mechanism to the aging body. The 3am wake. The night sweat. The sense that her sleep used to work and now doesn't. Cite age-related research with names and years. 400–600 words.]

## The one thing worth trying tonight

[One specific, doable action. Not a list. Not a protocol. One thing. Explain why this action specifically works on this mechanism. 250–400 words.]

---

**Until next issue**

Next: [teaser for the following issue — one sentence, creates curiosity]

Sleep well,
The Hive Mind
Brought to you by Beezy Beez Honey

*The honey we personally use to support these routines — trybeezybeez.com*
```

Total word count target: **3,500–4,500 words**. Aim for ~4,000. The reader has time. The depth is the point. Section word ranges above are guidance, not strict — distribute as the material requires.

## EMAIL TEASER BODY — 600–900 WORDS, ENDS ON CLIFFHANGER

This becomes the Klaviyo email body. Same opening as the long-form (the reader should feel continuity when she clicks through). Builds momentum into the mechanism. Then **STOPS** before the mechanism is fully resolved — at the most curiosity-loaded moment — followed by a single line CTA.

Structure:

```
[OPENING — same person, year, and place as the long-form opening. 250–400 words. Identical or very close phrasing through the opening 2 paragraphs is fine.]

[ONE more section that sets up the mechanism. Builds the question. 200–300 words.]

[Then the cliffhanger: the moment the reader is leaning in, where the discovery is about to land. One short paragraph or even one short sentence that promises the answer.]

**Continue reading on the page →**
```

Critical: the teaser must STOP BEFORE the mechanism is fully revealed. The reader should feel a pull. If you've explained the answer, you've gone too far. If she could close the email satisfied, you've failed.

After the cliffhanger and the CTA, do NOT include the "Until next issue" block, the closing signature, the testimonial, the editorial hubs, or the footer line. Those all live in the email shell that Klaviyo wraps around this body.

## What the Newsletter NEVER Does (Post Issue 006)

- Never includes a product offer, discount code, or Hive Club mention in the body
- Never positions honey as a solution to the issue's topic
- Never says "buy" or "shop" or "order"
- The footer line is the only product reference (and only in the long-form, not the teaser)

## Connecting Issues

Each long-form references 1–3 previous issues where natural. Use the `recent_issues` array in your context to pick relevant connections. Examples:
- "The circadian clock from Issue 001..."
- "The cortisol curve we covered in Issue 002..."
- "This is the switch from Issue 006. The one Walter Hess found."

The series builds a coherent system. Each issue adds a piece to a map the reader is assembling.

## Subject Line Formula

- Second-person, counterintuitive or surprising.
- Examples that worked:
  - "Your exhale controls whether your brain can calm down" (Issue 012)
  - "The Cat that wasn't supposed to move" (Issue 013)
  - "The Night Yale broke the nightcap" (Issue 014)
- The 48-hour follow-up subject is a genuinely different angle on the same issue, not a rephrasing.

## Page Slug (SEO)

Generate a slug for the Shopify page in the form `key-concept-1-key-concept-2-key-concept-3`. Examples:
- `breathing-vagus-nerve-sleep-technique` (Issue 012)
- `dreams-rem-sleep-emotional-processing` (Issue 013)
- `alcohol-sleep-architecture-rem-suppression` (Issue 014)

3–6 hyphenated words. Lowercase. SEO-friendly. Descriptive of the issue's central idea.

## SEO Title & Meta Description

- **`page_seo_title`**: under 60 chars, descriptive, ends with `| The Hive Mind`. Example: `Vagus Nerve and Sleep: Why Your Exhale Matters | The Hive Mind`
- **`page_meta_description`**: under 155 chars, includes the narrative hook and the core insight. Should make someone reading a Google result click.

## Cover Image Prompt

Generate a prompt for nano_banana_2, 16:9 aspect ratio. The image must:
- Connect to the reader emotionally
- Reference the historical figure, place, or moment from the opening when possible
- Use warm, candlelit, parchment-toned colors (amber, deep brown, soft gold)
- Avoid stock-photo aesthetics — editorial, almost painted quality
- Never include text overlays
- Never depict the brand product, just sleep / rest / nature / history

## Output Format

Return ONLY a JSON object — no markdown fences, no prose before or after. The object must have exactly these fields:

```
{
  "issue_number": <int>,
  "pillar": "Signal" | "Surrender" | "Renewal",
  "character": "<name of historical figure in opening>",
  "character_year": "<year referenced in opening, as string>",
  "character_location": "<city, country, or institution where opening is set>",
  "topic_summary": "<one-line summary, 6-10 words, of the issue's central mechanism>",
  "subject_line": "<email subject — second-person, counterintuitive, ~60-90 chars>",
  "subject_line_48h": "<different-angle subject for 48h follow-up send>",
  "preview_text": "<email preview text — 60-110 chars, creates curiosity>",
  "read_time_min": <int 14-18>,
  "page_slug": "<hyphenated SEO slug, 3-6 words, lowercase>",
  "page_seo_title": "<under 60 chars, ends with | The Hive Mind>",
  "page_meta_description": "<under 155 chars, narrative hook + core insight>",
  "long_form_body": "<full 3500-4500 word issue body, markdown headers, no HTML>",
  "email_teaser_body": "<600-900 word teaser ending on cliffhanger + 'Continue reading on the page →'>",
  "until_next_teaser": "<one-sentence teaser for next issue — this becomes the topic of issue N+1>",
  "cover_image_prompt": "<full nano_banana_2 prompt, 16:9, see cover image guidelines>",
  "testimonial_suggestion": "<short note on what kind of testimonial would pair best>",
  "previous_issues_referenced": [<list of integer issue numbers referenced in long_form_body>]
}
```

## Self-Check Before Returning

- [ ] Opens with a specific person, year, and place — not the topic
- [ ] Follows the 5-step Duhigg skeleton
- [ ] long_form_body is 3,500–4,500 words
- [ ] email_teaser_body is 600–900 words AND stops before the mechanism is revealed
- [ ] No bullet points or numbered lists anywhere in either body
- [ ] No product pitch in either body
- [ ] Footer line ("The honey we personally use…") appears in long_form_body only
- [ ] Subject line is second-person and counterintuitive
- [ ] References at least one previous issue (in `previous_issues_referenced`)
- [ ] "Why this matters more after 50" section present in long_form_body
- [ ] One actionable takeaway in long_form_body, not a list
- [ ] Page slug is SEO-friendly, 3-6 hyphenated words
- [ ] Honored the `previous_teaser` from context as the topic assignment
- [ ] Character/topic does not repeat any of `recent_issues`
- [ ] Output is valid JSON with all required fields

Return the JSON object now.
PROMPTEOF
echo "  ✓ workers/prompts/hive_mind.md (updated: 3,500-4,500 word target + dual output)"

# ----- workers/run.py (replaces old) -----
cat > workers/run.py <<'PYEOF'
"""
CLI for invoking Skill workers.

For hive_mind: reads the `issues` table to determine the next issue number and
the previous issue's teaser (the topic assignment), then drafts the issue,
inserts a row in `issues` with status='draft', and posts a Slack summary.

Examples:
    python -m workers.run --skill hive_mind                  # auto-detect next issue
    python -m workers.run --skill hive_mind --issue 15       # explicit issue number
    python -m workers.run --skill hive_mind --issue 15 --dry-run
    python -m workers.run --skill hive_mind --topic-override "Caffeine half-life"
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any, Optional

import psycopg

from config import DATABASE_URL
from lib.slack import notify_failure, post_draft
from workers.skill_runner import invoke_skill


# ---------- state helpers ----------

def _fetch_next_issue_state(conn: psycopg.Connection) -> dict[str, Any]:
    """Compute target_issue_number, previous_teaser, and recent_issues from the issues table."""
    cur = conn.execute(
        """
        select number, character_name, character_year, pillar, topic_summary, until_next_teaser, status
        from issues
        order by number desc
        limit 8
        """
    )
    rows = cur.fetchall()
    if not rows:
        return {"target_issue_number": 1, "previous_teaser": None, "recent_issues": []}

    top = rows[0]
    target = top[0] + 1
    previous_teaser = top[5]
    recent = [
        {
            "number": r[0],
            "character": r[1],
            "character_year": r[2],
            "pillar": r[3],
            "topic_summary": r[4],
        }
        for r in rows[:6]
    ]
    return {
        "target_issue_number": target,
        "previous_teaser": previous_teaser,
        "recent_issues": recent,
    }


def _insert_draft_issue(
    conn: psycopg.Connection,
    issue_data: dict[str, Any],
    run_id: str,
) -> None:
    """Insert a fresh draft row into issues. Overwrites if the issue number already exists in draft state."""
    long_form = issue_data.get("long_form_body") or ""
    email_teaser = issue_data.get("email_teaser_body") or ""
    long_form_wc = len(long_form.split())
    teaser_wc = len(email_teaser.split())

    conn.execute(
        """
        insert into issues (
            number, subject_line, subject_line_48h, preview_text,
            character_name, character_year, character_location, pillar,
            topic_summary, page_slug,
            cover_image_prompt, long_form_body, email_teaser_body,
            until_next_teaser, previous_issues_referenced,
            read_time_min, word_count_long_form, word_count_email_teaser,
            drafted_at, status, run_id
        ) values (
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s,
            %s, %s, %s,
            %s, %s,
            %s, %s, %s,
            now(), 'draft', %s
        )
        on conflict (number) do update set
            subject_line = excluded.subject_line,
            subject_line_48h = excluded.subject_line_48h,
            preview_text = excluded.preview_text,
            character_name = excluded.character_name,
            character_year = excluded.character_year,
            character_location = excluded.character_location,
            pillar = excluded.pillar,
            topic_summary = excluded.topic_summary,
            page_slug = excluded.page_slug,
            cover_image_prompt = excluded.cover_image_prompt,
            long_form_body = excluded.long_form_body,
            email_teaser_body = excluded.email_teaser_body,
            until_next_teaser = excluded.until_next_teaser,
            previous_issues_referenced = excluded.previous_issues_referenced,
            read_time_min = excluded.read_time_min,
            word_count_long_form = excluded.word_count_long_form,
            word_count_email_teaser = excluded.word_count_email_teaser,
            drafted_at = now(),
            status = 'draft',
            run_id = excluded.run_id
        where issues.status = 'draft'  -- never overwrite a scheduled or published issue
        """,
        (
            issue_data.get("issue_number"),
            issue_data.get("subject_line"),
            issue_data.get("subject_line_48h"),
            issue_data.get("preview_text"),
            issue_data.get("character"),
            issue_data.get("character_year"),
            issue_data.get("character_location"),
            issue_data.get("pillar"),
            issue_data.get("topic_summary"),
            issue_data.get("page_slug"),
            issue_data.get("cover_image_prompt"),
            long_form,
            email_teaser,
            issue_data.get("until_next_teaser"),
            issue_data.get("previous_issues_referenced") or [],
            issue_data.get("read_time_min"),
            long_form_wc,
            teaser_wc,
            uuid.UUID(run_id) if run_id else None,
        ),
    )


# ---------- slack rendering ----------

def _post_hive_mind_draft_to_slack(
    issue_data: dict[str, Any],
    run_id: str,
    cost_usd: float,
) -> None:
    """Post a structured summary + the email teaser body to Slack. Long-form body lives in the DB."""
    long_form = issue_data.get("long_form_body") or ""
    email_teaser = issue_data.get("email_teaser_body") or ""
    long_form_wc = len(long_form.split())
    teaser_wc = len(email_teaser.split())

    summary = [
        f"*Subject:* {issue_data.get('subject_line', '(none)')}",
        f"*48h follow-up:* {issue_data.get('subject_line_48h', '(none)')}",
        f"*Preview text:* {issue_data.get('preview_text', '(none)')}",
        f"*Read time:* {issue_data.get('read_time_min', '?')} min",
        f"*Long-form word count:* {long_form_wc:,}",
        f"*Email teaser word count:* {teaser_wc:,}",
    ]
    metadata = {
        "Issue": issue_data.get("issue_number"),
        "Pillar": issue_data.get("pillar"),
        "Character": f"{issue_data.get('character')} ({issue_data.get('character_year')})",
        "Location": issue_data.get("character_location"),
        "Page slug": issue_data.get("page_slug"),
        "Previous issues referenced": ", ".join(str(n) for n in (issue_data.get("previous_issues_referenced") or [])),
        "Run ID": run_id,
        "Cost": f"${cost_usd:.4f}",
    }

    sections: list[str] = [
        "*— Email teaser body (what goes in Klaviyo email) —*",
        email_teaser,
        "*— Until next issue (binding teaser for issue N+1) —*",
        issue_data.get("until_next_teaser") or "(none)",
        "*— Cover image prompt (for nano_banana_2, 16:9) —*",
        issue_data.get("cover_image_prompt") or "(none)",
        "*— Testimonial suggestion —*",
        issue_data.get("testimonial_suggestion") or "(none)",
        f"_Long-form body ({long_form_wc:,} words) is stored in the `issues` table. Query with:_ ```SELECT long_form_body FROM issues WHERE number = {issue_data.get('issue_number')};```",
    ]
    body = "\n\n".join(sections)

    post_draft(
        title=f"Hive Mind Issue {issue_data.get('issue_number', '?')} — draft",
        summary_lines=summary,
        body=body,
        metadata=metadata,
    )


# ---------- main ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Invoke a Skill worker.")
    parser.add_argument("--skill", required=True, help="Skill name (e.g. hive_mind)")
    parser.add_argument("--issue", type=int, help="Issue number to draft (default: auto-detect next from DB)")
    parser.add_argument("--topic-override", help="Override the previous_teaser as the topic")
    parser.add_argument("--pillar", choices=["Signal", "Surrender", "Renewal"], help="Pillar override")
    parser.add_argument("--model", help="Claude model override")
    parser.add_argument("--max-tokens", type=int, default=16384, help="Max output tokens (default 16384 for long-form)")
    parser.add_argument("--no-slack", action="store_true", help="Skip posting to Slack")
    parser.add_argument("--no-save", action="store_true", help="Don't write a draft row to issues table")
    parser.add_argument("--dry-run", action="store_true", help="Print output, don't post anywhere or save")
    args = parser.parse_args(argv)

    context: dict[str, Any] = {}
    state: dict[str, Any] = {}

    if args.skill == "hive_mind":
        with psycopg.connect(DATABASE_URL) as conn:
            state = _fetch_next_issue_state(conn)
        target_issue = args.issue or state["target_issue_number"]
        context = {
            "target_issue_number": target_issue,
            "previous_teaser": state["previous_teaser"],
            "recent_issues": state["recent_issues"],
        }
        if args.topic_override:
            context["topic_override"] = args.topic_override
        if args.pillar:
            context["pillar"] = args.pillar
    else:
        # Generic invocation for non-hive_mind skills
        if args.issue:
            context["issue_number"] = args.issue
        if args.topic_override:
            context["topic"] = args.topic_override
        if args.pillar:
            context["pillar"] = args.pillar

    kwargs: dict[str, Any] = {"max_tokens": args.max_tokens}
    if args.model:
        kwargs["model"] = args.model

    print(f"[run] invoking skill={args.skill}")
    print(f"[run] context={json.dumps(context, indent=2)[:500]}")

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

    # Save draft + post to slack
    if args.skill == "hive_mind" and result.output_json:
        issue_data = result.output_json
        if not args.no_save:
            try:
                with psycopg.connect(DATABASE_URL) as conn:
                    _insert_draft_issue(conn, issue_data, result.run_id)
                print(f"[run] saved draft to issues table (number={issue_data.get('issue_number')})")
            except Exception as e:
                print(f"[run] WARNING: failed to save draft to issues table: {e}", file=sys.stderr)
        if not args.no_slack:
            _post_hive_mind_draft_to_slack(issue_data, result.run_id, result.cost_usd)
            print("[run] posted Hive Mind draft to Slack")
    else:
        if not result.output_json:
            print("[run] WARNING: expected JSON output but parse failed; posting raw text", file=sys.stderr)
        if not args.no_slack:
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
echo "  ✓ workers/run.py (updated: state-aware + draft persistence)"

echo ""
echo "==> Files written."
echo ""
echo "Next steps:"
echo "  1) psql \"\$DATABASE_URL\" -f db/migrations/003_issues_table.sql"
echo "  2) python -m workers.run --skill hive_mind --dry-run    # auto-detects issue 15"
echo "  3) Review the printed JSON. If it looks right:"
echo "     python -m workers.run --skill hive_mind              # saves to DB + posts to Slack"
echo ""
