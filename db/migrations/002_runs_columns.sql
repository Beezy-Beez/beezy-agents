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
