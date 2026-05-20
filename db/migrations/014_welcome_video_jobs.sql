-- Migration 010: welcome_video_jobs
-- Job queue for personalized first-order welcome videos.
-- Webhook receives Klaviyo trigger -> INSERT row -> worker picks up -> renders video -> writes URL back to Klaviyo.

CREATE TABLE IF NOT EXISTS welcome_video_jobs (
    id              SERIAL PRIMARY KEY,

    -- idempotency: same Shopify order can't create duplicate jobs
    order_id        TEXT UNIQUE NOT NULL,

    -- inputs from Klaviyo webhook
    email           TEXT NOT NULL,
    first_name      TEXT NOT NULL,

    -- pipeline state
    -- pending: in queue, waiting for worker
    -- processing: worker is rendering right now
    -- complete: video URL written to Klaviyo, all done
    -- failed: worker hit an error, will retry
    -- dead: failed 3 times, gave up, needs human attention
    status          TEXT NOT NULL DEFAULT 'pending',

    -- retry tracking
    attempts        INT NOT NULL DEFAULT 0,

    -- HeyGen render references
    heygen_video_id TEXT,                -- HeyGen's internal job ID
    video_url       TEXT,                -- final public URL on R2

    -- error logging
    last_error      TEXT,

    -- timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_at       TIMESTAMPTZ,         -- watchdog: if processing > 10 min, reset to pending

    -- so we can search by email if a customer asks "where's my video"
    CONSTRAINT welcome_video_jobs_email_idx UNIQUE (order_id)
);

CREATE INDEX IF NOT EXISTS welcome_video_jobs_status_idx
    ON welcome_video_jobs (status, created_at);

CREATE INDEX IF NOT EXISTS welcome_video_jobs_email_lookup_idx
    ON welcome_video_jobs (email);
