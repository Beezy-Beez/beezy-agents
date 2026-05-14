-- Migration 007: attribution flags on performance table
-- Marks Klaviyo campaign performance as preliminary until 72h post-send,
-- then flips to final. Learning loop only reads final numbers.

ALTER TABLE performance
  ADD COLUMN IF NOT EXISTS is_preliminary BOOLEAN NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS finalized_at   TIMESTAMPTZ;

-- Auto-finalize rows older than 72h (run on each ingestion pass)
CREATE OR REPLACE FUNCTION finalize_performance() RETURNS void AS $$
  UPDATE performance
     SET is_preliminary = false,
         finalized_at   = NOW()
   WHERE is_preliminary = true
     AND measured_at < NOW() - INTERVAL '72 hours';
$$ LANGUAGE sql;

-- Index for learning loop queries (only reads final)
CREATE INDEX IF NOT EXISTS idx_perf_final
  ON performance (metric_name, measured_at)
  WHERE is_preliminary = false;

COMMENT ON COLUMN performance.is_preliminary IS
  'True until 72h post-send — Klaviyo attribution is still accruing';
