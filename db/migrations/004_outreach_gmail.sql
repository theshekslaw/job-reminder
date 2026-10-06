-- 004_outreach_gmail.sql — HR / referral outreach records + Gmail sync idempotency.
-- Invariant: the system DRAFTS outreach; the user sends it. A row becomes 'sent' only
-- when the user confirms they sent it.

CREATE TABLE IF NOT EXISTS outreach (
    id              BIGSERIAL PRIMARY KEY,
    application_id  TEXT NOT NULL REFERENCES applications(application_id),
    kind            TEXT NOT NULL
        CHECK (kind IN ('referral_request','hr_intro','follow_up','thank_you','other')),
    channel         TEXT NOT NULL CHECK (channel IN ('email','linkedin','other')),
    recipient_name  TEXT,
    recipient_role  TEXT,
    recipient_contact TEXT,   -- supplied by the user; never guessed
    draft_path      TEXT,     -- documents/applications/<company>_<role>/outreach/<file>.md
    status          TEXT NOT NULL DEFAULT 'drafted'
        CHECK (status IN ('drafted','sent','replied','cancelled')),
    drafted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ,
    replied_at      TIMESTAMPTZ,
    notes           TEXT
);

-- /gmail-sync: every message already looked at (approved, skipped or unmatched), so a
-- re-run never re-proposes the same email. Message bodies are NOT stored.
CREATE TABLE IF NOT EXISTS gmail_processed (
    message_id      TEXT PRIMARY KEY,
    application_id  TEXT REFERENCES applications(application_id),
    classification  TEXT,     -- ack | assessment | interview | offer | rejection | outreach_reply | unmatched | noise
    decision        TEXT,     -- written | skipped | unmatched | conflict
    email_date      TIMESTAMPTZ,
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_outreach_app ON outreach(application_id);

INSERT INTO schema_migrations (version) VALUES ('004_outreach_gmail')
ON CONFLICT (version) DO NOTHING;
