# Beezy Multi-Agent System

Orchestration brain + performance feedback layer for an existing Beezy Beez content production pipeline. We are NOT rebuilding what already works — we are building the missing layers above it.

## What already exists (do not rebuild — invoke via Anthropic API)
- **Hive Mind newsletter** — `hive-mind-newsletter` Skill, currently at Issue 009
- **Sleep audio podcast** — separate `sleep-audio-platform` project, do not touch
- **SEO blog content** — `seo-copywriter` Skill
- **Klaviyo integration with 12-rule validator + Slack approval** — `beezy-system` Skill
- **Zipify Pages publishing** — `zipify-page-deploy` and `beezy-sleep-story-page` Skills
- **Buzzsprout** — podcast hosting

## What this codebase builds

Layer 1 — Strategic brain
- `pacing/brain.py` — daily revenue-vs-target check → "what should we prioritize today?"
- `pacing/calendar.py` — monthly content plan, data-driven

Layer 2 — Orchestrator
- `pacing/cron.py` — reads priorities, invokes the right Skill at the right time

Layer 6 — Performance ingestion (every 4h)
- `ingestion/klaviyo.py` — campaigns, flows, opens, clicks, conversions, revenue
- `ingestion/shopify.py` — orders, attributed revenue
- `ingestion/sync.py` — orchestrator, dedupe, write to Postgres

Layer 7 — State store (this codebase) + dashboard (separate Next.js project, later)

## Skill invocation pattern
Workers don't contain content logic. They invoke existing Skills via the Anthropic API:
- `workers/skill_runner.py` — generic invoker
- `workers/prompts/<skill>.md` — per-Skill system prompts mirroring the actual Skills
- Cron decides which Skill to invoke, passes context; API call returns produced artifacts + metadata

## Stack
- Python + FastAPI on Replit
- Neon Postgres for state
- Slack webhooks for approvals (Tier 2) and digests (Tier 1)
- Anthropic API for Skill invocation
- Klaviyo + Shopify APIs for ingestion

## Autonomy tiers
- Tier 1 (full auto): ingestion, pacing brain, content drafts to artifact form
- Tier 2 (approve-once): publishing — Klaviyo sends, Zipify page publishes
- Tier 3 (notify-only): anything ToS-risky

## Non-negotiables
- Don't rebuild existing Skills. Always invoke via Anthropic API.
- Postgres is the queue. No Redis, no RabbitMQ.
- Pacing brain does NOT publish. It decides priorities; cron invokes Skills; Slack approval gates publish.
- Strategy updates go through Slack approval (the learning loop), not autonomous prompt rewriting.
- Never source .env via `set -a; . .env; set +a` or similar — bash job-control output leaks env values. Read DATABASE_URL and any secrets from environment variables (set by Replit Secrets), or read .env via python-dotenv inside Python code. For psql calls, use `psql "$DATABASE_URL"` directly when DATABASE_URL is already an env var.

## Operations

### Ingestion cadence — Replit Scheduled Deployment
- **Deployment type:** Scheduled Deployment (one of Replit's deployment options alongside Autoscale and Reserved VM).
- **Schedule (cron, UTC):** `0 */4 * * *` — runs at the top of the hour every 4 hours (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC).
- **Run command:** `python -m ingestion.sync all` — pulls Shopify orders + Klaviyo campaign/flow reports sequentially, using each source's last-successful-window-end as the cursor.
- **Required env / Replit Secrets:** `DATABASE_URL`, `SHOPIFY_ACCESS_TOKEN`, `SHOPIFY_SHOP_DOMAIN`, `KLAVIYO_API_KEY`. Optional: `SLACK_WEBHOOK_URL` (failure alerts).
- **How to provision:** In the Replit UI, *Deploy → New deployment → Scheduled*, then set the cron above and run command, and point it at this repo's environment. Replit Secrets propagate automatically.
- **One-off backfills:** run `python -m ingestion.sync all --lookback-days N` from a Replit shell; production cadence is unaffected since this bypasses the cursor for that invocation only.

### Daily pacing snapshot — Replit Scheduled Deployment
- **Deployment type:** Scheduled Deployment (second deployment, separate from ingestion).
- **Schedule (cron, UTC):** `0 13 * * *` — daily at 13:00 UTC. That's 9 AM EDT during US daylight saving (Mar–Nov) and 8 AM EST otherwise; if you need a stable 8 AM ET year-round, accept the 1h DST drift or split into two seasonal entries.
- **Run command:** `python -m pacing.cron daily` — computes pacing_state for every active goal, writes a `pacing_state` row, and posts a Block Kit digest to Slack. Add `--dry-run` to skip the POST (writes still happen).
- **Depends on:** the 4h ingestion cron above. The digest reads `performance` directly; if ingestion has stalled, `/healthz` will be 503 and the digest will reflect stale data.
- **Required env / Replit Secrets:** `DATABASE_URL`, `SLACK_WEBHOOK_URL`. (No Shopify/Klaviyo creds needed — this job only reads Postgres.)

### Failure alerts
- `ingestion/sync.py` POSTs a brief message to `SLACK_WEBHOOK_URL` whenever a sync returns any non-success status (covers `error`, `failed`, `partial`). Success runs are silent — that's noise.
- If `SLACK_WEBHOOK_URL` is unset, the helper logs a warning and proceeds; local runs are unaffected.

### Health monitoring
- `GET /healthz` (FastAPI app) returns latest-success timestamps and `age_hours` for each monitored source. Responds **503** if any source's `age_hours` exceeds `STALE_THRESHOLD_HOURS = 6` (i.e. at least one missed 4h tick), otherwise 200.

## Decisions

- **Shopify `order_revenue` = `currentTotalPriceSet` (net, post-refund).** Use this for revenue-vs-target pacing — it's the only field that reflects refunds, cancellations, and adjustments after the order is placed. `totalPriceSet` is frozen at order creation and would over-state revenue.
- **Shopify `gross_sales` = `subtotalPriceSet` (pre-discount, pre-shipping, pre-tax).** Use for demand-side analysis (what customers tried to buy before promo/shipping); not for revenue pacing.
- **Shopify orders filtered by `updated_at`, not `created_at`.** Refunds, cancellations, financial-status changes, and other mutations re-surface the order in a later window so we capture the updated `currentTotalPriceSet`. Downstream readers must dedupe by `dimensions->>'order_id'` and take the latest row per `(order_id, metric_name)` (e.g. by `measured_at DESC` on `performance`).
- **Shopify rows carry `dimensions.created_at`** (ISO string of `OrderRecord.created_at`). Pacing/period queries should scope by `(dimensions->>'created_at')::timestamptz`, not by `measured_at`, so a goal period reflects orders *placed* in that window rather than orders *ingested* in it. Legacy rows pre-dating this dimension are filtered out implicitly by the cast.

## Build phases
1. Performance ingestion (Layer 6) — first. Without this the pacing brain is flying blind.
2. Pacing brain v0 (Layer 1) — reads ingested data, computes pacing_state, decides daily priorities, posts to Slack.
3. Skill runner + first orchestrated worker — wire Hive Mind end-to-end.
4. Calendar generator — monthly planning.
5. Other workers one at a time — sleep audio, SEO blog, campaign emails, SMS, flow tuning.
6. Dashboard — separate Next.js project.
7. Learning loop — weekly retros (needs ~4 weeks of ingestion data first).
