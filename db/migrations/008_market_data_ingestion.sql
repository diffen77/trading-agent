-- Atomic raw-file archive, sync audit, and gap tracking.

CREATE TABLE IF NOT EXISTS market_data_files (
    id BIGSERIAL PRIMARY KEY,
    provider VARCHAR(100) NOT NULL,
    data_type VARCHAR(50) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    report_minute TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    source_url TEXT NOT NULL,
    checksum_sha256 CHAR(64) NOT NULL,
    content_encoding VARCHAR(20) NOT NULL,
    compressed_content BYTEA NOT NULL,
    uncompressed_bytes INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_market_data_file_name UNIQUE (
        provider, data_type, file_name
    ),
    CONSTRAINT uq_market_data_file_minute UNIQUE (
        provider, data_type, report_minute
    ),
    CONSTRAINT ck_market_data_file_time CHECK (
        report_minute <= received_at
    ),
    CONSTRAINT ck_market_data_file_checksum CHECK (
        checksum_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_market_data_file_encoding CHECK (
        content_encoding = 'gzip'
    ),
    CONSTRAINT ck_market_data_file_content CHECK (
        OCTET_LENGTH(compressed_content) > 0
        AND uncompressed_bytes > 0
    ),
    CONSTRAINT ck_market_data_file_url CHECK (
        source_url LIKE 'https://%'
    )
);

CREATE INDEX IF NOT EXISTS idx_market_data_files_latest
    ON market_data_files(provider, data_type, report_minute DESC);

CREATE TABLE IF NOT EXISTS market_data_gaps (
    id BIGSERIAL PRIMARY KEY,
    provider VARCHAR(100) NOT NULL,
    data_type VARCHAR(50) NOT NULL,
    gap_start TIMESTAMPTZ NOT NULL,
    gap_end TIMESTAMPTZ NOT NULL,
    reason VARCHAR(500) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    CONSTRAINT uq_market_data_gap UNIQUE (
        provider, data_type, gap_start, gap_end
    ),
    CONSTRAINT ck_market_data_gap_range CHECK (
        gap_end >= gap_start
    ),
    CONSTRAINT ck_market_data_gap_status CHECK (
        status IN ('OPEN', 'RESOLVED')
    ),
    CONSTRAINT ck_market_data_gap_resolution CHECK (
        (status = 'OPEN' AND resolved_at IS NULL)
        OR (status = 'RESOLVED' AND resolved_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_market_data_gaps_open
    ON market_data_gaps(provider, data_type, gap_start)
    WHERE status = 'OPEN';

ALTER TABLE data_sync_runs
    ADD COLUMN IF NOT EXISTS run_key VARCHAR(500),
    ADD COLUMN IF NOT EXISTS data_file_id BIGINT
        REFERENCES market_data_files(id),
    ADD COLUMN IF NOT EXISTS records_inserted INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS first_event_time TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_event_time TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS gap_count INTEGER NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS uq_data_sync_run_key
    ON data_sync_runs(run_key)
    WHERE run_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_data_sync_file
    ON data_sync_runs(data_file_id)
    WHERE data_file_id IS NOT NULL;

ALTER TABLE data_sync_runs
    ADD CONSTRAINT ck_data_sync_inserted_counts CHECK (
        records_inserted >= 0
        AND records_inserted <= records_received
        AND gap_count >= 0
    ),
    ADD CONSTRAINT ck_data_sync_event_range CHECK (
        first_event_time IS NULL
        OR last_event_time IS NULL
        OR last_event_time >= first_event_time
    );
