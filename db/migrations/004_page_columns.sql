-- 004_page_columns.sql
-- Adds page-related fields for native Shopify Pages publishing (Phase 3A.4).

alter table issues
    add column if not exists page_title text,
    add column if not exists page_dek text,
    add column if not exists page_breadcrumb_label text,
    add column if not exists shopify_image_id text,
    add column if not exists shopify_image_url text,
    add column if not exists shopify_page_id text,
    add column if not exists shopify_page_handle text,
    add column if not exists shopify_page_url text,
    add column if not exists page_published_at timestamptz;

create index if not exists idx_issues_shopify_page_id
    on issues (shopify_page_id) where shopify_page_id is not null;
