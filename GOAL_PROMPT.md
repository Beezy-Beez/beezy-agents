# Beezy Agents — Complete Build-Out Goal Prompt

**Purpose:** This document is a complete, self-contained execution prompt for building out everything that remains in the Beezy Agents system. Paste this into a new Claude Code session to continue the build. Read CLAUDE.md first for full system context.

---

## Who This Is For

You are building an autonomous email marketing system for Beezy Beez Honey. The end user is a new business owner with zero email marketing experience. The system must be simple enough that they can run it successfully with two weekly actions:

1. **Sunday:** Read the weekly plan in Slack or the dashboard → type `approved week`
2. **Monday–Saturday:** Glance at the dashboard to confirm things are running

Everything else is autonomous. The system generates calendars, writes copy, creates campaigns, schedules sends, monitors flows, learns from results, and adjusts. The human's only job is to say "yes, run it."

**The metaphor:** A light switch. The owner flips it on. They don't know how electricity works. The lights come on anyway — and they're bright.

**The standard:** Not just hitting $150K/month. Exceeding it. Every month. Getting better every month because the system learns from its own data.

---

## Start Here: Read the Full Context

Before writing a single line of code, read these files:
1. `CLAUDE.md` — complete system spec, architecture, all rules, all anti-patterns, product catalog, Klaviyo endpoints, segment IDs
2. `app/main.py` — the server, all 11 cron jobs, the Slack interactive endpoint
3. `app/dashboard.py` — the current read-only dashboard (what you're replacing/upgrading)
4. `workers/validator.py` — all 17 rules that gate every campaign send
5. `pacing/orchestrator.py` — how daily calendar slots get dispatched
6. `agents/slack_agent.py` — how Boris communicates with the system today

---

## What's Already Built (Do Not Rebuild)

Everything in this list works in production. Do not touch it unless a task below requires wiring it:

- **Layer 6 (Ingestion):** `ingestion/shopify.py`, `ingestion/klaviyo.py`, `ingestion/sync.py` — Shopify + Klaviyo data pulled every 4h, deduped, written to Neon
- **Layer 1 (Pacing math):** `pacing/brain.py` — gap%, required daily rate, top contributors
- **Layer 2 (Orchestrator):** `pacing/cron.py`, `pacing/orchestrator.py`, `pacing/weekly_brief.py`
- **Calendar generation:** `pacing/calendar.py` — Opus-driven, live RPR data, 7 days before month-end
- **All workers:** `beezy_campaign.py`, `klaviyo_campaign.py`, `seo_blog.py`, `sms_campaign.py`, `flow_monitor.py`, `learning_loop.py`, `revenue_backfill.py`, `pacing_cache.py`, `auto_schedule.py` (campaigns schedule and send automatically — not just draft)
- **Validator:** `workers/validator.py` — 12 structural rules + 5 content checks
- **Slack agent:** `agents/slack_agent.py` — Boris types commands, Claude interprets and acts
- **Email HTML:** `lib/email_builder.py`, `workers/shopify_page_builder.py`
- **All integrations:** Klaviyo REST, Shopify GraphQL, Higgsfield image gen, Neon Postgres

---

## What Needs to Be Built — Prioritized

### PRIORITY 1: Interactive Dashboard (The Control Center)
**Why first:** This is the primary interface for the new owner. Everything else flows through it.

**What to build:**

Replace `app/dashboard.py` with a significantly upgraded version. Keep it as FastAPI + server-rendered HTML (no separate frontend build step — must run on Replit with zero config). The current dashboard is read-only. The new one is the control center.

**Required sections:**

**A. Revenue Command Center (top strip)**
- Giant MTD revenue number vs $150K goal with % and status (AHEAD / ON TRACK / BEHIND)
- SVG arc gauge that fills green when on track, amber when <80%, red when <50%
- Revenue forecast line: "At current daily rate → projected end of month: $X" (use: daily rate × days remaining + current)
- Campaigns vs flows revenue split
- Daily rate needed for rest of month
- Days remaining

**B. Today's Agenda (prominent, top)**
- Every slot scheduled for today: content type, audience, topic, send time, status
- Status badges: `scheduled` (blue), `sent` (green), `failed` (red), `blocked` (orange)
- For any `failed` or `blocked` slot: show a Retry button (POST to `/api/retry-slot?id=X`)
- If no slots today: show "Rest day — next send: [date]"

**C. Approval Center (the most important section for new owner)**
- Show clearly: "Week of [date] — APPROVED ✓" or "Week of [date] — PENDING APPROVAL ⚠️"
- If pending: show a big `Approve This Week` button (POST to `/api/approve-week`)
- Show calendar month approval status too
- Show how many slots are queued, how many will auto-run once approved
- Never make the owner dig into Slack just to approve

**D. 7-Day Calendar Preview**
- Table: date, content type, audience, topic, send time, estimated revenue, status
- Color code by content type
- Show cumulative projected revenue for the week
- Each row: if `failed`, show a Retry link

**E. Audience Health Panel**
- Table: audience name, last send date, days since last touch, 90d RPR, 90d sends, health status
- Health status: `FRESH` (>14d since last touch), `WARM` (7–14d), `RECENT` (<7d → cooldown applies)
- Sort by revenue opportunity (RPR × estimated list size)
- This tells the new owner which audiences are ready to send to

**F. Flow Health (compact)**
- All tracked flows: name, 30d revenue, RPR, type, benchmark, status (OK / UNDERPERFORMING / BROKEN)
- Red flag on anything below benchmark
- "Fix queued" badge if flow_monitor has already generated a template fix waiting for apply

**G. Top Performers (motivating)**
- "Your best campaigns ever" — top 10 by actual_revenue from calendar_executions
- Audience, content type, actual revenue, RPR, send date
- This teaches the new owner what works without them having to ask

**H. Learning Loop Status**
- Last weekly review: date + summary line
- Last monthly retro: date + summary line
- RPR trend by segment: sparkline or simple ↑↓ arrows (30d vs 90d)

**Implementation rules:**
- Single Python file at `app/dashboard.py`, FastAPI router
- All data from Neon Postgres — never make API calls from the dashboard
- Auto-refresh every 5 minutes (`<meta http-equiv="refresh" content="300">`)
- Mobile responsive — new owner may check on phone
- Brand colors: `#8b4513` primary, `#faf6ee` background, `#2c2417` text, `#d4a847` accent
- Font: DM Sans + DM Serif Display (already used in current dashboard)
- No JavaScript frameworks. Vanilla JS only if needed for button clicks.
- Approval buttons must use `<form method="POST">` — no JS required to click them

**New FastAPI endpoints to add to `app/main.py`:**

```python
POST /api/approve-week      # writes calendar_approvals, same logic as "approved week" Slack command
POST /api/approve-month     # writes decisions approval flag
POST /api/retry-slot        # re-queues a failed calendar_executions row (set status='pending', orchestrator picks it up next run)
GET  /api/status            # JSON: today's slots, approval status, pacing snapshot — for dashboard polling
```

---

### PRIORITY 2: Morning Briefing (The Daily Notification)
**Why second:** This is how the new owner starts their day. It's the only Slack message they need to read. Everything else is autonomous.

**What to build:** A new cron job at **8:05am ET daily** (after orchestrator runs at 8am) that posts a structured Slack message to `#beezy-agents`.

**Message format:**

```
🌅 *Good morning. Here's today.*

💰 *Revenue MTD:* $X / $150K (XX%)
📈 *Pace:* [AHEAD by $X | ON TRACK | BEHIND $X — need $X/day]

📧 *Today's sends:*
  • [content_type] → [audience] at [time] — est. $X
  • [content_type] → [audience] at [time] — est. $X
  [OR: Rest day — batteries recharging.]

⚠️ *Action needed:* [or "Nothing — system is running."]
  • Week of May 19 needs approval → type `approved week`
  [OR list any failed slots that need retry]

📊 *Dashboard:* https://[replit-domain]/dashboard
```

**Rules:**
- If no action needed: say so explicitly. "Nothing — all systems running."
- If week is already approved and no failures: entire "action needed" section is hidden
- Keep it under 15 lines. Operators skim Slack.
- Never dump full calendar into the briefing — just today's slots + the link
- Add to `app/main.py` cron at `h == 8 and m == 5`
- Function: `workers/morning_brief.py:run_morning_brief()`

**Also build:** A **Sunday evening preview** (already partially done by `weekly_brief.py`) that shows next week's slots and has a big CTA: "Reply `approved week` to run it." Ensure this message is friendly and non-technical.

---

### PRIORITY 3: Phase 2B — Priority Brain
**Why third:** Right now the orchestrator blindly runs whatever's in the calendar. The brain should decide *daily* whether to adjust based on pacing.

**What to build:**

Add to `pacing/brain.py` a new function: `compute_daily_priorities(as_of=None) -> dict`

This function:
1. Reads all active goals + computes pacing state
2. Reads today's calendar slots
3. Makes a structured decision:
   - If **BEHIND by >20%**: flag `mode='boost'` — today's orchestrator should add an unscheduled high-RPR slot if any exist, or increase frequency for the week
   - If **BEHIND by 5–20%**: flag `mode='push'` — stay the course but prioritize high-RPR slots first
   - If **ON TRACK (±5%)**: flag `mode='maintain'`
   - If **AHEAD by >5%**: flag `mode='ease'` — can skip lowest-RPR slots if already at daily cadence limit

4. Writes a row to `decisions` table: `decision_type='daily_priority'`, `output={'mode': X, 'reasoning': '...', 'recommended_actions': [...]}`
5. Writes a row to `priorities` table: `effective_for=today`, `prioritized_workers=[...]`, `pacing_snapshot={...}`

The orchestrator (`pacing/orchestrator.py`) reads `priorities` for today before dispatching. If `mode='boost'` and pacing is behind, it picks the highest-RPR segment from `calendar_live_data` that hasn't been sent to in 7+ days and queues an additional slot.

The morning briefing reads the `decisions` row to include "Today's focus: BOOST MODE — adding extra VIP send."

---

### PRIORITY 4: Campaign Preview + Cancel Window
**Why fourth:** Before anything goes live to Klaviyo, the new owner should have a 60-minute window to see what's about to send and cancel it if something looks wrong.

**What to build:**

Add a preview step to `workers/beezy_campaign.py` between validation and scheduling:

1. After validator PASSES and campaign is created in Klaviyo (but BEFORE `schedule_campaign()` is called)
2. Post a Slack preview message to `#beezy-agents`:

```
👁️ *Campaign Preview — ready to schedule*

📧 Subject: [subject line]
👥 Audience: [audience name] (~[list_size] recipients)
🕐 Scheduled: [send_time] ET
💰 Est. revenue: $[X]

[Preview first 2 sentences of email body...]

✅ Auto-schedules in 60 minutes unless you cancel.
❌ Type `cancel [campaign_id]` to abort.
Klaviyo: https://www.klaviyo.com/campaign/[id]/edit
```

3. Store the campaign in a `pending_schedule` state (add a flag to calendar_executions or agent_state)
4. The `_run_cron_jobs` loop checks every minute: if a pending campaign is >60 minutes old and not cancelled → call `schedule_campaign()`
5. Add cancel handling to `agents/slack_agent.py`: if message contains `cancel [id]` → delete the Klaviyo campaign, mark slot as `cancelled`

For the **new owner experience**: if they don't understand or don't care, campaigns just send automatically after 60 minutes. They never need to act. The preview is there *if they want it*, not required.

---

### PRIORITY 5: "Boost Mode" Button
**Why fifth:** When revenue is behind, the new owner needs one button that says "help" and the system responds intelligently.

**Add to the dashboard** (in the Revenue Command Center, visible only when pacing is BEHIND):

```
🔴 You're behind pace by $X. 
[Boost Revenue Now]  ← big button
```

Clicking it calls `POST /api/boost` which:
1. Reads top-3 highest-RPR audience/content_type combos from `calendar_executions` (90d, `is_preliminary=false`)
2. Filters to those that haven't been sent to in 7+ days (R2 compliant)
3. Creates a new calendar slot for today or tomorrow with the top candidate
4. Runs the full campaign pipeline (copy → image → Klaviyo → schedule)
5. Posts result to Slack: "Boost activated. Added [audience] send for [time] — est. $X."

This is the "help I'm behind, do something smart" button. The system picks the best option based on real data.

---

### PRIORITY 6: Segment Freshness + Audience Health Monitoring

**What to build in `workers/audience_health.py`:**

A new daily job (run at 7:40am ET, after pacing cache) that:
1. For every audience in `CUSTOMER_SEGMENTS` + `PROSPECT_SEGMENTS`
2. Computes: days since last send, 30d RPR, 90d RPR, trend (↑/↓), estimated list size
3. Flags `STALE` if >21 days since last touch AND audience has RPR > $0.10 (money being left on the table)
4. Flags `AT_RISK` if 90d RPR was $0.20+ but 30d RPR dropped below $0.10 (segment degrading)
5. Writes summary to `agent_state` key `'audience_health'`
6. If any `STALE` flags: posts a Slack alert — "💤 [audience] hasn't been sent to in X days and averages $Y/send. Calendar has a gap."

The dashboard reads `audience_health` from `agent_state` to populate the **Audience Health Panel**.

---

### PRIORITY 7: Content Strategy Attribution

**What to build in `pacing/brain.py`** (new function: `content_strategy_attribution(days=90)`):

Groups `calendar_executions` by topic theme/pillar and computes:
- Sleep science content: avg RPR, total revenue, # sends
- Product/offer content: avg RPR, total revenue, # sends
- Story/narrative content: avg RPR, total revenue, # sends

Heuristic: classify by `topic_angle` keywords:
- Sleep science: "sleep", "science", "research", "study", "brain", "REM", "cortisol", "melatonin"
- Product/offer: "% off", "discount", "bundle", "deal", "save", "code", "limited"
- Story/narrative: "years ago", "discovered", "story", "one night", "her name", "he noticed"

Surface this in the dashboard as: "What works best: Sleep Science ($X/send) | Product ($X/send) | Story ($X/send)"

Feed this into calendar generation system prompt: "Historical data shows sleep science content drives $X/send for this audience — weight toward it."

---

### PRIORITY 8: A/B Subject Line Testing

**What to add to `workers/beezy_campaign.py`** for segments with list size >3,000:

1. Generate 2 subject lines (Anthropic call with: "Give me 2 different subject line options for this campaign. Different approaches: one curiosity-based, one benefit-based.")
2. Create 2 Klaviyo campaign messages with different subjects (same template, same send time)
3. Set `send_options.use_smart_sending: false` (already done)
4. After 72h attribution: `revenue_backfill.py` picks the winner by RPR
5. Store winner in `agent_state` key `'subject_patterns'` as running log
6. Feed winner patterns into future copy generation: "Past winner pattern: [X]. Use this approach."

This makes the system smarter every single send without any human involvement.

---

### PRIORITY 9: Tests for Critical Paths

Zero test coverage is the biggest operational risk. Build tests for the two highest-risk components:

**`tests/test_validator.py`:**
- Test each of the 17 rules individually (R1–R12, C1–C5)
- Test that R2 (7-day cooldown) auto-fails and blocks
- Test that C5 (`/collections/all`) auto-fails
- Test that HIGH_VALUE_SEGMENTS + discount language → C3 fail
- Test that `validate_campaign` PASS returns `{"pass": True, "verdict": "PASS"}`
- Use a real Postgres connection (not mocks — per project rules) with a test database populated with fixture data

**`tests/test_pacing.py`:**
- Test `compute_pacing_state` with known data (seed a goal + performance rows, verify gap_pct math)
- Test `_period_to_date_revenue` dedup logic (two rows for same order_id → only latest counted)
- Test status classification (ahead/on-track/behind) thresholds

**`tests/test_ingestion.py`:**
- Test `to_performance_rows` converts Shopify order data to correct metric rows
- Test dedup: same order_id with different updated_at → latest wins

Use `pytest`. Add `pytest` to `requirements.txt`. Run via `python -m pytest tests/ -v`.

---

### PRIORITY 10: Cleanup

**Remove dead code:**
- Delete `app/slack.py` — both endpoints raise `NotImplementedError`. The real handler is in `app/main.py POST /api/slack/interactive`. This file is a trap.

**Fix the `decisions` table gap:**
- After pacing brain runs daily, write a `daily_priority` row to `decisions` (from Priority 3 above). This makes the table actually used.

**Add `revenue_estimate` to calendar slots from Slack conversational edits:**
- When the Slack agent creates/modifies a slot, it should always compute `revenue_estimate` using the live RPR × list size formula, not leave it null. Null `revenue_estimate` means R6 (revenue floor) is always skipped.

---

## What Success Looks Like

**For the new owner:**
1. System emails 3–5x/week autonomously
2. Owner's only required action: `approved week` once per week in Slack
3. Dashboard tells them: are we on track? What's running today? Do I need to do anything?
4. Morning Slack briefing answers the same question before they even open the dashboard
5. If they go on vacation for 2 weeks: the system continues, generates next month's calendar, runs it, reports results. Nothing breaks.

**For revenue:**
1. Month 1: Hit $150K or within 10%
2. Month 2: Exceed $150K — learning loop is feeding better RPR data into calendar
3. Month 3+: Consistently exceed because A/B testing is finding winning subject patterns and segment health monitoring is catching degrading audiences before they drag revenue down

**For the system:**
1. Every campaign that goes out has passed all 17 validator rules
2. Every audience has had at least 7 days since last touch (R2 never violated)
3. Revenue backfill populates actual RPR within 72h of every send
4. Learning loop reads real data and improves next month's calendar
5. `/healthz` returns 200 at all times

---

## Technical Rules (Do Not Violate)

All of these come from hard-won production bugs documented in CLAUDE.md:

1. **Data rule:** Never present Beezy revenue numbers without pulling them live from Klaviyo or Shopify. No estimates. No memory.
2. **Klaviyo API:** All calls use revision `2025-10-15`. Field names use underscores, never hyphens. `editor_type: "CODE"` required for templates.
3. **Template assign endpoint:** `POST /api/campaign-message-assign-template/` — not a sub-path, not PATCH, `type: "campaign-message"` (not `type: "campaign-message-assign-template"`).
4. **Shopify:** CTA URLs are `/pages/bf-collection` or `/discount/CODE?redirect=/pages/bf-collection`. Never `/collections/all`.
5. **Segment IDs:** Use the confirmed IDs in CLAUDE.md. Never invent new segment IDs.
6. **Product catalog:** Only reference the locked product list. Never invent products.
7. **R2 is absolute:** 7-day audience cooldown. Never bypass, never make exceptions.
8. **Postgres only:** No Redis, no queues. Postgres is the queue.
9. **No env leaking:** Never `set -a; . .env; set +a`. Read secrets from environment variables or `python-dotenv`.
10. **No import `calendar` in `pacing/__init__.py`:** Circular import. Only import `brain` and `cron` from there.
11. **`decisions.component` not `decisions.agent`:** Wrong column name caused silent failures.
12. **Approval check is a range query:** `week_start <= today < week_start + 7 days`. Never exact date match.
13. **`_already_ran` must check `status != 'failed'`:** Failed slots must retry.
14. **Higgsfield:** Use REST at `https://platform.higgsfield.ai`, never the `higgsfield-client` Python SDK.

---

## Build Order (Execute in This Sequence)

Execute sequentially. Each item builds on the previous. Do not jump ahead.

```
[ ] P1-A  New dashboard — Revenue Command Center + Today's Agenda + Approval buttons
[ ] P1-B  New dashboard — Audience Health Panel + Flow Health + Top Performers
[ ] P1-C  FastAPI endpoints: /api/approve-week, /api/retry-slot, /api/status
[ ] P2-A  Morning briefing worker (workers/morning_brief.py)
[ ] P2-B  Wire morning brief into app/main.py cron at 8:05am
[ ] P2-C  Update Sunday weekly_brief.py to be friendlier for new owners
[ ] P3-A  Phase 2B brain: compute_daily_priorities() in pacing/brain.py
[ ] P3-B  Wire priorities table write into pacing/cron.py
[ ] P3-C  Orchestrator reads priorities table before dispatching
[ ] P4    Campaign preview + 60-minute cancel window
[ ] P5    /api/boost endpoint + Boost button on dashboard
[ ] P6    workers/audience_health.py + agent_state write + dashboard panel
[ ] P7    Content strategy attribution in pacing/brain.py
[ ] P8    A/B subject line testing in beezy_campaign.py
[ ] P9-A  tests/test_validator.py
[ ] P9-B  tests/test_pacing.py
[ ] P9-C  tests/test_ingestion.py
[ ] P10-A Delete app/slack.py
[ ] P10-B Ensure all Slack conversational slot edits include revenue_estimate
```

---

## The Deliverable

When this is complete, a person who has never run an email marketing campaign opens a browser, sees the dashboard, reads:

- **"Month: $47,200 / $150,000 — 31%. You need $3,840/day. 17 days left."**
- **"Today: 1 campaign scheduled — lapsed_30d at 2pm. Est. $967."**
- **"Week of May 19: PENDING APPROVAL"** → they click `Approve This Week`
- **"Nothing else needed. System is running."**

They click the button. They close the browser. They check back tomorrow. The morning Slack message tells them yesterday's campaign sent, made $1,100, system is on pace. They reply nothing, because nothing is needed.

That is the product. Build it.
