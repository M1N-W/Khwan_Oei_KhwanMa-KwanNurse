-- Render PostgreSQL transactional persistence. Apply through the migration
-- script; this file is deliberately idempotent for repeatable deployments.
CREATE TABLE IF NOT EXISTS patient_profiles (
    user_id TEXT PRIMARY KEY,
    profile JSONB NOT NULL,
    registration_status TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS patient_profiles_registration_status_idx
    ON patient_profiles (registration_status);

CREATE TABLE IF NOT EXISTS survey_schedules (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES patient_profiles(user_id),
    milestone_day INTEGER NOT NULL,
    survey_url TEXT NOT NULL,
    tracking_token TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'scheduled',
    scheduled_at TIMESTAMPTZ NOT NULL,
    sent_at TIMESTAMPTZ,
    clicked_at TIMESTAMPTZ,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    UNIQUE (user_id, milestone_day)
);
CREATE INDEX IF NOT EXISTS survey_schedules_due_idx
    ON survey_schedules (status, scheduled_at);

CREATE TABLE IF NOT EXISTS idempotency_records (
    operation_key TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idempotency_records_expiry_idx
    ON idempotency_records (expires_at);

CREATE TABLE IF NOT EXISTS outbox_jobs (
    id BIGSERIAL PRIMARY KEY,
    job_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS outbox_jobs_ready_idx
    ON outbox_jobs (status, available_at);
