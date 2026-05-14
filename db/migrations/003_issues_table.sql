-- 003_issues_table.sql
-- Track every Hive Mind issue: published, scheduled, or draft.
-- Single source of truth for what issue is next + what the previous teaser was.

create table if not exists issues (
  number integer primary key,
  subject_line text,
  subject_line_48h text,
  preview_text text,
  character_name text,
  character_year text,
  character_location text,
  pillar text check (pillar in ('Signal', 'Surrender', 'Renewal')),
  topic_summary text,
  page_url text,
  page_slug text,
  cover_image_url text,
  cover_image_prompt text,
  email_template_id text,
  campaign_id text,
  long_form_body text,
  email_teaser_body text,
  until_next_teaser text,
  previous_issues_referenced integer[],
  read_time_min integer,
  word_count_long_form integer,
  word_count_email_teaser integer,
  drafted_at timestamptz default now(),
  scheduled_send_at timestamptz,
  published_at timestamptz,
  status text default 'draft' check (status in ('draft', 'scheduled', 'published')),
  run_id uuid,
  notes text
);

create index if not exists issues_status_number_idx on issues (status, number desc);

-- Backfill issues 012–014. teaser for 014 → assignment for issue 015.
insert into issues (number, subject_line, character_name, character_year, pillar, page_url, until_next_teaser, status, published_at) values
  (12,
   'Your exhale controls whether your brain can calm down',
   'Otto Loewi', '1921', 'Surrender',
   'https://trybeezybeez.com/pages/breathing-vagus-nerve-sleep-technique',
   null,
   'published', null),
  (13,
   'The Cat that wasn''t supposed to move',
   'Michel Jouvet', '1959', 'Renewal',
   'https://trybeezybeez.com/pages/dreams-rem-sleep-emotional-processing',
   null,
   'published', null),
  (14,
   'The Night Yale broke the nightcap',
   'Richard B. Yules', '1966', 'Renewal',
   'https://trybeezybeez.com/pages/alcohol-sleep-architecture-rem-suppression',
   'the thing about dreams that your brain is doing on purpose — and why forgetting them might be the point.',
   'published', null)
on conflict (number) do update set
  subject_line = excluded.subject_line,
  character_name = excluded.character_name,
  character_year = excluded.character_year,
  pillar = excluded.pillar,
  page_url = excluded.page_url,
  until_next_teaser = excluded.until_next_teaser,
  status = excluded.status;
