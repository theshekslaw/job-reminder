-- 003_jd_enrichment.sql — full job descriptions + posting liveness.
-- Scraped rows often carry an empty or truncated description; tools/fetch_jd.py
-- fetches the full text (LinkedIn via the portal CLI's `detail`, other hosts only
-- where robots.txt allows) and records where it came from.

ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS description_source     TEXT;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS description_fetched_at TIMESTAMPTZ;
-- NULL = unknown, false = the board reports the posting closed/expired.
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS posting_active         BOOLEAN;
-- Last fetch attempt outcome, so blocked/failed hosts are not retried every run.
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS description_fetch_note TEXT;

INSERT INTO schema_migrations (version) VALUES ('003_jd_enrichment')
ON CONFLICT (version) DO NOTHING;
