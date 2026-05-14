-- Beezy Agents — Unified schema (generated from live Neon DB, May 2026)
-- Applies all 11 migrations in a single file for fresh-DB setup.
-- Run: psql "$DATABASE_URL" -f db/schema.sql

-- ── Core tables ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS goals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT NOT NULL,
    target_metric   TEXT NOT NULL,
    target_value    NUMERIC,
    period_start    DATE,
    period_end      DATE,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pacing_state (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id                 UUID NOT NULL REFERENCES goals(id),
    measured_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    period_to_date_value    NUMERIC,
    target_to_date_value    NUMERIC,
    gap_pct                 NUMERIC,
    days_remaining          INTEGER,
    required_daily_rate     NUMERIC
);
CREATE INDEX IF NOT EXISTS pacing_state_goal_id_measured_at_idx ON pacing_state (goal_id, measured_at);

CREATE TABLE IF NOT EXISTS priorities (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decided_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    effective_for       DATE NOT NULL,
    prioritized_workers JSONB NOT NULL,
    reasoning           TEXT,
    pacing_snapshot     JSONB
);
CREATE INDEX IF NOT EXISTS priorities_effective_for_idx ON priorities (effective_for);

CREATE TABLE IF NOT EXISTS runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    worker          TEXT,
    triggered_by    TEXT,
    priority_id     UUID REFERENCES priorities(id),
    status          TEXT DEFAULT 'queued',
    autonomy_tier   SMALLINT DEFAULT 2,
    approval_status TEXT,
    input           JSONB DEFAULT '{}',
    output          JSONB,
    attempts        INTEGER DEFAULT 0,
    max_attempts    INTEGER DEFAULT 3,
    error           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    -- Migration 002 columns
    skill           TEXT,
    model           TEXT,
    context         JSONB,
    output_text     TEXT,
    output_json     JSONB,
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    cost_usd        NUMERIC DEFAULT 0,
    elapsed_seconds NUMERIC DEFAULT 0
);
CREATE INDEX IF NOT EXISTS runs_status_worker_idx ON runs (status, worker);
CREATE INDEX IF NOT EXISTS runs_priority_id_idx ON runs (priority_id);
CREATE INDEX IF NOT EXISTS runs_skill_started_idx ON runs (skill, started_at DESC);
CREATE INDEX IF NOT EXISTS runs_status_idx ON runs (status) WHERE status = 'error';

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source              TEXT NOT NULL,
    window_start        TIMESTAMPTZ,
    window_end          TIMESTAMPTZ,
    records_ingested    INTEGER,
    status              TEXT NOT NULL,
    error               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ingestion_runs_source_created_at_idx ON ingestion_runs (source, created_at);

CREATE TABLE IF NOT EXISTS performance (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID REFERENCES runs(id),
    source          TEXT NOT NULL,
    metric_name     TEXT NOT NULL,
    metric_value    NUMERIC,
    dimensions      JSONB,
    window_start    TIMESTAMPTZ,
    window_end      TIMESTAMPTZ,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Migration 007 columns
    is_preliminary  BOOLEAN NOT NULL DEFAULT TRUE,
    finalized_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS performance_run_id_metric_name_idx ON performance (run_id, metric_name);
CREATE INDEX IF NOT EXISTS performance_source_measured_at_idx ON performance (source, measured_at);
CREATE INDEX IF NOT EXISTS performance_metric_name_measured_at_idx ON performance (metric_name, measured_at);
CREATE INDEX IF NOT EXISTS idx_perf_final ON performance (metric_name, measured_at) WHERE is_preliminary = FALSE;

CREATE TABLE IF NOT EXISTS decisions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decided_by      TEXT NOT NULL DEFAULT 'pacing_brain',
    decision_type   TEXT NOT NULL,
    input_context   JSONB,
    reasoning       TEXT,
    output          JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS decisions_decision_type_created_at_idx ON decisions (decision_type, created_at);

CREATE TABLE IF NOT EXISTS strategies (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    component               TEXT NOT NULL,
    strategy_text           TEXT NOT NULL,
    approved_by             TEXT,
    approved_at             TIMESTAMPTZ,
    supersedes_id           UUID REFERENCES strategies(id),
    evidence_decision_id    UUID REFERENCES decisions(id),
    is_active               BOOLEAN NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS strategies_component_is_active_idx ON strategies (component, is_active);

-- ── Migration 003/004/005 — Hive Mind issues ──────────────────────────────────

CREATE TABLE IF NOT EXISTS issues (
    number                      INTEGER PRIMARY KEY,
    subject_line                TEXT,
    subject_line_48h            TEXT,
    preview_text                TEXT,
    character_name              TEXT,
    character_year              TEXT,
    character_location          TEXT,
    pillar                      TEXT,  -- Signal|Surrender|Renewal
    topic_summary               TEXT,
    page_url                    TEXT,
    page_slug                   TEXT,
    cover_image_url             TEXT,
    cover_image_prompt          TEXT,
    email_template_id           TEXT,
    campaign_id                 TEXT,
    long_form_body              TEXT,
    email_teaser_body           TEXT,
    until_next_teaser           TEXT,
    previous_issues_referenced  INTEGER[],
    read_time_min               INTEGER,
    word_count_long_form        INTEGER,
    word_count_email_teaser     INTEGER,
    drafted_at                  TIMESTAMPTZ DEFAULT NOW(),
    scheduled_send_at           TIMESTAMPTZ,
    published_at                TIMESTAMPTZ,
    status                      TEXT DEFAULT 'draft',
    run_id                      UUID,
    notes                       TEXT,
    -- Migration 004 page columns
    page_title                  TEXT,
    page_dek                    TEXT,
    page_breadcrumb_label       TEXT,
    shopify_image_id            TEXT,
    shopify_image_url           TEXT,
    shopify_page_id             TEXT,
    shopify_page_handle         TEXT,
    shopify_page_url            TEXT,
    page_published_at           TIMESTAMPTZ,
    -- Migration 005 Klaviyo columns
    klaviyo_campaign_id         TEXT,
    klaviyo_template_id         TEXT,
    klaviyo_message_id          TEXT,
    campaign_drafted_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS issues_status_number_idx ON issues (status, number DESC);
CREATE INDEX IF NOT EXISTS idx_issues_shopify_page_id ON issues (shopify_page_id) WHERE shopify_page_id IS NOT NULL;

-- ── Migration 005/009/011 — Calendar executions ───────────────────────────────

CREATE TABLE IF NOT EXISTS calendar_executions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id         UUID NOT NULL,
    slot_date           DATE NOT NULL,
    content_type        TEXT NOT NULL,
    audience            TEXT,
    topic_angle         TEXT,
    status              TEXT NOT NULL DEFAULT 'dispatched',
    notes               TEXT,
    executed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Migration 009 revenue tracking
    actual_revenue      NUMERIC DEFAULT 0,
    klaviyo_campaign_id TEXT,
    recipients          INTEGER DEFAULT 0,
    actual_rpr          NUMERIC,
    -- Migration 011 attribution
    is_preliminary      BOOLEAN,
    finalized_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_cal_exec_date     ON calendar_executions (slot_date);
CREATE INDEX IF NOT EXISTS idx_cal_exec_decision ON calendar_executions (decision_id);
CREATE INDEX IF NOT EXISTS idx_cal_exec_campaign ON calendar_executions (klaviyo_campaign_id) WHERE klaviyo_campaign_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cal_exec_final    ON calendar_executions (slot_date) WHERE is_preliminary = FALSE;

-- ── Migration 006 — Calendar approvals ───────────────────────────────────────

CREATE TABLE IF NOT EXISTS calendar_approvals (
    week_start  DATE PRIMARY KEY,
    token       TEXT NOT NULL,
    approved_at TIMESTAMPTZ,
    approved_by TEXT DEFAULT 'boris'
);

-- ── Migration 008 — Agent state key/value ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent_state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Migration 010 — SEO topics queue ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS seo_topics (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    keyword             TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',  -- pending|published|error
    published_url       TEXT,
    shopify_article_id  TEXT,
    error_detail        TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at        TIMESTAMPTZ
);

-- ── Finalization helper (run in every ingestion pass) ─────────────────────────
-- UPDATE performance SET is_preliminary = FALSE, finalized_at = NOW()
-- WHERE is_preliminary = TRUE AND measured_at < NOW() - INTERVAL '72 hours';
