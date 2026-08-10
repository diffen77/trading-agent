-- Versioned, auditable exchange calendars with correction history.

CREATE TABLE IF NOT EXISTS market_calendar_snapshots (
    id BIGSERIAL PRIMARY KEY,
    mic CHAR(4) NOT NULL,
    year INTEGER NOT NULL,
    source VARCHAR(100) NOT NULL,
    source_url TEXT NOT NULL,
    verified_at TIMESTAMPTZ NOT NULL,
    checksum_sha256 CHAR(64) NOT NULL,
    session_count INTEGER NOT NULL,
    closed_day_count INTEGER NOT NULL,
    half_day_count INTEGER NOT NULL,
    sync_run_id BIGINT NOT NULL REFERENCES data_sync_runs(id),
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    supersedes_snapshot_id BIGINT
        REFERENCES market_calendar_snapshots(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_market_calendar_version UNIQUE (
        mic, year, checksum_sha256
    ),
    CONSTRAINT uq_market_calendar_sync UNIQUE (sync_run_id),
    CONSTRAINT ck_market_calendar_mic CHECK (
        mic ~ '^[A-Z0-9]{4}$'
    ),
    CONSTRAINT ck_market_calendar_year CHECK (
        year BETWEEN 2000 AND 2100
    ),
    CONSTRAINT ck_market_calendar_checksum CHECK (
        checksum_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_market_calendar_counts CHECK (
        session_count > 0
        AND closed_day_count >= 0
        AND half_day_count >= 0
        AND half_day_count <= session_count
    ),
    CONSTRAINT ck_market_calendar_url CHECK (
        source_url LIKE 'https://%'
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_market_calendar_current
    ON market_calendar_snapshots(mic, year)
    WHERE is_current;

CREATE INDEX IF NOT EXISTS idx_market_calendar_history
    ON market_calendar_snapshots(mic, year, verified_at DESC);
