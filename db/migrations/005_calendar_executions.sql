CREATE TABLE IF NOT EXISTS calendar_executions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id     UUID NOT NULL,
    slot_date       DATE NOT NULL,
    content_type    TEXT NOT NULL,
    audience        TEXT,
    topic_angle     TEXT,
    status          TEXT NOT NULL DEFAULT 'dispatched',
    notes           TEXT,
    executed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cal_exec_date     ON calendar_executions (slot_date);
CREATE INDEX IF NOT EXISTS idx_cal_exec_decision ON calendar_executions (decision_id);
