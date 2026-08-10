-- Isolated S3-compatible archive for validated market-data payloads.

CREATE TABLE object_archive_runs (
    id BIGSERIAL PRIMARY KEY,
    run_key VARCHAR(100) NOT NULL UNIQUE,
    evaluated_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) NOT NULL,
    reason_code VARCHAR(50) NOT NULL,
    selected_count INTEGER NOT NULL,
    archived_count INTEGER NOT NULL,
    uploaded_count INTEGER NOT NULL,
    existing_count INTEGER NOT NULL,
    failed_count INTEGER NOT NULL,
    compressed_bytes BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_object_archive_run_identity CHECK (
        run_key ~ '^object-archive:[A-Za-z0-9][A-Za-z0-9:-]{0,84}$'
        AND reason_code ~ '^[A-Z][A-Z0-9_]{1,49}$'
    ),
    CONSTRAINT ck_object_archive_run_counts CHECK (
        selected_count BETWEEN 0 AND 100
        AND archived_count BETWEEN 0 AND selected_count
        AND uploaded_count BETWEEN 0 AND archived_count
        AND existing_count BETWEEN 0 AND archived_count
        AND uploaded_count + existing_count = archived_count
        AND failed_count BETWEEN 0 AND selected_count
        AND archived_count + failed_count = selected_count
        AND compressed_bytes >= 0
        AND (
            archived_count = 0
            OR compressed_bytes > 0
        )
    ),
    CONSTRAINT ck_object_archive_run_state CHECK (
        (
            status = 'SKIPPED'
            AND reason_code = 'NO_PENDING'
            AND selected_count = 0
        )
        OR (
            status = 'SUCCEEDED'
            AND reason_code = 'ARCHIVED'
            AND selected_count > 0
            AND archived_count = selected_count
            AND failed_count = 0
        )
        OR (
            status = 'PARTIAL'
            AND reason_code = 'OBJECT_STORAGE_ERROR'
            AND archived_count > 0
            AND failed_count > 0
        )
        OR (
            status = 'FAILED'
            AND reason_code = 'OBJECT_STORAGE_ERROR'
            AND selected_count > 0
            AND archived_count = 0
            AND failed_count = selected_count
        )
    )
);

CREATE INDEX idx_object_archive_runs_latest
    ON object_archive_runs(evaluated_at DESC, id DESC);

CREATE TABLE market_data_object_archives (
    id BIGSERIAL PRIMARY KEY,
    market_data_file_id BIGINT NOT NULL UNIQUE
        REFERENCES market_data_files(id) ON DELETE RESTRICT,
    run_id BIGINT NOT NULL
        REFERENCES object_archive_runs(id) ON DELETE RESTRICT,
    storage_backend VARCHAR(40) NOT NULL DEFAULT 'garage-s3-v1',
    bucket VARCHAR(63) NOT NULL,
    object_key TEXT NOT NULL UNIQUE,
    compressed_checksum_sha256 CHAR(64) NOT NULL,
    compressed_bytes INTEGER NOT NULL,
    uploaded BOOLEAN NOT NULL,
    archived_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_market_data_object_archive_storage CHECK (
        storage_backend = 'garage-s3-v1'
        AND bucket ~ '^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$'
        AND bucket NOT LIKE '%..%'
        AND object_key ~ (
            '^trading-agent/market-data/[a-z0-9-]+/'
            '[a-z0-9-]+/[0-9]{4}/[0-9]{2}/[0-9]{2}/'
            '[1-9][0-9]*-[0-9a-f]{64}[.]gz$'
        )
        AND LENGTH(object_key) <= 1024
        AND compressed_checksum_sha256 ~ '^[0-9a-f]{64}$'
        AND compressed_bytes > 0
    )
);

CREATE INDEX idx_market_data_object_archives_latest
    ON market_data_object_archives(archived_at DESC, id DESC);

CREATE OR REPLACE FUNCTION reject_object_archive_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'object archive evidence is append-only';
END;
$$;

CREATE TRIGGER trg_object_archive_runs_no_update
BEFORE UPDATE OR DELETE ON object_archive_runs
FOR EACH ROW
EXECUTE FUNCTION reject_object_archive_mutation();

CREATE TRIGGER trg_market_data_object_archives_no_update
BEFORE UPDATE OR DELETE ON market_data_object_archives
FOR EACH ROW
EXECUTE FUNCTION reject_object_archive_mutation();
