-- Durable leases for recurring analysis and study jobs.
-- Trade-producing slots are never replayed after they become stale.

CREATE TABLE scheduled_job_runs (
    job_key VARCHAR(160) PRIMARY KEY,
    job_name VARCHAR(32) NOT NULL,
    scheduled_at TIMESTAMPTZ NOT NULL,
    run_kind VARCHAR(16) NOT NULL,
    status VARCHAR(20) NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    claimed_at TIMESTAMPTZ NOT NULL,
    lease_expires_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    failure_code VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_scheduled_job_key CHECK (
        job_key ~ '^[a-z0-9][a-z0-9:._-]{2,159}$'
    ),
    CONSTRAINT ck_scheduled_job_name CHECK (
        job_name IN ('brain_cycle', 'student_study')
    ),
    CONSTRAINT ck_scheduled_job_kind CHECK (
        run_kind IN ('CURRENT', 'RECOVERY', 'STALE_SKIP')
    ),
    CONSTRAINT ck_scheduled_job_status CHECK (
        status IN ('CLAIMED', 'SUCCEEDED', 'FAILED', 'SKIPPED_STALE')
    ),
    CONSTRAINT ck_scheduled_job_attempts CHECK (
        attempt_count BETWEEN 1 AND 100
    ),
    CONSTRAINT ck_scheduled_job_times CHECK (
        claimed_at >= scheduled_at
        AND (
            lease_expires_at IS NULL
            OR lease_expires_at > claimed_at
        )
        AND (
            completed_at IS NULL
            OR completed_at >= claimed_at
        )
    ),
    CONSTRAINT ck_scheduled_job_outcome CHECK (
        (
            status = 'CLAIMED'
            AND lease_expires_at IS NOT NULL
            AND completed_at IS NULL
            AND failure_code IS NULL
        )
        OR (
            status IN ('SUCCEEDED', 'SKIPPED_STALE')
            AND lease_expires_at IS NULL
            AND completed_at IS NOT NULL
            AND failure_code IS NULL
        )
        OR (
            status = 'FAILED'
            AND lease_expires_at IS NULL
            AND completed_at IS NOT NULL
            AND failure_code ~ '^[A-Z][A-Z0-9_]{2,63}$'
        )
    )
);

CREATE INDEX idx_scheduled_job_latest_terminal
    ON scheduled_job_runs(job_name, scheduled_at DESC)
    WHERE status IN ('SUCCEEDED', 'SKIPPED_STALE');

CREATE INDEX idx_scheduled_job_expired_lease
    ON scheduled_job_runs(lease_expires_at)
    WHERE status = 'CLAIMED';
