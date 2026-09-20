-- 001_init.sql — job-hunt records schema (see architecture/DESIGN.md)

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Normalized job identity: application_id = sha256(company+role+location)[:16].
-- Source-specific ids live in job_sources (one job, N boards).
CREATE TABLE IF NOT EXISTS job_postings (
    application_id  TEXT PRIMARY KEY,
    company         TEXT NOT NULL,
    title           TEXT NOT NULL,
    location        TEXT,
    remote_type     TEXT,
    description     TEXT,
    salary          TEXT,
    posted_at       DATE,
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS job_sources (
    id              BIGSERIAL PRIMARY KEY,
    application_id  TEXT NOT NULL REFERENCES job_postings(application_id),
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    url             TEXT,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, source_id)
);

-- State machine (DESIGN.md). Overrides are recorded columns, never hidden booleans.
CREATE TABLE IF NOT EXISTS applications (
    application_id           TEXT PRIMARY KEY REFERENCES job_postings(application_id),
    state                    TEXT NOT NULL DEFAULT 'DISCOVERED'
        CHECK (state IN ('DISCOVERED','EVALUATED','DRAFTED','COVERAGE_CHECK',
                         'COVERAGE_BLOCKED','VALIDATED','AWAITING_APPROVAL',
                         'REJECTED','PUBLISHED')),
    fit_rating               INTEGER,
    keyword_coverage_score   NUMERIC(5,2),
    revision_count           INTEGER NOT NULL DEFAULT 0,
    cv_path                  TEXT,
    resume_path              TEXT,
    override_reason          TEXT,
    override_actor           TEXT,
    override_at              TIMESTAMPTZ,
    original_coverage_score  NUMERIC(5,2),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Audit trail: every state change.
CREATE TABLE IF NOT EXISTS transitions (
    id              BIGSERIAL PRIMARY KEY,
    application_id  TEXT NOT NULL REFERENCES applications(application_id),
    from_state      TEXT,
    to_state        TEXT NOT NULL,
    reason          TEXT,
    actor           TEXT NOT NULL DEFAULT 'system',
    at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- /rank explainability: "why was this discarded?" is always answerable.
CREATE TABLE IF NOT EXISTS rankings (
    id                    BIGSERIAL PRIMARY KEY,
    application_id        TEXT NOT NULL REFERENCES job_postings(application_id),
    rank_score            INTEGER,
    matched_requirements  JSONB,
    missing_requirements  JSONB,
    reason                TEXT,
    model                 TEXT,
    at                    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Observability: parent/child correlation shows one /apply run as a tree
-- (evaluation, draft attempts, coverage checks, revisions).
CREATE TABLE IF NOT EXISTS runs (
    run_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_run_id   UUID REFERENCES runs(run_id),
    application_id  TEXT REFERENCES job_postings(application_id),
    command         TEXT,
    stage           TEXT NOT NULL,
    attempt         INTEGER NOT NULL DEFAULT 1,
    model           TEXT,
    tokens_in       BIGINT,
    tokens_out      BIGINT,
    duration_ms     BIGINT,
    result          TEXT,
    at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Everything the human gate needs, persisted before any headless process exits,
-- so `job-hunt queue` reconstructs full review context.
CREATE TABLE IF NOT EXISTS review_packets (
    id                   BIGSERIAL PRIMARY KEY,
    application_id       TEXT NOT NULL REFERENCES applications(application_id),
    draft_tex_path       TEXT,
    pdf_path             TEXT,
    coverage_report      JSONB,
    tailoring_decisions  JSONB,
    reviewer_output      JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_applications_state ON applications(state);
CREATE INDEX IF NOT EXISTS idx_transitions_app    ON transitions(application_id);
CREATE INDEX IF NOT EXISTS idx_runs_app           ON runs(application_id);
CREATE INDEX IF NOT EXISTS idx_job_sources_app    ON job_sources(application_id);

INSERT INTO schema_migrations (version) VALUES ('001_init')
ON CONFLICT (version) DO NOTHING;
