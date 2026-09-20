-- 002_outcomes.sql — post-publication lifecycle (what /outcome tracks upstream).
-- The pipeline state machine ends at PUBLISHED; what happens after the user
-- submits by hand (applied → interview → offer/hired/rejected/no_response)
-- is outcome data, recorded here.

CREATE TABLE IF NOT EXISTS outcome_events (
    id              BIGSERIAL PRIMARY KEY,
    application_id  TEXT NOT NULL REFERENCES applications(application_id),
    outcome         TEXT NOT NULL
        CHECK (outcome IN ('applied','interview','offer','hired','rejected',
                           'no_response','offer_declined','withdrawn')),
    notes           TEXT,
    source          TEXT,   -- e.g. 'user', 'gmail-sync'
    at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE applications ADD COLUMN IF NOT EXISTS outcome TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS outcome_updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_outcome_events_app ON outcome_events(application_id);

INSERT INTO schema_migrations (version) VALUES ('002_outcomes')
ON CONFLICT (version) DO NOTHING;
