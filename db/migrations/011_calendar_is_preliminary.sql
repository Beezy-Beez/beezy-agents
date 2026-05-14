-- Migration 011: add attribution finalization columns to calendar_executions
-- Mirrors the pattern on the performance table (migration 007).
-- is_preliminary = NULL  → row predates backfill; treat as unfinalized
-- is_preliminary = true  → within 72h attribution window, not yet backfilled
-- is_preliminary = false → backfilled; safe for learning loop
ALTER TABLE calendar_executions
  ADD COLUMN IF NOT EXISTS is_preliminary BOOLEAN,
  ADD COLUMN IF NOT EXISTS finalized_at   TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_cal_exec_final
  ON calendar_executions (slot_date)
  WHERE is_preliminary = false;
