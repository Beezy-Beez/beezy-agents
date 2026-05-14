-- Migration 006: calendar_approvals
-- Stores weekly approval state. One row per week_start date.
CREATE TABLE IF NOT EXISTS calendar_approvals (
    week_start   DATE PRIMARY KEY,
    token        TEXT NOT NULL,
    approved_at  TIMESTAMPTZ,
    approved_by  TEXT DEFAULT 'boris'
);
