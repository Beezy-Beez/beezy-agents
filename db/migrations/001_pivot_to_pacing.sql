-- Migration 001: pivot to pacing/ingestion-centric schema.
-- Drops the old multi-agent v0 tables and creates the v1 schema.
-- Safe to run as a single transaction.

begin;

-- ===== Drop old v0 schema =====
drop table if exists strategies cascade;
drop table if exists decisions cascade;
drop table if exists outcomes cascade;
drop table if exists reviews cascade;
drop table if exists artifacts cascade;
drop table if exists tasks cascade;
drop table if exists goals cascade;

-- ===== Create v1 schema (mirror of schema.sql) =====
create table goals (
  id              uuid primary key default gen_random_uuid(),
  title           text not null,
  target_metric   text not null,
  target_value    numeric,
  period_start    date,
  period_end      date,
  status          text not null default 'active',
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create table pacing_state (
  id                       uuid primary key default gen_random_uuid(),
  goal_id                  uuid not null references goals(id),
  measured_at              timestamptz not null default now(),
  period_to_date_value     numeric,
  target_to_date_value     numeric,
  gap_pct                  numeric,
  days_remaining           int,
  required_daily_rate      numeric
);
create index on pacing_state (goal_id, measured_at);

create table priorities (
  id                    uuid primary key default gen_random_uuid(),
  decided_at            timestamptz not null default now(),
  effective_for         date not null,
  prioritized_workers   jsonb not null,
  reasoning             text,
  pacing_snapshot       jsonb
);
create index on priorities (effective_for);

create table runs (
  id                uuid primary key default gen_random_uuid(),
  worker            text not null,
  triggered_by      text not null,
  priority_id       uuid references priorities(id),
  status            text not null default 'queued',
  autonomy_tier     smallint not null default 2,
  approval_status   text,
  input             jsonb not null default '{}'::jsonb,
  output            jsonb,
  attempts          int not null default 0,
  max_attempts      int not null default 3,
  error             text,
  created_at        timestamptz not null default now(),
  started_at        timestamptz,
  completed_at      timestamptz
);
create index on runs (status, worker);
create index on runs (priority_id);

create table ingestion_runs (
  id                  uuid primary key default gen_random_uuid(),
  source              text not null,
  window_start        timestamptz,
  window_end          timestamptz,
  records_ingested    int,
  status              text not null,
  error               text,
  created_at          timestamptz not null default now()
);
create index on ingestion_runs (source, created_at);

create table performance (
  id              uuid primary key default gen_random_uuid(),
  run_id          uuid references runs(id),
  source          text not null,
  metric_name     text not null,
  metric_value    numeric,
  dimensions      jsonb,
  window_start    timestamptz,
  window_end      timestamptz,
  measured_at     timestamptz not null default now()
);
create index on performance (run_id, metric_name);
create index on performance (source, measured_at);
create index on performance (metric_name, measured_at);

create table decisions (
  id              uuid primary key default gen_random_uuid(),
  decided_by      text not null default 'pacing_brain',
  decision_type   text not null,
  input_context   jsonb,
  reasoning       text,
  output          jsonb,
  created_at      timestamptz not null default now()
);
create index on decisions (decision_type, created_at);

create table strategies (
  id                    uuid primary key default gen_random_uuid(),
  component             text not null,
  strategy_text         text not null,
  approved_by           text,
  approved_at           timestamptz,
  supersedes_id         uuid references strategies(id),
  evidence_decision_id  uuid references decisions(id),
  is_active             boolean not null default false,
  created_at            timestamptz not null default now()
);
create index on strategies (component, is_active);

commit;
