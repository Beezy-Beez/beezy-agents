-- Tonight's Anchor — test-phase performance tracker.
-- Separate from calendar_executions during the 4-send test phase so the
-- kill-rule counter (completed_send_count, aggregate_rpr) has a clean
-- audit trail. Merge into calendar_executions only if the format survives.

CREATE TABLE IF NOT EXISTS tonight_anchor_performance (
    issue_number    INTEGER     PRIMARY KEY,
    campaign_id     TEXT        NOT NULL,
    template_id     TEXT,
    discount_code   TEXT        NOT NULL,
    discount_amount NUMERIC(10,2),
    audience_id     TEXT        NOT NULL,
    sent_at         TIMESTAMPTZ NOT NULL,
    recipients      INTEGER,
    opens           INTEGER,
    clicks          INTEGER,
    conversions     INTEGER,
    revenue         NUMERIC(12,2),
    rpr             NUMERIC(8,4),
    last_synced_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ta_perf_sent_at
    ON tonight_anchor_performance (sent_at DESC);
