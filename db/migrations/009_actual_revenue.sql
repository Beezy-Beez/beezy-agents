-- Migration 009: actual revenue tracking on calendar_executions
ALTER TABLE calendar_executions
  ADD COLUMN IF NOT EXISTS actual_revenue   NUMERIC(10,2) DEFAULT 0,
  ADD COLUMN IF NOT EXISTS klaviyo_campaign_id TEXT,
  ADD COLUMN IF NOT EXISTS recipients       INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS actual_rpr       NUMERIC(8,4);

CREATE INDEX IF NOT EXISTS idx_cal_exec_campaign
  ON calendar_executions (klaviyo_campaign_id)
  WHERE klaviyo_campaign_id IS NOT NULL;
