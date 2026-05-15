# Beezy Multi-Agent System

Orchestration brain + performance feedback layer for an existing Beezy Beez content production pipeline. We are NOT rebuilding what already works — we are building the missing layers above it.

**Replit project:** `beezy-agents-ingestion` | **Working directory:** `~/workspace/`

---

## 0. ABSOLUTE DATA RULE — NO EXCEPTIONS

**Never present, analyze, or recommend based on any Beezy Beez data without first pulling it live from Klaviyo or Shopify.**

This covers: revenue, RPR, segment sizes, list sizes, open rates, campaign performance, flow performance, order counts, AOV, product performance, audience freshness, pacing projections, calendar estimates — everything.

No guessing. No estimating. No memory-based numbers. No fallback values presented as facts. Pull first, show the data, then speak.

If data cannot be pulled (tool error, doesn't exist), say so explicitly. Do not fill the gap with estimates.

Invented numbers — even conservative ones — produce calendars that are 15–80x off reality and make every downstream decision wrong. The cost of a tool call is zero. The cost of an invented number is the entire system's credibility.

---

## 0b. Locked State (May 2026)

**May 2026 real revenue (Klaviyo May 1–14):**
- Campaigns: $14,417.72 | Flows: $12,717.63 | Attributed: $27,135.35 | Store: $51,733.04

**Send frequency strategy (data-driven):**
- **HIGH FREQ:** Sleep audio ($1,065/send), VIP ($930/send), lapsed_30d ($923/send), Active Seal ($503/send), Hive Mind (pipeline owns this)
- **MODERATE:** OTB ($664/send), Whales/Pre-paid ($607/send), High-AOV ($734/send) — 2–3x/month
- **LOW:** Lapsed 90–180d+ ($190/send) — MAX 1x/month. No gummies-to-niche or cold blasts.
- **Iteration rule:** If pacing behind, increase VIP/lapsed_30d/active_seal first — never lapsed_90–180d+.
- Quality over quantity. Fewer sends, higher revenue per send.

**Content schedule (confirmed):** Hive Mind every 3 days from Issue 014 (May 15, prospects only, EXCLUDE customers, 8pm). Sleep audio every 3 days offset 1 day (seal 8:15pm + customers 8pm). Pre-paid 2–3x/month. SMS 2–3x/week. All 7 days valid — never skip weekends.

**Known issues:** _(none currently)_

---

## What already exists (do not rebuild — invoke via Anthropic API)

- **Hive Mind newsletter** — `hive-mind-newsletter` Skill, currently at Issue 014 (next is 015)
- **Sleep audio podcast** — separate `sleep-audio-platform` project, do not touch
- **SEO blog content** — `seo-copywriter` Skill
- **Klaviyo integration with 12-rule validator + Slack approval** — `beezy-system` Skill
- **Zipify Pages publishing** — `zipify-page-deploy` and `beezy-sleep-story-page` Skills
- **Buzzsprout** — podcast hosting

---

## What this codebase builds

**Layer 1 — Strategic brain**
- `pacing/brain.py` — daily revenue-vs-target check → pacing math, top contributors
- `pacing/calendar.py` — monthly content plan, Opus-driven, data-backed

**Layer 2 — Orchestrator**
- `pacing/cron.py` — daily pacing snapshot → Slack digest
- `pacing/orchestrator.py` — reads calendar, dispatches today's slots to workers
- `pacing/weekly_brief.py` — Sunday approval request; gates the orchestrator
- `workers/learning_loop.py` — weekly/biweekly/monthly self-correction

**Layer 6 — Performance ingestion (every 4h)**
- `ingestion/klaviyo.py` — campaigns, flows, opens, clicks, conversions, revenue
- `ingestion/shopify.py` — orders, attributed revenue
- `ingestion/sync.py` — orchestrator, dedupe, write to Postgres

**Layer 7 — State store + dashboard**
- Neon Postgres (schema below)
- `app/dashboard.py` — FastAPI HTML dashboard (placeholder; Next.js later)

---

## Skill invocation pattern

Workers don't contain content logic. They invoke existing Skills via the Anthropic API:
- `workers/skill_runner.py` — generic invoker; loads prompt from `workers/prompts/<skill>.md`
- `workers/prompts/` — 6 skill prompts: `campaign_email`, `flow_tuning`, `hive_mind`, `seo_blog`, `sleep_audio`, `sms`
- Cron decides which Skill to invoke, passes context; API call returns produced artifacts + metadata

---

## Stack

- Python 3.11 + FastAPI on Replit (Autoscale deployment)
- Neon Postgres for state
- Slack Web API + webhooks for approvals (Tier 2) and digests (Tier 1)
- Anthropic API for Skill invocation
- Klaviyo API (revision `2025-10-15`) for ingestion, campaign creation, flow management
- Shopify Admin GraphQL API (version `2025-10`) for orders, pages, images

---

## Autonomy tiers

- **Tier 1 (full auto):** ingestion, pacing brain, email campaign creation to Klaviyo draft, SEO blog publish, sleep audio (script → image → page → campaigns; Boris only feeds script to sleep-audio-platform for TTS)
- **Tier 2 (approve-once):** calendar week approval gates orchestrator; SMS draft to Slack for Boris review; flow fix via Slack button
- **Tier 3 (notify-only):** flow experiments (Boris implements manually in Klaviyo)

---

## Non-negotiables

- Don't rebuild existing Skills. Always invoke via Anthropic API.
- Postgres is the queue. No Redis, no RabbitMQ.
- Pacing brain does NOT publish. It decides priorities; cron invokes workers; Slack approval gates publishing.
- Strategy updates go through Slack approval (the learning loop), not autonomous prompt rewriting.
- Never source `.env` via `set -a; . .env; set +a` or similar — bash job-control output leaks env values. Read secrets from environment variables (Replit Secrets) or via `python-dotenv` inside Python. For psql calls, use `psql "$DATABASE_URL"` directly when `DATABASE_URL` is already set.
- `R2` (7-day audience cooldown) is non-negotiable and always auto-fails. Never bypass it.
- Validator warnings block campaigns the same as failures until the validator matures.
- Never skip weekends in calendar generation — all 7 days are valid send days.
- Never invent products not in the product catalog (Section below).
- Never present Beezy data without pulling it live first (Section 0).

---

## Cron jobs (all times ET, via `app/main.py` `_cron_loop`)

The server runs two async background loops: a 5s Slack poller (`_slack_loop`) and a 60s cron checker (`_cron_loop`). All cron work is synchronous and runs in a thread executor.

| Time | Day | Job | Module |
|---|---|---|---|
| Every 4h (0,4,8,12,16,20) | Daily | Shopify + Klaviyo ingestion sync | `ingestion.sync` |
| 7:30am | Daily | Pacing brain snapshot → Slack | `pacing.cron.run_daily` |
| 7:35am | Daily | Pacing cache refresh (Klaviyo MTD) | `workers.pacing_cache.refresh_pacing_cache` |
| 7:40am | Daily | Audience health monitor (STALE alert) | `workers.audience_health.run_audience_health` |
| 8:00am | Daily | Orchestrator — dispatch today's slots | `pacing.orchestrator.run_daily` |
| 8:05am | Daily | Morning brief → Slack daily digest | `workers.morning_brief.run_morning_brief` |
| 9:00am | Daily | Revenue backfill (72h attribution window) | `workers.revenue_backfill.run_backfill` |
| 10:00am | Daily | Hive Mind campaign auto-create (pending issues) | `workers.klaviyo_campaign.auto_create_pending` |
| 10:30am | Daily | Deliverability check — bounce/spam/unsub rates vs thresholds | `workers.deliverability_monitor.run_deliverability_check` |
| 9:00pm | Sunday | Weekly performance review + adjustments | `workers.learning_loop.run_weekly` |
| 9:05pm | Sunday | Weekly approval brief (next 7 days to Slack) | `pacing.weekly_brief.run_weekly_brief` |
| 9:15pm | Sunday | Flow health check + fix suggestions | `workers.flow_monitor.run_flow_check` |
| 9:30am | Monday | Approval nudge if this week still not approved | `pacing.weekly_brief.run_approval_nudge` |
| 9:30am | 15th | Mid-month pacing check | `workers.learning_loop.run_biweekly` |
| 9:30am | 1st | Monthly retrospective + RPR table update | `workers.learning_loop.run_monthly` |
| 9am | 7 days before month-end | Calendar generation (next month) | `pacing.calendar.run_monthly` |

**Dependency chain:** Ingestion → Pacing snapshot → Cache refresh → Audience health → Orchestrator → Morning brief → Revenue backfill

**NOTE:** The `scripts/cron_dispatch.py` Scheduled Deployment is neutered (`echo ok`). All jobs run in the web server background loop. If ever needed, `cron_dispatch.py` must start with:
```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```
Without this, `from agents.slack_agent import run_once` fails silently when the cron process starts from a different working directory.

---

## Database schema

All tables live in Neon Postgres. `schema.sql` is the base; `db/migrations/` holds 11 incremental migrations. `db/migrations.py` applies `schema.sql`; incremental migrations must be run manually via psql.

### Base tables (schema.sql)

**`goals`** — revenue targets
- `id uuid pk`, `title text`, `target_metric text`, `target_value numeric`, `period_start date`, `period_end date`, `status text default 'active'`, `created_at`, `updated_at`

**`pacing_state`** — daily snapshot per goal
- `id uuid pk`, `goal_id uuid → goals`, `measured_at timestamptz`, `period_to_date_value numeric`, `target_to_date_value numeric`, `gap_pct numeric`, `days_remaining int`, `required_daily_rate numeric`
- Index: `(goal_id, measured_at)`

**`priorities`** — daily priority decisions (Phase 2B, written by `pacing.brain.compute_daily_priorities`, read by `pacing.orchestrator._today_priority_mode`)
- `id uuid pk`, `decided_at`, `effective_for date`, `prioritized_workers jsonb`, `reasoning text`, `pacing_snapshot jsonb`
- Index: `effective_for`

**`runs`** — skill invocation log
- `id uuid pk`, `worker text`, `triggered_by text`, `priority_id → priorities`, `status text`, `autonomy_tier smallint`, `approval_status text`, `input jsonb`, `output jsonb`, `attempts int`, `max_attempts int`, `error text`, `created_at`, `started_at`, `completed_at`
- Extra columns (migration 002): `skill`, `model`, `context jsonb`, `output_text`, `output_json`, `input_tokens int`, `output_tokens int`, `cost_usd numeric`, `elapsed_seconds numeric`
- Indexes: `(status, worker)`, `priority_id`

**`ingestion_runs`** — sync cursor
- `id uuid pk`, `source text`, `window_start timestamptz`, `window_end timestamptz`, `records_ingested int`, `status text`, `error text`, `created_at`
- Index: `(source, created_at)`

**`performance`** — all Shopify + Klaviyo metrics
- `id uuid pk`, `run_id → runs`, `source text`, `metric_name text`, `metric_value numeric`, `dimensions jsonb`, `window_start`, `window_end`, `measured_at`
- Extra columns (migration 007): `is_preliminary boolean default true`, `finalized_at timestamptz`
- Finalization SQL (run every ingestion pass): `UPDATE performance SET is_preliminary = false, finalized_at = NOW() WHERE is_preliminary = true AND measured_at < NOW() - INTERVAL '72 hours'`
- Indexes: `(run_id, metric_name)`, `(source, measured_at)`, `(metric_name, measured_at)`, `(metric_name, measured_at) WHERE is_preliminary = false`

**`decisions`** — calendar plans + LLM decisions
- `id uuid pk`, `decided_by text default 'pacing_brain'`, `decision_type text`, `input_context jsonb`, `reasoning text`, `output jsonb`, `created_at`
- Index: `(decision_type, created_at)`
- Key value: `decision_type = 'calendar_plan'` → `output` contains `{"month": "YYYY-MM", "slots": [...]}`
- **Column names:** `component` (NOT `agent`), `strategy_text` (NOT `decision_text`)

**`strategies`** — approved strategy text + monthly RPR updates
- `id uuid pk`, `component text`, `strategy_text text`, `approved_by text`, `approved_at`, `supersedes_id → strategies`, `evidence_decision_id → decisions`, `is_active boolean default false`, `created_at`
- Index: `(component, is_active)`
- **Column name:** `component` (NOT `agent`)
- Monthly RPR update stored as: `component='learning_loop'`, `strategy_text=json` containing `rpr_by_audience` dict

### Migration-added tables

**`issues`** (migrations 003 + 004 + 005) — Hive Mind newsletter issues
- Core: `number int pk`, `subject_line`, `subject_line_48h`, `preview_text`, `character_name`, `character_year`, `character_location`, `pillar (Signal|Surrender|Renewal)`, `topic_summary`, `page_url`, `page_slug`, `cover_image_url`, `cover_image_prompt`, `email_template_id`, `campaign_id`, `long_form_body`, `email_teaser_body`, `until_next_teaser`, `previous_issues_referenced int[]`, `read_time_min`, `word_count_long_form`, `word_count_email_teaser`, `drafted_at`, `scheduled_send_at`, `published_at`, `status (draft|scheduled|published)`, `run_id uuid`, `notes`
- Page columns (004): `page_title`, `page_dek`, `page_breadcrumb_label`, `shopify_image_id`, `shopify_image_url`, `shopify_page_id`, `shopify_page_handle`, `shopify_page_url`, `page_published_at`
- Klaviyo columns (005): `klaviyo_campaign_id`, `klaviyo_template_id`, `klaviyo_message_id`, `campaign_drafted_at`
- Seeded: Issues 012 (Otto Loewi/breathing), 013 (Michel Jouvet/dreams), 014 (Richard B. Yules/alcohol+sleep)

**`calendar_executions`** (migrations 005 + 009 + 011) — slot execution log
- Core: `id uuid pk`, `decision_id uuid`, `slot_date date`, `content_type text`, `audience text`, `topic_angle text`, `status text default 'dispatched'`, `notes text`, `executed_at`
- Revenue tracking (009): `actual_revenue numeric(10,2)`, `klaviyo_campaign_id text`, `recipients int`, `actual_rpr numeric(8,4)`
- Attribution (011): `is_preliminary boolean`, `finalized_at timestamptz`
  - `is_preliminary = NULL` → predates backfill, treat as unfinalized
  - `is_preliminary = true` → within 72h window
  - `is_preliminary = false` → backfilled, safe for learning loop
- Indexes: `slot_date`, `decision_id`, `klaviyo_campaign_id`, `slot_date WHERE is_preliminary = false`

**`calendar_approvals`** (migration 006) — weekly approval gate
- `week_start date pk`, `token text`, `approved_at timestamptz`, `approved_by text default 'boris'`
- **ALWAYS use range query** (never exact date match):
  ```sql
  WHERE week_start <= %s AND %s < week_start + INTERVAL '7 days' AND approved_at IS NOT NULL
  ```

**`agent_state`** (migration 008) — key/value store
- `key text pk`, `value text`, `updated_at timestamptz`
- Key `'pacing_cache'` → JSON: `{campaign_rev, flow_rev, total, campaign_count, as_of}`
- Keys `'slack_last_read_beezy_agents'`, `'slack_last_read_new_episodes'` → Slack poll cursors

**`seo_topics`** (migration 010) — SEO article queue
- `id uuid pk`, `keyword text`, `status text default 'pending'` (pending|published|error), `published_url`, `shopify_article_id`, `error_detail`, `created_at`, `published_at`

---

## Shopify + Klaviyo data decisions

- **`order_revenue` = `currentTotalPriceSet` (net, post-refund).** Use for revenue-vs-target pacing. `totalPriceSet` is frozen at order creation and over-states revenue.
- **`gross_sales` = `subtotalPriceSet` (pre-discount, pre-shipping, pre-tax).** For demand-side analysis only; not for revenue pacing.
- **Shopify orders filtered by `updated_at`, not `created_at`.** Refunds/cancellations re-surface the order so we capture updated `currentTotalPriceSet`. Downstream readers must dedupe by `dimensions->>'order_id'` and take latest row (by `measured_at DESC`).
- **Shopify rows carry `dimensions.created_at`** (ISO string of `OrderRecord.created_at`). Pacing queries scope by `(dimensions->>'created_at')::timestamptz` so goal periods reflect orders *placed* in that window, not *ingested*.
- **Klaviyo conversion metric ID:** `X93gjq` ("Placed Order") — used for all campaign + flow revenue attribution.
- **Klaviyo API revision:** `2025-10-15` — hardcoded everywhere. Do not mix revisions in the same request.
- **Shopify API version:** `2025-10` — set via `SHOPIFY_ADMIN_API_VERSION` env var (default `2025-10`).
- **Attribution window:** 72h. `is_preliminary=true` until `revenue_backfill` finalizes. Learning loop only reads `is_preliminary=false` rows.

---

## Validator — all 17 rules

`workers/validator.py` — runs before every Klaviyo send via `validate_campaign(conn, slot, copy, cta_url)`.

### Structural rules (R1–R12)

| Rule | Name | Logic | Auto-fail? |
|---|---|---|---|
| R1 | Smart Sending ≥24h | No send to same audience today | No |
| **R2** | **7-day cooldown ≥168h** | **No send to same audience within 7 days. NON-NEGOTIABLE.** | **Yes** |
| R3 | Theme 5d gap ≥120h | Same `content_type` to same audience: ≥5d gap | No |
| R4 | Active Seal weekly <4 | `active_seal`/`active_subscribers`: ≤3 sends in 7 days | No |
| R5 | Burned audience list | Reads `agent_state['burned_audiences']`; fails if audience key present | No |
| R6 | Revenue floor ≥$300 | `slot.revenue_estimate ≥ 300`; skipped if no estimate | No |
| R7 | Format kill list | Blocked combos: `active_seal+editorial`, `vip+pre_paid_bundle` | No |
| R8 | Daily cadence ≤3 | Max 3 sends today across all audiences | No |
| R9 | Segment overlap same day | Cross-references overlap groups; blocks if overlapping segment sent today | No |
| **R10** | **Active flow overlap** | **Live Klaviyo flows double-touching audience within 72h** | **Yes** |
| R11 | Top-1% benchmark | 90d avg RPR ≥ $0.10 AND open rate ≥ 25% (needs ≥3 finalized sends) | No |
| R12 | Format data-backed | Current `content_type` RPR ≥ 70% of best format for this audience (90d) | No |

### Content checks (C1–C5)

| Rule | Name | Logic | Auto-fail? |
|---|---|---|---|
| **C1** | **Subject personalization syntax** | **Must use `{{ first_name }}`, not `{{ person.first_name\|default:'...' }}`** | **Yes** |
| **C2** | **CTA URL (customer → direct)** | **Customer segments must link to `/pages/bf-collection` or `/discount/CODE`** | **Yes** |
| **C3** | **Offer/audience alignment** | **HIGH_VALUE_SEGMENTS must not receive discount/BOGO/credit language** | **Yes** |
| C4 | Image includes humans | Image prompt must include woman/women 50+ | No |
| **C5** | **Collection URL** | **Must be `/pages/bf-collection`, never `/collections/all`** | **Yes** |

Auto-fail rules: `{"R2", "R10", "C1", "C2", "C3", "C5"}`. Any other failure → `"WARN"` → also blocked.

### Segment classifications

```python
CUSTOMER_SEGMENTS = {
    "lapsed_30d", "lapsed_60d", "lapsed_60_90d", "lapsed_90d", "lapsed_90_180d",
    "lapsed_180d", "lapsed_180d_plus", "winback_180d",
    "vip", "inner_circle", "engaged_customers", "all_customers",
    "active_seal", "active_subscribers", "whales", "high_aov",
    "one_time_buyers", "otb", "cart_abandoners",
}
HIGH_VALUE_SEGMENTS = {"vip", "inner_circle", "whales", "high_aov", "active_seal", "active_subscribers"}
PROSPECT_SEGMENTS   = {"engaged_prospects", "super_engaged"}
```

---

## Klaviyo API — confirmed endpoints

**Base URL:** `https://a.klaviyo.com` | **Revision:** `2025-10-15` (all calls)

```python
headers = {
    "Authorization": "Klaviyo-API-Key " + KLAVIYO_API_KEY,
    "revision":      "2025-10-15",
    "Content-Type":  "application/json",
}
```

### Step 1: Create template
```
POST /api/templates/
```
```json
{
  "data": {
    "type": "template",
    "attributes": {
      "name": "lapsed_30d | 2026-05-14",
      "html": "<!DOCTYPE html>...",
      "editor_type": "CODE"
    }
  }
}
```
Returns `data.id` = template_id. **`editor_type: "CODE"` is required — omitting it returns 400.**

### Step 2: Create campaign
```
POST /api/campaigns/
```
```json
{
  "data": {
    "type": "campaign",
    "attributes": {
      "name": "lapsed_30d | topic | 2026-05-14 14:00",
      "audiences": {"included": ["SEGMENT_ID"], "excluded": []},
      "send_options": {"use_smart_sending": false},
      "tracking_options": {
        "is_tracking_opens": true,
        "is_tracking_clicks": true,
        "add_tracking_params": true,
        "custom_tracking_params": [see UTM params below]
      },
      "campaign-messages": {
        "data": [{
          "type": "campaign-message",
          "attributes": {
            "definition": {
              "channel": "email",
              "content": {
                "subject":      "...",
                "preview_text": "",
                "from_email":   "help@trybeezybeez.com",
                "from_label":   "Alan from Beezy Beez"
              }
            }
          }
        }]
      }
    }
  }
}
```
Returns `data.id` = campaign_id, `data.relationships.campaign-messages.data[0].id` = message_id.
**All field names use underscores. `send-options` (hyphen) = 400.**

### Step 3: Assign template
```
POST /api/campaign-message-assign-template/
```
```json
{
  "data": {
    "type": "campaign-message",
    "id": "MESSAGE_ID",
    "relationships": {
      "template": {"data": {"type": "template", "id": "TEMPLATE_ID"}}
    }
  }
}
```
This is its own resource endpoint — NOT a sub-path of campaign-messages. NOT PATCH.

### UTM tracking params (locked)
```python
TRACKING_PARAMS = [
    {"type": "static",  "value": "Klaviyo",       "name": "utm_source"},
    {"type": "static",  "value": "campaign",       "name": "utm_medium"},
    {"type": "dynamic", "value": "campaign_name",  "name": "utm_campaign"},
    {"type": "dynamic", "value": "campaign_id",    "name": "utm_id"},
    {"type": "static",  "value": "Klaviyo",        "name": "tw_source"},
    {"type": "dynamic", "value": "profile_id",     "name": "tw_profile_id"},
    {"type": "static",  "value": "campaign",       "name": "tw_medium"},
]
```

---

## Audience / Segment IDs (Klaviyo) — confirmed May 2026

```python
SEGMENT_IDS = {
    # Segments
    "lapsed_30d":          "UEQD6k",
    "lapsed_60d":          "UfARWm",
    "lapsed_90d":          "XuS7rY",
    "lapsed_180d":         "W98qh3",
    "vip":                 "RArtzN",
    "engaged_customers":   "RvtHdn",
    "active_seal":         "UBFUcH",
    "whales":              "VAUD58",
    "engaged_prospects":   "Xrp3ha",
    "super_engaged":       "Sme9Nq",
    "one_time_buyers":     "UfARWm",
    "inner_circle":        "QHV2s5",
    # Lists
    "hive_mind_prospects": "Y6VSre",
    "all_customers":       "XFSxZt",
}
```

Hive Mind campaign: includes `[Sme9Nq, Xrp3ha, Y6VSre]`, excludes `[XFSxZt]`.

---

## Flow IDs (Klaviyo) — confirmed May 2026

```python
FLOW_TYPE_MAP = {
    "RByGDp": "welcome",             # First-Time Buyer Welcome Series
    "UU8eEK": "abandoned_checkout",  # Abandoned Checkout Flow
    "SXhgap": "abandoned_checkout",  # Abandoned Checkout - SMS Only
    "RM265B": "abandoned_cart",      # Abandoned Cart Reminder
    "W8AarU": "browse_abandonment",  # Browse Abandonment
    "RUzx4x": "replenishment",       # 1→2 replenishment flow
    "WLY4yj": "replenishment",       # Repeat Customers (2→3 Orders)
    "SHX3Ss": "winback",             # Winback
    "SmECWv": "winback",             # Lapsed Customer Check-In
    "RRMe5p": "post_purchase",       # Delayed Shipment
    "XLT2F6": "membership",          # Beehive Club
    "S97LdZ": "membership",          # Started Subscription (Hive Club)
    "UvZWwJ": "membership",          # Subscription Upgrade Flow
}
```

### Flow RPR benchmarks (minimum acceptable)

| Flow type | Min RPR | Min open rate |
|---|---|---|
| welcome | $1.50 | 40% |
| abandoned_checkout | $2.00 | 35% |
| abandoned_cart | $1.00 | 35% |
| browse_abandonment | $0.50 | 30% |
| replenishment | $0.50 | 30% |
| winback | $0.20 | 25% |
| post_purchase | $0.50 | 30% |
| membership | $0.50 | 25% |
| default | $0.10 | 20% |

---

## Shopify GraphQL patterns

**Endpoint:** `https://{SHOPIFY_SHOP_DOMAIN}/admin/api/2025-10/graphql.json`
**Header:** `X-Shopify-Access-Token: {SHOPIFY_ACCESS_TOKEN}`

### Create/update page
```graphql
mutation pageCreate($page: PageCreateInput!) {
  pageCreate(page: $page) {
    page { id handle }
    userErrors { field message }
  }
}
```
Variables: `{title, handle, body (HTML), isPublished: true}`

### Upload image to Files CDN
```graphql
mutation fileCreate($files: [FileCreateInput!]!) {
  fileCreate(files: $files) {
    files { ... on MediaImage { image { url } } }
    userErrors { field message }
  }
}
```
Variables: `{originalSource: "https://...", mediaContentType: "IMAGE", alt: "..."}`
Polls `fileCreate` result until `image.url` is populated.

### Create discount code
```graphql
mutation discountCodeBasicCreate($basicCodeDiscount: DiscountCodeBasicInput!) {
  discountCodeBasicCreate(basicCodeDiscount: $basicCodeDiscount) {
    codeDiscountNode { id codeDiscount { ... on DiscountCodeBasic {
      codes(first: 1) { nodes { code } }
    }}}
    userErrors { field code message }
  }
}
```
Variables:
```python
{
    "title":    "Beezy 20% | lapsed_30d | 2026-05-14",
    "code":     "SLEEP20",
    "startsAt": "2026-05-14T00:00:00Z",
    "endsAt":   "2026-05-17T00:00:00Z",   # 72h window
    "customerGets":      {"value": {"percentage": 0.20}, "items": {"all": True}},
    "customerSelection": {"all": True},
    "appliesOncePerCustomer": True,
}
```

### Discount CTA URL format
```
https://trybeezybeez.com/discount/CODE?redirect=/pages/bf-collection
```
Always lowercase `/discount/`. Redirect always to `/pages/bf-collection` (never `/collections/all`).

---

## Higgsfield REST (image generation)

**NEVER use `higgsfield-client` Python SDK — it is stale and broken.** Use REST directly.

```python
BASE    = "https://platform.higgsfield.ai"
MODEL   = "higgsfield-ai/soul/standard"
HEADERS = {"Authorization": f"Key {HIGGSFIELD_KEY}:{HIGGSFIELD_SECRET}", "Content-Type": "application/json"}

# Submit
resp   = httpx.post(f"{BASE}/{MODEL}", headers=HEADERS, json={"prompt": "...", "aspect_ratio": "16:9"})
req_id = resp.json()["request_id"]

# Poll until complete (3s interval, 180s timeout)
for _ in range(60):
    time.sleep(3)
    s = httpx.get(f"{BASE}/requests/{req_id}/status", headers=HEADERS).json()
    if s["status"] == "completed":
        image_url = s["images"][0]["url"]
        break
```

Default aspect ratio: 16:9. Default resolution: 720p. Model: `higgsfield-ai/soul/standard` (painterly/editorial).
Image prompts: 12 words max, woman 50+, honey tones, no text in image.

---

## Slack setup

### Channels — LOCKED, confirmed May 2026, do not change

| Channel | ID | Purpose |
|---|---|---|
| `#beezy-agents` | `C0B3DEUJS9G` | Boris's command interface — all system comms |
| `#beezy-new-episodes` | `C0B3S0CM2JV` | Sleep audio episode auto-deploy |

Both channels must be **public** for the bot to read them with standard scopes.

**WARNING:** A past installer hardcoded `BEEZY_AGENTS_CHANNEL = "C0B3S0CM2JV"` (wrong — that's #beezy-new-episodes). A runtime `_assert_channel_ids()` corrects them on every startup. Always verify:
```bash
cd ~/workspace && python3 -c "import sys; sys.path.insert(0,'.'); from agents.slack_agent import BEEZY_AGENTS_CHANNEL; print(BEEZY_AGENTS_CHANNEL)"
# Must print: C0B3DEUJS9G
```

### Bot credentials
- Bot username: `slackbottoken` | Bot user ID: `U0B3JA9UYE7` | Bot ID: `B0B3U9UEWH2`
- Token env var: `SLACK_BOT_TOKEN` (`xoxb-` prefix)
- Webhook env var: `SLACK_WEBHOOK_URL` (outbound-only posts)

### Required OAuth scopes
`channels:history`, `channels:read`, `groups:history`, `groups:read`, `chat:write`

### Commands Boris types in `#beezy-agents`

**Fixed commands:**
```
approved              → approve current month's calendar
approved week         → approve the 7-day plan (writes calendar_approvals)
approved today        → approve just today's slots
approved [date]       → approve a specific date
deploy campaigns      → run today's orchestrator now
what is revenue       → pull pacing data from Neon
deploy latest episode → deploy from #beezy-new-episodes
generate calendar     → regenerate this month's calendar
run weekly brief      → post next 7 days to Slack now
restore calendar      → re-post the current calendar page link
status                → see today's dispatch log
help                  → show full command list
```

**Conversational (natural language, Claude-interpreted):**
- `"add 2 more SMS this month targeting VIPs"`
- `"remove flow experiments from the calendar"`
- `"move the Wednesday campaign to Friday"`
- `"max 2 emails per day for the rest of May"`
- `"what's planned for next week?"`

The agent interprets these via Claude Sonnet, modifies the calendar JSON in Neon, republishes the Shopify calendar page, and confirms in Slack.

**Acknowledgment behavior:** Agent posts `⏳ Got it — working on it...` immediately when it reads a new message, before processing. On completion: posts result. On error: posts `❌ Error: [details]`.

### Interactive endpoint (`POST /api/slack/interactive`)

Handles Slack button callbacks. Currently supports:
- `action_id = "apply_flow_fix"`, `value = "<template_id>:<flow_id>"` → assigns the pre-generated fix template to all email messages in the flow

Responds within 3s (heavy work in thread executor). Auto-replies to `response_url`.

---

## Calendar system

### Generation schedule
Generates **next month's** calendar **7 days before end of current month** at 9am ET.
- Formula: `calendar.monthrange(year, month)[1] - 7` days from month start
- For June calendar: triggers May 24 (31 − 7 = 24)

### Generation flow
1. Pull live RPR by segment from `performance` + `calendar_executions` tables
2. Pull current pacing state (gap, required daily, days remaining)
3. Build context block with both (see `pacing/calendar_live_data.py`)
4. Call Opus (`claude-opus-4-6`, `max_tokens=16000`) with system prompt + live data as hard constraints
5. Parse + repair JSON response (`_repair_json` fallback)
6. Persist to `decisions` table (`decision_type='calendar_plan'`)
7. Generate HTML report (planned vs actual columns)
8. Publish to Shopify `/pages/calendar-YYYY-MM`
9. Post Slack executive summary + link (no slot dump in Slack)

### Slot schema
```json
{
  "date":             "2026-05-15",
  "content_type":     "klaviyo_campaign",
  "audience":         "lapsed_30d",
  "topic_angle":      "Sleep science: why 50+ women wake at 3am",
  "send_time_est":    "14:00",
  "priority":         "high",
  "revenue_estimate": 315.00,
  "needs_page":       true,
  "discount_code":    "SLEEP20",
  "discount_pct":     20,
  "rationale":        "lapsed_30d last touched 34 days ago, drove $1,420 last send",
  "goal_alignment":   "closes $5,956/day gap with high-intent reactivation",
  "adjustment_lever": "If under $200: switch to VIP audience"
}
```

### `needs_page` logic
- `true` → system creates Shopify landing page first; email CTA drives to page; page CTA drives to product/discount. Use for: sleep science, research, story, audio, meditation angles.
- `false` → email CTA drives directly to product or discount URL. Use for: pure offer, discount, reactivation, promotional angles.

### Confirmed RPR by segment (real Klaviyo 90-day pull, May 2026)

| Segment | Median RPR | Median list size | Est $/send |
|---|---|---|---|
| active_seal | $1.268 | 511 | $648 |
| whales | $0.658 | 1,038 | $683 |
| lapsed_30d | $0.267 | 3,618 | $967 |
| vip | $0.161 | 5,424 | $873 |
| engaged_customers | $0.101 | 13,340 | $1,347 |
| one_time_buyers | $0.056 | 12,951 | $725 |
| engaged_prospects | $0.064 | 12,002 | $768 |
| sniper_followup | $0.120 | 4,447 | $534 |

These live in `pacing/calendar_live_data.py` as `FALLBACK_RPR`/`FALLBACK_LIST_SIZE`. Always pull fresh via live API before making decisions — do not treat as permanent facts.

### Approval flow
1. Calendar generated → Shopify page + Slack link (executive summary only)
2. Boris reviews at `trybeezybeez.com/pages/calendar-YYYY-MM`
3. Boris types `approved` in #beezy-agents → month approved in DB
4. Every Sunday 9pm: weekly brief posts next 7 days to Slack
5. Boris types `approved week` → week approved in `calendar_approvals`
6. Next day 8am: orchestrator checks approval, dispatches slots

---

## Content type definitions

| content_type | Handler | Autonomy | Notes |
|---|---|---|---|
| `klaviyo_campaign` | `beezy_campaign.py` | Tier 1 auto | Page-first if `needs_page=true` |
| `sniper_followup` | `beezy_campaign.py` | Tier 1 auto | Different angle from parent, same discount code |
| `hive_mind` | `klaviyo_campaign.py` | Skip (owns its 10am cron) | Every 3 days from Issue 014 |
| `seo_blog` | `seo_blog.py` | Tier 1 auto | `revenue_estimate` always 0 |
| `sleep_audio` | `workers/sleep_audio_producer.py` | Tier 1 auto | Script → image → Shopify page → two Klaviyo campaigns → Slack posts full script for Boris to feed into sleep-audio-platform (TTS → Buzzsprout → posts to #beezy-new-episodes → watcher updates page with audio embed) |
| `sms_campaign` | `post_draft()` | Tier 2 Slack draft | Boris reviews before send |
| `flow_experiment` | `post_draft()` | Tier 3 notify | Boris manually implements in Klaviyo |

---

## Campaign pipeline (beezy_campaign.py)

Full sequence for `klaviyo_campaign` and `sniper_followup` slots:

1. **Discount:** if `slot.discount_pct + slot.discount_code` → create Shopify discount (72h window, once per customer) → CTA = `https://trybeezybeez.com/discount/CODE?redirect=/pages/bf-collection`
2. **Landing page:** if `_needs_page(slot)` → generate page content via Anthropic → build branded HTML → create Shopify page at `/pages/{slug}`
3. **Copy:** generate via Anthropic Sonnet (subject, preview_text, from_label, body_paragraphs, cta_text, image_prompt)
4. **Image:** Higgsfield REST → Shopify CDN upload (poll until `image.url` populated)
5. **Email HTML:** hero image + body + discount box (if applicable) + CTA button
6. **Klaviyo:** `POST /api/templates/` → `POST /api/campaigns/` → `POST /api/campaign-message-assign-template/`
7. **Validator:** `validate_campaign(conn, slot, copy, cta_url)` — blocks if any auto-fail rule triggers
8. **Auto-schedule:** `workers/auto_schedule.py` — sets send time based on `slot.send_time_est` or content_type defaults
9. **Mark:** `calendar_executions` row → `status='dispatched'`
10. **Slack notify:** Block Kit with subject, preview, audience, send time, rev estimate, "Open in Klaviyo" + "View Landing Page" buttons

---

## Hive Mind newsletter

- Klaviyo list ID: `Y6VSre` (Hive Mind prospects)
- Issues state machine: `draft → approved → published`
- Issue pages: `trybeezybeez.com/pages/hive-mind-issue-{NNN}`
- **Auto-create trigger** (10am daily): `status='draft' AND shopify_page_id IS NOT NULL AND klaviyo_campaign_id IS NULL`
- Campaign audience: includes `[Sme9Nq, Xrp3ha, Y6VSre]`, excludes `[XFSxZt]`
- Send time: 8pm ET
- Skills: `hive-mind-newsletter` (writing), `hive-mind-pipeline` (publishing)

---

## Episode deployer (sleep audio)

The sleep audio platform (separate local machine) posts JSON metadata to `#beezy-new-episodes` when an episode is ready. `agents/slack_agent.py` watches the channel and auto-triggers `agents/klaviyo_deployer.py:deploy_episode()` when it sees a message containing `"episode_id"`.

### Episode metadata schema (posted to #beezy-new-episodes)
```json
{
  "episode_id":          "ep_001",
  "title":               "The Same Steps Every Night",
  "episode_type":        "sleep_story",
  "buzzsprout_url":      "https://...",
  "shopify_page_url":    "https://trybeezybeez.com/pages/...",
  "suggested_send_date": "2026-05-15",
  "duration_minutes":    28
}
```

Episode types: `sleep_story`, `guided_meditation`, `affirmation_meditation`, `morning_meditation`, `soundscape`

### Episode deploy creates two Klaviyo campaigns
- **Email A:** Engaged Customers (`RvtHdn`) excluding Active Seal (`UBFUcH`) — 8:00pm ET
- **Email B:** Active Seal (`UBFUcH`) — 8:15pm ET

---

## Worker pipelines

### `workers/seo_blog.py` — 2,000-word article
1. Look up pending topic from `seo_topics` table (or use `slot.topic_angle`)
2. Generate article via Anthropic Sonnet: title, slug, meta_description (≤155 chars), html_body, word_count
3. Publish to first Shopify blog via GraphQL `articleCreate`
4. Update `seo_topics` status → `published`, store `published_url`, `shopify_article_id`

Article voice: Expert copywriter, warm tone, science-backed, opens with specific person/stat/scenario.

### `workers/flow_monitor.py` — weekly flow health check
1. Pull all live flows from Klaviyo
2. Pull 30d performance per flow (recipients, revenue, opens, RPR)
3. Benchmark against `FLOW_BENCHMARKS` by flow type
4. Flag: zero-revenue flows, underperforming, high-engagement-no-conversion
5. For zero-revenue flows with >50 recipients: call `fix_flow()` → generate new copy via Anthropic → create Klaviyo template → post Slack "Apply Fix" button
6. Button click → `POST /api/slack/interactive` → `action_id=apply_flow_fix` → assigns template to all flow email messages

### `workers/revenue_backfill.py` — 72h attribution finalization
- Runs 9am daily
- Finds `calendar_executions` dispatched 3+ days ago with `is_preliminary IS NOT false`
- Pulls actual revenue, recipients, RPR from Klaviyo Reporting API per `klaviyo_campaign_id`
- Updates: `actual_revenue`, `recipients`, `actual_rpr`, `is_preliminary=false`, `finalized_at`

### `workers/learning_loop.py` — three cadences
- **Weekly (Sunday 9pm):** 7d actual vs projected, top/underperformers, monthly pacing, adjustment recommendations → Slack
- **Biweekly (15th 9:30am):** MTD vs $150K goal, recommends frequency adjustments → Slack
- **Monthly (1st 9:30am):** full retrospective, writes RPR-by-audience to `strategies` table (`component='learning_loop'`), feeds next calendar generation → Slack

### `workers/klaviyo_backfill.py` — historical data import
- CLI: `python -m workers.klaviyo_backfill [--month YYYY-MM] [--dry-run]`
- Pulls manually-sent Klaviyo campaigns, maps segment IDs → internal audience names
- Inserts finalized rows into `calendar_executions` so learning loop has real data

### `workers/image_gen.py` — Higgsfield image generation
See Higgsfield REST section above. Default model: `higgsfield-ai/soul/standard`.

### `workers/pacing_cache.py` — Klaviyo MTD revenue
- Runs 7:35am daily (after pacing brain)
- Pulls this_month campaign + flow revenue from Klaviyo Reporting API
- Stores in `agent_state` key `'pacing_cache'`
- Used by `/debug/pacing` endpoint and dashboard

---

## Pacing brain — Phase 2A vs 2B

**Phase 2A (built):** Pure math. `brain.py` computes `gap_pct`, `required_daily_rate`, `status` (ahead/on-track/behind), top-5 campaigns, top-5 flows. `cron.py` persists to `pacing_state` and posts Block Kit digest to Slack.

**Phase 2B (built):** `compute_daily_priorities()` in `brain.py` determines today's mode (boost/push/maintain/ease) from pacing gap, writes to both `decisions` (decision_type='daily_priority') and `priorities` tables. `pacing/cron.py` calls it at 7:30am. `orchestrator.py` reads the mode and acts: boost sorts by RPR then injects an emergency extra slot for the first cooldown-free HIGH_FREQ audience; push sorts by RPR only; ease drops the lowest-revenue campaign slot when at cadence limit (≥3 campaign sends).

---

## Brand design system

All emails and landing pages use:

| Property | Value |
|---|---|
| Font | Georgia, serif |
| Primary color | `#8b4513` (amber/saddle brown) |
| Background | `#faf6ee` (warm cream) |
| Text | `#2c2417` (dark brown) |
| Accent | `#d4a847` (honey gold) |
| Border | `#e8dcc8` |
| Max width (email) | 600px |
| Max width (landing pages) | 680px |
| CTA button | `background: #8b4513; color: #fff; letter-spacing: 1.5px; text-transform: uppercase` |
| Discount box | `background: #fdf5e6; border: 1px dashed #d4a847` |

Email from names:
- `"Alan from Beezy Beez"` — personal/lapsed/reactivation
- `"Beezy Beez"` — promotional/announcement

---

## Product catalog — LOCKED, never invent products

Only reference these in campaign copy. Anything not on this list **DOES NOT EXIST**.

**Honey ($49–$64.95):** Cinnamon, Caramel, Blood Orange, Apple Pie, Vanilla, Chocolate Strawberry, Original, Graham Cracker, Strawberry Cheesecake
**Premium:** Delicious Calm 1500MG ($64.95), Ultra Strength 3000MG ($129.95)
**Gummies ($49–$59.95):** Mixed Fruit, Black Cherry, Strawberry, CBN, Trio Bundle ($179.85)
**Other:** Oil ($54.95/$69.95), Balm ($39.95/$69.95), Lotion ($59.95), Lip Balm 3pk ($39.95), Tea ($24.95), Doggy Treats ($34.95), Candle Set ($29.95), Gift Box ($191.95)
**Subscriptions:** Hive Club ($19.95/mo or $199.50/yr, 45% off, free shipping), 3-Month ($54.95/mo), Pre-Paid Annual ($199.50)

**DOES NOT EXIST:** ashwagandha honey, wildflower honey, lavender honey, manuka honey, or anything not listed above.

**Note:** Beezy Beez product pages are built with Zipify Pages, not standard Shopify templates. Edit in Zipify editor only — do not overwrite with standard Shopify page mutations.

---

## Operations

### Environment variables (Replit Secrets)

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Neon Postgres connection string |
| `BEEZY_ANTHROPIC_API_KEY` | Anthropic API (copy, calendar, Slack agent) |
| `KLAVIYO_API_KEY` | Klaviyo REST API |
| `KLAVIYO_FROM_EMAIL` | `help@trybeezybeez.com` |
| `SHOPIFY_SHOP_DOMAIN` | `trybeezybeez.myshopify.com` |
| `SHOPIFY_ACCESS_TOKEN` | Shopify Admin API token |
| `HIGGSFIELD_KEY` | Higgsfield API key |
| `HIGGSFIELD_SECRET` | Higgsfield secret |
| `SLACK_BOT_TOKEN` | `xoxb-...` Bot User OAuth Token |
| `SLACK_WEBHOOK_URL` | Incoming webhook for outbound-only posts |
| `REPLIT_DOMAIN` | Replit project domain (for webhooks) |

### Replit deployments

**Autoscale (primary):** runs `app/main.py` → FastAPI server + dual-loop background tasks. Handles everything: Slack polling, cron jobs, API endpoints.

**Scheduled (neutered):** `scripts/cron_dispatch.py` runs `echo ok`. All actual cron work moved to web server `_cron_loop`.

**One-off backfill:** `python -m ingestion.sync all --lookback-days N` — bypasses cursor, production cadence unaffected.

### Health endpoints

- `GET /healthz` — returns `{"status": "ok"}`. Used by Replit health checks.
- `GET /debug/pacing` — reads `agent_state` key `'pacing_cache'`, returns parsed JSON. Useful for diagnosing deployed container state.

### Failure alerts

`ingestion/sync.py` POSTs to `SLACK_WEBHOOK_URL` on any non-success status. Success is silent. If `SLACK_WEBHOOK_URL` is unset, logs a warning and continues.

---

## Model choices

| Use case | Model |
|---|---|
| Calendar generation | `claude-opus-4-6` |
| Slack agent interpretation | `claude-sonnet-4-6` |
| Campaign copy, SEO blog, SMS, flow fix | `claude-sonnet-4-6` (default) |
| Hive Mind newsletter (skill invocation) | `claude-opus-4-7` |
| `workers/skill_runner.py` default | `claude-sonnet-4-6` |

Pricing reference: Sonnet $3/$15 per 1M, Opus 4.7 $15/$75, Haiku $1/$5 (input/output).

---

## External integrations

### Sleep Audio Platform
- Runs locally on Boris's Windows machine
- Python/FastAPI + ElevenLabs Pro + Neon + Cloudflare R2 + Buzzsprout
- Brand: Deep Bear Sleep (`deepbearsleep.com`)
- Narrator: Margaret, voice Luna (`v7t81zh1sAZvDEPx2B8A`)
- Posts episode metadata JSON to `#beezy-new-episodes` when ready

### Beezy System (manual campaigns)
- Separate Claude chat with MCP tools (Klaviyo, Shopify, Higgsville)
- Handles ad-hoc campaigns, flow tuning, learning loop retros
- Skill: `beezy-system`

### Hive Mind Pipeline
- Claude chat with MCP tools
- Skills: `hive-mind-newsletter` (writing), `hive-mind-pipeline` (publishing)

### CryptoStuffConverter
- Separate Replit project: `cryptostuffconverter.com`
- 37,000+ programmatic SEO pages, three-tier automation

---

## Anti-patterns — all confirmed from production bugs

### Python / architecture
- **Don't import `calendar` in `pacing/__init__.py`** — circular import with Python's stdlib `calendar` module. Only import `brain` and `cron` from `pacing/__init__.py`.
- **Don't use `agent` column** on `decisions` or `strategies` tables — column is `component`
- **Don't use exact date match** for approval check — use range query `week_start <= today < week_start + 7 days`
- **Don't check `status != 'dispatched'`** in `_already_ran` — must be `AND status != 'failed'` so failed slots retry
- **Don't call `python scripts/publish_page.py` directly** — use `python -m scripts.publish_page`
- **Don't run agents from shell without `sys.path.insert(0,'.')`** — produces `ModuleNotFoundError` even from the correct directory
- **Don't call `conn.commit()` inside test helpers** — test fixture must call `conn.rollback()` in teardown; helpers just INSERT without committing. Committed test data creates phantom `calendar_executions` rows that poison R1/R2/R8 validator checks on the next run. (Pacing tests are the exception: `compute_pacing_state` opens its own connection so they need commit+explicit-DELETE cleanup.)

### Klaviyo REST
- **Don't use hyphens in field names** — `send-options`, `use-smart-sending`, `tracking-options` all return 400. Must be underscores.
- **Don't use `editor_type: "DRAG_DROP"`** — must be `"CODE"` for HTML templates
- **Don't POST `body`** in campaign message content — not a valid field, returns 400
- **Don't POST `template_id`** in campaign message definition — not a valid field, returns 400
- **Don't PATCH campaign-messages with `relationships`** — `template` is not an allowed relation on PATCH
- **Don't use `/api/campaign-messages/{id}/assign-template/`** — 404, path does not exist
- **Don't use `type: "campaign-message-assign-template"`** in the assign payload — use `"campaign-message"`
- **Don't mix API revisions** — all calls must use `2025-10-15`

### Higgsfield
- **Don't use `higgsfield-client` Python SDK** — stale and broken. Use REST at `https://platform.higgsfield.ai`

### Shopify
- **Don't assume product pages are standard Shopify templates** — they're built with Zipify Pages. Edit in Zipify editor only.
- **Don't use `/collections/all` in CTA URLs** — C5 auto-fails. Always use `/pages/bf-collection`.

### Calendar + copy
- **Don't dump all slots into Slack** — post executive summary + live URL only
- **Don't assign revenue to SEO blog slots** — always 0, excluded from totals
- **Don't let Opus invent revenue estimates** — pass live RPR data as hard constraints
- **Don't skip weekends** — Opus defaults to weekday-only without explicit instruction in system prompt
- **Don't trust initial RPR fallback values** — original `lapsed_30d` fallback was $0.09; actual Klaviyo shows $0.267 median (3x off). Pull live data before making any decisions.

### Slack channel IDs
- **Don't hardcode `C0B3S0CM2JV` as `BEEZY_AGENTS_CHANNEL`** — that's #beezy-new-episodes. #beezy-agents is `C0B3DEUJS9G`.
- **Don't omit `_assert_channel_ids()`** — runtime guard in `agents/slack_agent.py` corrects IDs on every startup.

### Email copy
- **Don't use `{{ person.first_name|default:'there' }}`** in subjects — C1 auto-fail. Use `{{ first_name }}` only.
- **Don't reference products not in the catalog** — system has a locked product list. Invented products make copy uncredible and un-sendable.

---

## Current build status

### Fully working (production-ready)
- All of Layer 6: `ingestion/shopify.py`, `ingestion/klaviyo.py`, `ingestion/sync.py`
- Layer 2 core: `pacing/cron.py`, `pacing/orchestrator.py`, `pacing/weekly_brief.py`
- Layer 1 math: `pacing/brain.py` (Phase 2A only)
- `pacing/calendar.py` — Opus-driven monthly calendar generator
- `workers/validator.py` — all 17 rules: 12 structural (R1–R12) + 5 content checks (C1–C5); auto-fail: R2, R10, C1, C2, C3, C5
- `workers/beezy_campaign.py` — full autonomous email pipeline
- `workers/klaviyo_campaign.py` — Hive Mind campaign creator
- `workers/seo_blog.py` — generation + Shopify publish
- `workers/sms_campaign.py` — copy generation + Klaviyo campaign creation
- `workers/flow_monitor.py` — flow health check + Slack fix button
- `workers/learning_loop.py` — all three cadences (weekly/biweekly/monthly)
- `workers/revenue_backfill.py` — 72h attribution finalization
- `workers/klaviyo_backfill.py` — historical data import
- `workers/pacing_cache.py` — daily Klaviyo MTD cache (with retry)
- `workers/auto_schedule.py` — campaign scheduling
- `workers/deliverability_monitor.py` — daily bounce/spam/unsub check; Slack alert if thresholds exceeded; `/api/run-deliverability-check` endpoint
- `workers/hub_updater.py` — injects Hive Mind issue cards and episode cards into Shopify hub pages via sentinel comments
- `workers/audience_health.py` — daily STALE audience detection; RPR trend per audience; writes to `agent_state['audience_health']`; Slack alert for money-on-the-table gaps
- `workers/morning_brief.py` — 8:05am Slack digest: MTD revenue vs goal, pace mode, today's sends, action needed
- `workers/run.py` — CLI for Hive Mind drafts: `python -m workers.run --skill hive_mind [--issue N] [--dry-run]`; reads DB state, calls skill, saves draft, generates cover image, posts to Slack
- `workers/image_gen.py` — Higgsfield REST integration
- `workers/shopify_publisher.py` — image upload + CDN polling
- `workers/shopify_page_builder.py` — Hive Mind page HTML builder
- `workers/skill_runner.py` — generic Anthropic invoker
- `lib/slack.py`, `lib/email_builder.py`, `lib/email_builder_episode.py`, `lib/shopify_admin.py`
- `agents/slack_agent.py` — Slack Web API polling + Claude routing
- `app/main.py` — dual-loop server, 16 cron jobs, interactive endpoint
- `app/dashboard.py` — live FastAPI HTML dashboard (primary dashboard)
- `dashboard/` — Next.js dashboard (built, multi-route: analytics/audiences/calendar/content/flows/system; not yet deployed; talks to FastAPI backend via `NEXT_PUBLIC_API_URL`)

### Not built / known gaps
- **Next.js dashboard** (`dashboard/`) — fully built and compiles clean (`npm run build` passes). Has `vercel.json` and `.env.example`. Deploy: connect the `dashboard/` directory to a Vercel project, set env var `NEXT_PUBLIC_API_URL=https://<your-replit-app>.replit.app`. All 6 routes (analytics/audiences/calendar/content/flows/system) are live; API calls proxy to FastAPI backend.

### Recently completed (session May 2026)
- **Tests:** 95 tests across `tests/test_pacing.py`, `tests/test_validator.py`, `tests/test_dispatch_chain.py`, and `tests/test_e2e_pipeline.py` — all 17 validator rules, pacing math, orchestrator routing, R5 burn list, R2 sniper exemption, sniper helpers, hub updater, full E2E pipeline (approval gate, BOOST/EASE/PUSH mode, sniper E2E, deliverability math, audience health flags). All pass.
- **A/B subject testing:** `workers/beezy_campaign.py` — curiosity vs benefit subjects for large segments (>3k); patterns accumulated in `agent_state['subject_patterns']`; winner wired into calendar context.
- **Revenue backfill:** `workers/revenue_backfill.py` — updates `actual_revenue`, `actual_rpr`, `is_preliminary=false` after 72h window; feeds subject_patterns learning loop.
- **Dashboard data:** `app/dashboard.py` — all sections now show live data: campaign/flow revenue from performance table fallback, top performers from Klaviyo, audience RPR from segment_ids mapping.
- **`klaviyo_campaign_id` stored:** `pacing/orchestrator.py` — now extracts campaign_id from handler return value and writes it to `calendar_executions`; enables future backfill.
- **`POST /api/approve-month`** — approves all weeks in current month; "Approve All Weeks" button in dashboard.
- **Calendar context enriched:** `pacing/calendar.py` — subject_patterns and content pillar attribution now fed to Opus at calendar generation time.
- **`db/schema.sql`:** unified schema file for fresh DB setup (replaces running 11 migrations manually).
- **Root cleanup:** 70 orphaned install/patch scripts deleted from workspace root.
- **Human-readable Slack messages:** `pacing/weekly_brief.py` and `workers/morning_brief.py` — slot lines now show "Email → Lapsed 30d customers at 2pm  ·  est. $967" instead of raw slugs; `_fmt_time()` converts 24h to 12h am/pm; `CONTENT_LABEL`/`AUDIENCE_LABEL` dicts map all internal names to owner-friendly labels.
- **Concurrent orchestrator guard:** `pacing/orchestrator.run_daily()` — `pg_try_advisory_lock(7777777777)` at entry; rapid-fire `deploy campaigns` Slack triggers no longer create phantom `calendar_executions` rows. Cleaned 28 orphan phantom rows from May 15.
- **Cron catch-up logic:** `app/main.py` — pacing brain (7:30am) and orchestrator (8:00am) now have catch-up windows (7:30–8:29 and 8:00–9:00 ET) backed by `agent_state` ran-today sentinels. Replit Autoscale scale-to-zero no longer silently drops daily jobs.
- **Duplicate pacing_state dedup:** `pacing/cron._insert_pacing_state()` — checks for existing today's row before inserting; prevents duplicate pacing snapshots from rapid re-runs or catch-up.
- **BOOST topic from calendar:** `pacing/orchestrator._boost_candidate_slot()` — looks up next unexecuted calendar slot for the audience before falling back to hardcoded topic angle; BOOST emails now match planned calendar narrative.
- **Approval nudge:** `pacing/weekly_brief.run_approval_nudge()` — Monday 9:30am ET: posts urgent Slack reminder if this week's plan is still not approved; no-op if already approved.
- **R2 parity fix:** `pacing/orchestrator._audience_in_cooldown()` mirrors validator R2 exactly — exclusive 7-day boundary, only dispatched/completed rows count. Prevents orchestrator BOOST from bypassing R2 rules the validator enforces.
- **`sniper_followup` non-opener targeting:** `workers/beezy_campaign.py` — finds parent campaign via DB, pulls opener profile IDs from Klaviyo Events API (metric `WrnXmp`), creates a temporary exclusion list, passes it to campaign creation. Validator R2 updated with sniper exemption: allowed within 7-day window if parent `klaviyo_campaign` exists. 31/31 tests pass.
- **Test DB isolation fixed:** `tests/test_validator.py` — fixture now calls `conn.rollback()` on teardown; `_insert_exec` no longer calls `conn.commit()`. Validator reads uncommitted data on the same connection. Zero phantom rows written to production on every test run. Cleaned 236 phantom rows caused by prior test runs committing directly to production.
- **Hub page auto-updates:** `workers/hub_updater.py` — `add_issue_to_hubs(issue)` and `add_episode_to_hubs(metadata)` inject content cards into Shopify hub/archive pages using sentinel HTML comments (`<!-- HUB_SECTION_START -->` / `<!-- HUB_ITEMS_START -->`). Hive Mind issues trigger a full rebuild of `/pages/the-hive-mind` from DB + a prepend on `/pages/sleep-science-hub`. Episodes route by `episode_type` to the correct hub (sleep_story/soundscape → sleep-science-hub, guided/affirmation meditation → meditation-library, morning_meditation → morning-wellness-hub). Wired into `workers/klaviyo_campaign.create_campaign_for_issue()` and `agents/klaviyo_deployer.deploy_episode()`. Both calls are non-fatal (errors logged, pipeline continues).
- **`pacing_cache.py` retry:** replaced bare `try/except` with `_post_report()` (429/5xx backoff, up to 5 retries, mirrors `ingestion/klaviyo.py` pattern) and `_write_cache()` (3-attempt retry on Neon write). Silent $0 on timeout no longer possible.
- **`sleep_audio` pipeline (two-phase):** `workers/sleep_audio_producer.py` — orchestrator runs phase 1 on calendar slots: `invoke_skill("sleep_audio")` → Higgsfield image → Shopify CDN → Shopify landing page → saves episode stub to `episodes` DB → updates hub pages → Slack posts full script for Boris. Phase 2: Boris feeds script into sleep-audio-platform (TTS → Buzzsprout → posts metadata JSON to `#beezy-new-episodes`); the watcher calls `deploy_episode()` once, creating both Klaviyo campaigns with the Buzzsprout URL. Calling `deploy_episode()` in phase 1 (before audio exists) was a campaign-duplication bug — fixed by splitting into two phases.
- **`deliverability_monitor.py` retry + field fix:** added `_post_with_retry()` (429/5xx backoff, 5 retries). Fixed stat field names (`unsubscribed` → `unsubscribes`); `hard_bounced` falls back to `bounced` if Klaviyo doesn't return it. Runs 10:30am ET daily.
- **R5 validator implemented:** reads `agent_state['burned_audiences']` (JSON list of audience keys); fails if the slot's audience appears in the list. No longer a stub.
- **Episodes DB table:** `episodes` table added to `db/schema.sql`. Audio archive is now DB-backed, same as Hive Mind `issues`. Hub pages can be fully rebuilt from DB if ever wiped.

### Known gaps / deferred
_(none — all known gaps resolved as of May 2026)_

---

## Build phases (status)

1. ~~Performance ingestion (Layer 6)~~ **DONE**
2. ~~Pacing brain (Layer 1)~~ **DONE** — Phase 2A math + Phase 2B priority decisions: `compute_daily_priorities()` writes to `decisions`+`priorities` tables; orchestrator reads mode and acts on it (sort by RPR in push/boost, inject emergency slot in boost, drop weakest in ease)
3. ~~Skill runner + first orchestrated worker~~ **DONE**
4. ~~Calendar generator~~ **DONE**
5. ~~Other workers~~ **DONE** (email, SMS, SEO blog, flow monitor, learning loop)
6. ~~Dashboard~~ **DONE** — FastAPI HTML, live data from Klaviyo/performance table, approve buttons, top performers, audience health with RPR
7. ~~Learning loop~~ **DONE** (all 3 cadences)
8. ~~Skill prompt files~~ **DONE** — all 6 workers/prompts/*.md populated (campaign_email, flow_tuning, seo_blog, sleep_audio, sms, hive_mind)
9. ~~Auto-scheduling~~ **DONE** — `workers/auto_schedule.py` sets `send_strategy` + triggers `campaign-send-jobs`. Campaign created → written to `agent_state['pending_schedules']` → `check_pending_schedules()` fires every 5 min in cron loop → calls `schedule_campaign()`. Boris does not manually schedule.
10. ~~Tests~~ **DONE** — 95 tests: validator (17 rules), pacing math, orchestrator routing, R5 burn list, R2 sniper exemption, sniper helpers, hub updater, E2E pipeline; production-DB-safe

---

## Quick start (rebuilding from scratch)

1. Create Replit project — Python, name `beezy-agents-ingestion`
2. Add all env vars (Section above) to Replit Secrets
3. Create Neon DB — run `psql "$DATABASE_URL" -f db/schema.sql` (unified schema, replaces 11 individual migration files)
4. Deploy file structure from `~/workspace/`
5. `pip install anthropic httpx psycopg fastapi uvicorn python-dotenv`
6. Create Slack app — add 5 OAuth scopes; install to workspace; invite bot to both channels
7. Set web deployment run command to `uvicorn app.main:app --host 0.0.0.0 --port 8080`
8. Generate first calendar:
   ```bash
   cd ~/workspace && python3 -c "import sys; sys.path.insert(0,'.'); from pacing.calendar import run_monthly; run_monthly()"
   ```
9. Type `help` in #beezy-agents — bot responds with command list
10. Type `approved` then `approved week` — campaigns start flowing
