-- Migration 010: seo_topics — tracks pending and published SEO blog articles
CREATE TABLE IF NOT EXISTS seo_topics (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    keyword         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | published | error
    published_url   TEXT,
    shopify_article_id TEXT,
    error_detail    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_seo_topics_status ON seo_topics (status);
