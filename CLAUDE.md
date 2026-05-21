# CLAUDE.md — beezy-agents

Project context for Claude Code. Read top to bottom before touching anything.
Last updated: 2026-05-19.

---

## What this is

`beezy-agents` is the automation pipeline behind **Beezy Beez Honey**
(trybeezybeez.com) — a DTC botanical-honey brand. It runs the email-marketing
operation: generates content, creates Klaviyo campaigns, publishes Shopify
pages, produces a sleep-audio podcast, and reports to Slack.

It is an **automation pipeline**, not an autonomous agent swarm. Deterministic
Python workers fired by a time-gated cron loop. This is deliberate — see
*Design philosophy* below. Do not "agent-ify" the plumbing.

- Host: Replit project `beezy-agents-ingestion`, working dir `~/workspace/`
- DB: Neon Postgres via `NEON_DATABASE_URL`
- Storage: Cloudflare R2
- Stack: Python / FastAPI
- Integrations: Klaviyo (REST), Shopify (Admin GraphQL), Higgsfield (REST), ElevenLabs, Slack

The operator is **Boris** (operates as "Alan @ Beezy Beez" in brand contexts).

---

## How to work here

- **Boris is the Board.** He approves decisions and direction. You execute.
- **You can edit files and run commands directly now** — that is the whole
  point of working in Claude Code. Do not ask Boris to copy-paste code or run
  shell commands you can run yourself. For read-only inspection, just do it.
  For writes, migrations, or deploys, show the plan/diff and confirm first.
- **Be concise and direct.** No padding, no preamble. Boris pushes back hard on
  anything vague or ungrounded. Claims about performance need data behind them.
- **Never invent Klaviyo numbers.** Revenue, open rates, segment sizes, RPR —
  pull them live (run the existing Klaviyo REST code in the repo) or say you
  don't have them. Never estimate, round, or guess.
- **Never invent products.** Only reference real products on trybeezybeez.com.
- **Verify, don't assume.** This codebase has a history of bugs from assumed
  state. Read the actual file / query the actual DB before acting.

---

## Design philosophy (do not violate)

Reliability comes from **determinism + observability**, not autonomy.

- Workers are deterministic scripts. They do exactly what their code says.
- LLM reasoning belongs in **content generation and strategic judgment only**
  (writing newsletter copy, interpreting performance, the Slack agent's NL
  parsing). Never in the plumbing.
- "Self-healing" = bounded retries with backoff. Never an LLM autonomously
  rewriting code or DB rows.
- The path to the "runs itself" vision is: durable job state → monitoring →
  dashboard → close known bugs → operator UI. Not an agent org chart.

Do NOT, without explicit instruction: migrate off Replit, introduce an agent
framework, change Slack channel IDs, or touch the live Shopify theme.

---

## Architecture

- The Replit **web deployment** runs FastAPI with two background loops:
  1. Slack agent — polls every ~5s
  2. Cron — time-gated (`if hour == H and minute == M:` style checks)
- The Replit **Scheduled Deployment is neutered** (`echo ok`). All real work
  happens in the web server loops.
- DB access: fresh connections with keepalives, **no pool** (prevents Neon
  timeouts during long Opus calls). Retry logic (3 attempts) is installed.
- The cron loop currently lives in `app/main.py` (recent work added a job at
  `h == 8 and m == 5`). `scripts/cron_dispatch.py` also exists as the historical
  entrypoint. **Before instrumenting the dispatcher, read both and confirm
  which one is the live loop.**

---

## Repo map

```
~/workspace/
├── app/main.py              FastAPI app + the two background loops (cron lives here)
├── agents/
│   ├── slack_agent.py       polls #beezy-agents + #beezy-new-episodes, NL via Sonnet
│   └── klaviyo_deployer.py  confirmed Klaviyo REST endpoints
├── db/migrations/           numbered SQL migrations, applied in order
├── ingestion/               shopify.py, klaviyo.py, sync.py
├── lib/
│   ├── slack.py             exports: post_draft, notify_failure, _post  (NO post_message)
│   ├── shopify_admin.py     exports: graphql
│   └── email_builder.py
├── pacing/
│   ├── brain.py             compute_pacing_state, active_goals
│   ├── calendar.py          monthly calendar generator (Opus + live RPR)
│   ├── calendar_live_data.py get_performance_by_segment  ← returns $0 from Replit (known bug)
│   ├── orchestrator.py      daily slot dispatch, approval gate
│   └── weekly_brief.py      Sunday digest
├── scripts/
│   ├── cron_dispatch.py     historical Scheduled Deployment entrypoint
│   └── dry_pipeline_test.py end-to-end dry test
└── workers/
    ├── run.py               Hive Mind issue generator  ← fixed this session
    ├── klaviyo_campaign.py  Hive Mind campaign creation (_create_page_for_issue @179)
    ├── publish_and_index.py page publish + index update  ← fixed this session
    ├── shopify_publisher.py create_page / update_page / upload_image_to_shopify
    ├── shopify_page_builder.py build_page_html (HTML only, no publish)
    ├── beezy_campaign.py    general campaign pipeline
    ├── seo_blog.py          SEO blog worker
    └── image_gen.py         Higgsfield REST
```

`shopify_publisher.create_page()` defaults `is_published=False` — pages are
created **hidden** and published later on send day.

---

## Database (Neon — exact column names matter)

- **jobs** — *does not exist yet; building it is the next task. See below.*
- **issues** — Hive Mind issues. Columns include: `number`, `status`,
  `page_title`, `page_dek`, `page_breadcrumb_label`, `page_slug`,
  `long_form_body`, `until_next_teaser`, `read_time_min`, `cover_image_url`,
  `shopify_image_id`, `shopify_image_url`, `preview_text`, `buzzsprout_url`,
  `scheduled_send_at`, plus Klaviyo IDs.
- **decisions** — `id, decided_by, decision_type, input_context, reasoning,
  output, created_at`.  NEVER `agent`, `decision_text`, `evidence`.
- **strategies** — `id, component, strategy_text, approved_by, approved_at,
  is_active, created_at`.  The column is `component`, never `agent`.
- **calendar_executions** — `id, decision_id, slot_date, content_type,
  audience, topic_angle, status, notes, executed_at, actual_revenue,
  klaviyo_campaign_id, recipients, actual_rpr`.
  Status: `dispatched | completed | failed | skipped`.
- **calendar_approvals** — `week_start` (DATE PK), `token, approved_at,
  approved_by`. Always query by range:
  `WHERE week_start <= %s AND %s < week_start + INTERVAL '7 days'`.
- **agent_state** — `key` (TEXT PK), `value, updated_at`.
- **performance** — has `is_preliminary BOOLEAN`, `finalized_at TIMESTAMPTZ`.
  Learning loop reads only `WHERE is_preliminary = false`.

---

## Cron schedule

All gating logic lives in `_run_cron_jobs(now)` in `app/main.py` (times are ET).
"Claim gate" = wrapped in `_try_claim_today("<key>")`, an atomic
`agent_state` INSERT…ON CONFLICT that lets the job run **once per calendar
day** and survive restarts / catch-up windows without double-running. There is
only ONE running instance (the Replit web server): it wins the claim on the
first eligible tick of the day and loses it on every later tick (the stored
value already equals today). It is restart/catch-up safety, not multi-instance
arbitration — there is no second instance. "none" = fires unconditionally
every tick its time gate matches.

| Time gate (ET) | Job | Entry point | Claim gate |
|---|---|---|---|
| every ~5s | Slack agent poll | `agents.slack_agent.run_once` | n/a — not a tracked job |
| `h%4==0, m<2` (every 4h) | ingestion sync | `ingestion.sync.run_shopify_sync` + `run_klaviyo_sync` | none |
| `(h==7,m>=30) or h==8` | pacing brain | `pacing.cron.run_daily` | `cron_pacing_brain` |
| `h==7, m==35` | pacing cache refresh | `workers.pacing_cache.refresh_pacing_cache` | none |
| `h==7, m==40` | audience health | `workers.audience_health.run_audience_health` | `cron_audience_health` |
| `h==8 or (h==9,m==0)` | orchestrator (sole campaign dispatcher) | `pacing.orchestrator.run_daily` | `cron_orchestrator` |
| `h==8, m==5` | morning brief | `workers.morning_brief.run_morning_brief` | `cron_morning_brief` |
| `h==8, m==5` | publish + index | `workers.publish_and_index.run` | `cron_publish_and_index` |
| `h==9, m==0` | revenue backfill | `workers.revenue_backfill.run_backfill` | none |
| `day==last_day-7, h==9, m==0` | next month's calendar | `pacing.calendar.run_monthly` | none |
| `day==15, h==9, m==30` | bi-weekly pacing check | `workers.learning_loop.run_biweekly` | none |
| `day==1, h==9, m==30` | monthly retrospective | `workers.learning_loop.run_monthly` | none |
| `Mon, h==9, m==30` | approval nudge | `pacing.weekly_brief.run_approval_nudge` | none |
| `h==10, m==0` | Hive Mind auto-campaign | `workers.klaviyo_campaign.auto_create_pending` | none |
| `h==10, m==30` | deliverability check | `workers.deliverability_monitor.run_deliverability_check` | none |
| `Sun, h==21, m==0` | weekly learning review | `workers.learning_loop.run_weekly` | none |
| `Sun, h==21, m==5` | weekly approval brief | `pacing.weekly_brief.run_weekly_brief` | none |
| `h==21, m==10` | Hive Mind status sync | `workers.hive_mind_status_sync.sync_sent_campaigns` | none |
| `Sun, h==21, m==15` | flow health check | `workers.flow_monitor.run_flow_check` | none |
| `m%5==0` (every 5 min) | pending campaign auto-schedule | `workers.beezy_campaign.check_pending_schedules` | none — acts only if pending work exists |
| `m%5==0` (every 5 min) | TTS timeout watchdog | `workers.sleep_audio_producer.check_tts_timeouts` | none — acts only if timeouts exist |

20 discrete jobs. 5 are claim-gated (`pacing_brain`, `audience_health`,
`orchestrator`, `morning_brief`, `publish_and_index`); the other 15 fire
unconditionally on their time gate. The two `m%5==0` watchdogs run every 5 min
but usually no-op — they only do work when there is pending/timed-out work.

### Watchdog expected-schedule spec (Step 4 source of truth)

Canonical `job_name` → time gate → pattern mapping. The `job_name` is the
`jobs` table join key. The Step 4 watchdog builds its expected-schedule dict
**from this table — do not re-derive from code.** Pattern decides what a
missing run means: `time` jobs should always have a `succeeded` run within
their cadence + grace; `claim` jobs the same — the single instance wins the
claim once per day and writes exactly one row, so overdue logic for them is
identical to daily `time` (a lost claim is just a later tick of the same
instance, never a missing row); `watchdog` jobs write a row only when they had
work, so absence is normal and must NOT alarm — only a `failed`/`running`-stuck
row for them is actionable.

| `job_name` | pattern | time gate (ET) |
|---|---|---|
| `ingestion_sync` | time | `h%4==0, m<2` (every 4h) |
| `cron_pacing_brain` | claim | `(h==7,m>=30) or h==8` |
| `cron_orchestrator` | claim | `h==8 or (h==9,m==0)` |
| `cron_audience_health` | claim | `h==7, m==40` |
| `pacing_cache_refresh` | time | `h==7, m==35` |
| `cron_morning_brief` | claim | `h==8, m==5` |
| `cron_publish_and_index` | claim | `h==8, m==5` |
| `revenue_backfill` | time | `h==9, m==0` |
| `calendar_generation` | time | `day==last_day-7, h==9, m==0` |
| `learning_loop_biweekly` | time | `day==15, h==9, m==30` |
| `learning_loop_monthly` | time | `day==1, h==9, m==30` |
| `approval_nudge` | time | `Mon, h==9, m==30` |
| `hive_mind_campaign` | time | `h==10, m==0` |
| `deliverability_check` | time | `h==10, m==30` |
| `learning_loop_weekly` | time | `Sun, h==21, m==0` |
| `weekly_brief` | time | `Sun, h==21, m==5` |
| `hive_mind_status_sync` | time | `h==21, m==10` |
| `flow_monitor` | time | `Sun, h==21, m==15` |
| `pending_schedules` | watchdog | `m%5==0` (row only when work) |
| `tts_timeout_watchdog` | watchdog | `m%5==0` (row only when work) |

20 rows: 5 `claim`, 2 `watchdog`, 13 `time`. All `trigger='cron'`.

---

## Hard rules (each one is here because violating it caused a real bug)

1. `#beezy-agents` channel = `C0B3DEUJS9G`. NEVER `C0B3S0CM2JV` (that is
   `#beezy-new-episodes`). Verify after any change touching channel IDs.
2. Revenue / performance numbers come from **live Klaviyo pulls only**.
3. The content calendar must cover every day from today through month end.
4. Product copy may reference **only** real products on trybeezybeez.com.
5. Klaviyo personalization syntax is context-specific and NOT interchangeable:
   - Subject line: `{{ first_name }}` — this format only.
   - Body / HTML: `{{ person.first_name|default:'there' }}` — this format only.
6. CTA rule: never send existing customers to a landing page. Link direct to
   product/collection, or `trybeezybeez.com/discount/CODE?redirect=/pages/bf-collection`.
   Landing pages are for prospects (education) only.

---

## Anti-patterns — all confirmed from production bugs

**Python / path**
- Always `cd ~/workspace` first; run Python with
  `python3 -c "import sys; sys.path.insert(0,'.'); ..."`.
- `cron_dispatch.py` must have the `sys.path.insert` at the very top.
- Never `import calendar` in `pacing/__init__.py` — collides with stdlib.

**Klaviyo REST**
- Field names use underscores, never hyphens (`use_smart_sending`, not
  `use-smart-sending`) — hyphens 400.
- `editor_type` must be `"CODE"`, never `"DRAG_DROP"`.
- Don't POST `body` in campaign message content — not a valid field.
- Don't POST `template_id` in the campaign definition — not a valid field.
- Assign template via `POST /api/campaign-message-assign-template/` with
  `type: "campaign-message"`. Do not use `/assign-template/` paths.
- Don't use `mcp_servers` with the Python anthropic SDK — not supported.

**Slack**
- `lib/slack.py` has `post_draft`, `notify_failure`, `_post`. There is no
  `post_message` — do not call it.

**Calendar**
- All 7 days are valid send days — no weekend blackout.
- Never let Opus invent revenue — pass live RPR as hard constraints.
- SEO blog slots: `revenue_estimate = 0` always.

**Higgsfield**
- Never use the `higgsfield-client` SDK — stale. Use REST at
  `https://platform.higgsfield.ai`, auth `Authorization: Key {KEY}:{SECRET}`.

---

## Current state — May 2026

Fixed and deployed this session (Hive Mind pipeline):

- **Issue-numbering bug.** The `issues` table had `14,15,16,17,020` — a gap at
  18/19 caused by a stray `020` row. `run.py` now uses **first-gap numbering**:
  it walks issue numbers from the bottom and targets the first gap
  (`last_contiguous + 1`), so it self-heals. Next generated issue = **19**.
- `run.py` also now populates `scheduled_send_at` on insert and **refuses an
  explicit `--issue N` beyond `max+1`** (the gap guard — this is what stops
  another orphan row).
- `fix_issues_scheduling.py` was run once: backfilled `scheduled_send_at` on
  all issues, backfilled Issue 016's cover image, registered Issue 018.
- `publish_and_index.py` now **publishes the issue's Shopify page on its send
  day** (`isPublished:true`), then updates the index pages. Pages are created
  hidden and go live the morning the email sends.
- Issue 018 is registered and scheduled (sends May 27). Its page is visible
  early as a one-off; the hidden-until-send-day rule applies to Issue 019+.

Known open bugs (not yet fixed):

- `pacing/calendar_live_data.py::get_performance_by_segment` returns **$0**
  from Replit — the calendar revenue pull is broken; calendar is semi-manual.
- Klaviyo campaigns sometimes land in **"Queued without Recipients"** — root
  cause unknown, can silently kill a send.
- **Flow/campaign revenue split** target is 70/30; currently inverted (~47/53)
  and nothing monitors it.
- Sleep-audio deploy is still a manual Slack command (`deploy latest episode`).

Control plane — Steps 1–3b DONE and deployed:

- **Step 1** `db/migrations/013_jobs_control_plane.sql` applied to Neon
  (`jobs` table, verified: 10 cols, 3 indexes, 2 CHECK constraints).
- **Step 2** `lib/jobs.py` — `run_job` context manager (telemetry never gates
  plumbing; finalize is total/swallowed; failed re-raises).
- **Step 3** `_run_cron_jobs` in `app/main.py`: **all 20 dispatch sites
  instrumented** — 13 time-gated wrapped directly, 5 claim-gated (claim check
  stays outside, `run_job` entered only if claim won), 2 five-min watchdogs.
  Verified live: real `succeeded` + `failed` rows with `duration_ms` and
  traceback; behavior unchanged (failed still re-raises into the existing
  per-job `except`).
- **Step 3b CLOSED** — the 2 watchdogs (`pending_schedules`,
  `tts_timeout_watchdog`) now predicate-gated: pure side-effect-free
  `_pending_schedules_due()` / `_tts_candidates_due()` decide whether to enter
  `run_job`, so idle ticks write no row. Predicate and actor share one
  threshold constant each (`PENDING_SCHEDULE_AGE_MIN=60`,
  `TTS_TIMEOUT_AGE_MIN=30`) — cannot drift. Actors now return `bool`
  (`job.detail = {"acted": ...}`).
- **Step 4 DONE** — `workers/watchdog.py`. Hourly (`m==0`) `run_job("watchdog")`
  line in `_run_cron_jobs`. Checks every job in `EXPECTED_SCHEDULE` (20, exact
  `job_name` strings) for overdue / failed / stuck; ONE consolidated digest
  per tick via `lib.slack._post` with a job-attributed header (`🔴 Watchdog —
  N jobs need attention`) — `notify_failure` is reserved for an actual
  watchdog crash. Daily heartbeat via `post_draft`, dispatcher-gated to the
  11:00 ET tick with a `_try_claim_today("watchdog_heartbeat")` restart belt;
  a missing heartbeat is itself the alarm. Overdue skipped for `watchdog`-
  pattern jobs and bootstrap-guarded (no alarms until the control plane has
  been online longer than a job's cadence+grace). `watchdog` is deliberately
  NOT in `EXPECTED_SCHEDULE` — it does not monitor itself.

The control plane (Steps 1–4) is complete. Next on the roadmap: dashboard /
the known-bug closes (revenue pull, Queued-without-Recipients, flow split).

---

## >>> IMMEDIATE NEXT TASK: build the control plane

The system has no single source of truth for "what ran, when, did it work."
Job state is scattered across tables and in-memory vars lost on restart. Build
it in four steps, in order. Confirm the plan with Boris before each write step.

### Step 1 — the `jobs` table

```sql
-- db/migrations/0NN_jobs_control_plane.sql  (use the next free migration number)
CREATE TABLE IF NOT EXISTS jobs (
    id           BIGSERIAL    PRIMARY KEY,
    job_name     TEXT         NOT NULL,
    status       TEXT         NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running','succeeded','failed','skipped')),
    trigger      TEXT         NOT NULL DEFAULT 'cron'
                  CHECK (trigger IN ('cron','slack','manual','watchdog')),
    attempt      INTEGER      NOT NULL DEFAULT 1,
    started_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    duration_ms  INTEGER,
    detail       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_name_started ON jobs (job_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_started      ON jobs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_running      ON jobs (started_at) WHERE status = 'running';
```

A row is born `running` and dies `succeeded`, `failed` (with `error`), or
`skipped` (intentional no-op — e.g. orchestrator with nothing approved, so the
watchdog does not false-alarm). `detail` holds the job's result, e.g.
`{"issue": 19, "campaign_id": "..."}`. Track only discrete jobs — NOT the Slack
5s poll.

### Step 2 — `lib/jobs.py`, the `run_job` wrapper

A context manager every job flows through:

```python
with run_job("publish_and_index", trigger="cron") as job:
    job.detail = {"issues_published": cron_publish_and_index()}
```

- enter → INSERT the `running` row
- clean exit → UPDATE `succeeded` (set `finished_at`, `duration_ms`, `detail`)
- exception → UPDATE `failed` with the error text, **then re-raise** (behavior
  unchanged — the job still fails, it is just also recorded)
- `job.skip("reason")` → `skipped`

### Step 3 — wire the dispatcher (NOT the workers)

Instrument the **cron loop** (in `app/main.py` — verify) so every job dispatch
is wrapped in `run_job(...)`. One file changes; all ~10 jobs become tracked
without touching any worker. Do not add `run_job` inside individual workers.

### Step 4 — `workers/watchdog.py`

Runs on a cron tick. Holds an expected-schedule dict
(`publish_and_index` daily ~08:05, `ingestion_sync` every 4h, `weekly_brief`
Sun 21:00, etc.). For each job, alert Slack on any of:

1. **Overdue** — no `succeeded` run within cadence + a grace window
2. **Failed** — most recent run is `failed`
3. **Stuck** — a row has been `running` past a sane max duration

Alerts via `lib/slack.py` (`notify_failure` for problems). Add the watchdog to
the cron loop as part of Step 3.

---

## Roadmap (after the control plane)

1. Control plane (current task)
2. Watchdog + alerting (Step 4)
3. Dashboard — the "control room" so the system is visible
4. Close known bugs: revenue pull, Queued-without-Recipients, flow monitoring
5. Everything reachable from Slack/UI → operator-handoff-ready
6. Productization (multi-tenant) — only after Beezy runs untouched for weeks

---

## Key identifiers

- **Slack:** `#beezy-agents` `C0B3DEUJS9G` · `#beezy-new-episodes` `C0B3S0CM2JV`
- **Klaviyo company:** `W8SW8k` · Placed Order metric: `X93gjq`
- **Klaviyo segments:** super_engaged `Sme9Nq`, engaged_prospects `Xrp3ha`,
  vip `RArtzN`, active_seal `UBFUcH`, whales `VAUD58`, engaged_customers
  `RvtHdn`, lapsed_30d `UEQD6k`, lapsed_60d `UfARWm`, lapsed_90d `XuS7rY`,
  lapsed_180d `W98qh3`. Hive Mind list `Y6VSre`. ALL CUSTOMERS (exclude) `XFSxZt`.
- **Shopify pages (GIDs):** the-hive-mind archive `132665803001`,
  sleep-science-hub `132548198649`, meditation-library `132583031033`.
- **Hive Mind cadence:** every 3 days. Anchor: Issue 014 = 2026-05-15.
  → 014 May15 · 015 May18 · 016 May21 · 017 May24 · 018 May27 · 019 May30.

---

## Running things

- Always `cd ~/workspace` first.
- Migrations: numbered SQL in `db/migrations/`, applied in order. Make them
  idempotent (`CREATE TABLE IF NOT EXISTS`).
- "Deploy" = the file is in `~/workspace`; the Replit web server picks it up.
  There is no separate build step.
- Dry-test the pipeline with `scripts/dry_pipeline_test.py` before live runs.
- Env vars available: `NEON_DATABASE_URL`, `BEEZY_ANTHROPIC_API_KEY`,
  `KLAVIYO_API_KEY`, `KLAVIYO_FROM_EMAIL`, `SHOPIFY_ACCESS_TOKEN`,
  `SHOPIFY_SHOP_DOMAIN`, `HIGGSFIELD_API_KEY`, `HIGGSFIELD_SECRET`,
  `SLACK_BOT_TOKEN`, `SLACK_WEBHOOK_URL`, `NEW_EPISODES_CHANNEL_ID`,
  `REPLIT_DOMAIN`.

Optional: the deeper operating manuals (beezy-agents-system, beezy-system,
hive-mind-newsletter, etc.) exist as skills in the claude.ai environment. To
give future Claude Code sessions that depth, copy them into `.claude/skills/`.
