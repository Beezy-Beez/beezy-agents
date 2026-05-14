-- Phase 3A.5: Klaviyo campaign tracking columns
ALTER TABLE issues
  ADD COLUMN IF NOT EXISTS klaviyo_campaign_id   TEXT,
  ADD COLUMN IF NOT EXISTS klaviyo_template_id   TEXT,
  ADD COLUMN IF NOT EXISTS klaviyo_message_id    TEXT,
  ADD COLUMN IF NOT EXISTS campaign_drafted_at   TIMESTAMPTZ;
