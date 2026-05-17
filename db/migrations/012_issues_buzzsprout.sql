-- Migration 012 — Buzzsprout audio embed URL for Hive Mind issues
-- Run: psql "$DATABASE_URL" -f db/migrations/012_issues_buzzsprout.sql

ALTER TABLE issues ADD COLUMN IF NOT EXISTS buzzsprout_url TEXT;
