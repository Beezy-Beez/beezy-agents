-- 013_jobs_control_plane.sql
-- The control-plane jobs table: single source of truth for "what ran, when,
-- did it work." One row per discrete dispatched job (NOT the Slack 5s poll).
-- Idempotent — safe to re-run.

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
