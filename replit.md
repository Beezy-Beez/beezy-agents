# Beezy Agents — Multi-Agent Marketing Operations

Orchestration brain + performance feedback layer for Beezy Beez (trybeezybeez.com). Runs autonomous Klaviyo email campaigns, SMS drafts, SEO blog posts, flow health monitoring, and revenue-paced content calendars.

## Run & Operate

### Backend (FastAPI — port 8080)
```
uvicorn app.main:app --host 0.0.0.0 --port 8080
```
The Replit Autoscale deployment runs this automatically. Includes Slack polling (5s) and all cron jobs in background loops.

### Dashboard (Next.js — port 3000)
```
cd dashboard && npm run dev        # dev
cd dashboard && npm run build && npm start   # production
```
Points to `NEXT_PUBLIC_API_URL` (default: `http://localhost:8080`).

### One-off jobs
```bash
# Apply DB migrations
psql "$NEON_DATABASE_URL" -f db/schema.sql

# Historical data backfill
python -m workers.klaviyo_backfill --month 2026-05

# Generate calendar manually
python3 -c "import sys; sys.path.insert(0,'.'); from pacing.calendar import run_monthly; run_monthly()"

# Run ingestion manually
python -m ingestion.sync all --lookback-days 7
```

## Stack

- **Backend:** Python 3.11 + FastAPI + uvicorn (Replit Autoscale)
- **DB:** Neon Postgres (psycopg3) — schema in `db/schema.sql`, 11 incremental migrations in `db/migrations/`
- **Dashboard:** Next.js 14 App Router, TypeScript, Tailwind CSS, SWR, Recharts
- **Integrations:** Klaviyo REST API, Shopify Admin GraphQL, Anthropic API, Slack Web API, Higgsfield REST, Buzzsprout

## Where things live

| Path | Purpose |
|---|---|
| `app/main.py` | FastAPI server, cron loop, Slack interactive handler |
| `app/dashboard.py` | FastAPI HTML dashboard (fallback, legacy) |
| `dashboard/` | Next.js admin dashboard (primary UI) |
| `pacing/` | Revenue brain: calendar generator, orchestrator, weekly brief |
| `workers/` | One worker per content type + validator, learning loop, etc. |
| `agents/` | Slack agent, Klaviyo deployer |
| `ingestion/` | Shopify + Klaviyo ingestion sync |
| `lib/` | Email builders, Slack helpers, Shopify admin client |
| `db/` | Schema, migrations, connection pool |
| `config.py` | All env vars + `KLAVIYO_REVISION` constant |

## Architecture decisions

- **Single web process owns all cron.** No separate scheduled deployment — all cron jobs run in `app/main.py`'s `_cron_loop` background task. Avoids cold-start race conditions.
- **Postgres is the queue.** `calendar_executions` table is the source of truth. No Redis/RabbitMQ.
- **Approval gates autonomy.** Calendar → Slack summary → Boris types `approved week` → orchestrator runs. No autonomous publishing without a human in the loop.
- **72h attribution window.** `calendar_executions.is_preliminary=true` until `revenue_backfill` finalizes. Learning loop only reads finalized rows.
- **Klaviyo revision centralized.** All API calls use `config.KLAVIYO_REVISION`. Bump in one place.

## Cron schedule (all ET)

| Time | Job |
|---|---|
| Every 4h | Shopify + Klaviyo ingestion |
| 7:30am daily | Pacing brain → Slack |
| 7:35am daily | Pacing cache refresh (Klaviyo MTD) |
| 8:00am daily | Orchestrator — dispatch today's calendar slots |
| 9:00am daily | Revenue backfill (72h attribution) |
| 10:00am daily | Hive Mind campaign auto-create |
| 9pm Sunday | Weekly learning loop + approval brief |
| 9:15pm Sunday | Flow health check |
| 9:30am Monday | Approval nudge (if week not yet approved) |
| 9:30am 15th | Mid-month pacing check |
| 9:30am 1st | Monthly retrospective + RPR update |
| 9am 7 days pre-month-end | Calendar generation (next month) |

## Required environment variables (Replit Secrets)

`NEON_DATABASE_URL`, `BEEZY_ANTHROPIC_API_KEY`, `KLAVIYO_API_KEY`, `KLAVIYO_FROM_EMAIL`, `SHOPIFY_SHOP_DOMAIN`, `SHOPIFY_ACCESS_TOKEN`, `HIGGSFIELD_KEY`, `HIGGSFIELD_SECRET`, `SLACK_BOT_TOKEN`, `SLACK_WEBHOOK_URL`, `REPLIT_DOMAIN`

## Gotchas

- **Never source `.env` via `set -a; . .env; set +a`** — leaks env values to bash job control. Use Replit Secrets.
- **R2 (7-day audience cooldown) is non-negotiable.** Hard-coded auto-fail in validator. Never bypass.
- **Validator warnings block campaigns.** WARN = FAIL until the validator matures.
- **`ingestion/klaviyo.py` uses `KLAVIYO_API_REVISION`** (local alias for `config.KLAVIYO_REVISION`) — do not change the local alias name; it's used throughout the file.
- **`calendar_approvals` uses range query.** Never exact date match: `week_start <= today < week_start + 7 days`.
- **Test helpers must not call `conn.commit()`** — use `conn.rollback()` in teardown. See `tests/test_validator.py` for the pattern.
- **BEEZY_AGENTS_CHANNEL = `C0B3DEUJS9G`** (not `C0B3S0CM2JV` which is #beezy-new-episodes). A startup assert in `agents/slack_agent.py` catches misconfigurations.
- **Higgsfield: use REST directly**, not `higgsfield-client` SDK (stale/broken).
- **Zipify Pages**: product pages are built in Zipify, not Shopify templates. Don't overwrite with standard Shopify mutations.
