-- Auditable, point-in-time reference snapshots for the XSTO universe.

ALTER TABLE instruments
    ALTER COLUMN symbol DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS cfi_code CHAR(6),
    ADD COLUMN IF NOT EXISTS reference_snapshot_date DATE;

ALTER TABLE instruments
    ADD CONSTRAINT ck_instruments_cfi_code CHECK (
        cfi_code IS NULL OR cfi_code ~ '^[A-Z0-9]{6}$'
    );

CREATE INDEX IF NOT EXISTS idx_instruments_reference_snapshot
    ON instruments(source, mic, reference_snapshot_date DESC);

CREATE TABLE IF NOT EXISTS reference_data_snapshots (
    id BIGSERIAL PRIMARY KEY,
    provider VARCHAR(100) NOT NULL,
    mic CHAR(4) NOT NULL,
    snapshot_date DATE NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    universe_checksum_sha256 CHAR(64) NOT NULL,
    file_count INTEGER NOT NULL,
    instrument_count INTEGER NOT NULL,
    sync_run_id BIGINT NOT NULL REFERENCES data_sync_runs(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_reference_data_snapshot UNIQUE (
        provider, mic, snapshot_date
    ),
    CONSTRAINT uq_reference_data_snapshot_sync UNIQUE (sync_run_id),
    CONSTRAINT ck_reference_data_snapshot_mic CHECK (
        mic ~ '^[A-Z0-9]{4}$'
    ),
    CONSTRAINT ck_reference_data_snapshot_checksum CHECK (
        universe_checksum_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_reference_data_snapshot_counts CHECK (
        file_count > 0 AND instrument_count > 0
    ),
    CONSTRAINT ck_reference_data_snapshot_time CHECK (
        snapshot_date <= received_at::DATE
    )
);

CREATE INDEX IF NOT EXISTS idx_reference_data_snapshots_latest
    ON reference_data_snapshots(provider, mic, snapshot_date DESC);

CREATE TABLE IF NOT EXISTS reference_data_files (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL
        REFERENCES reference_data_snapshots(id) ON DELETE CASCADE,
    file_name VARCHAR(255) NOT NULL,
    source_url TEXT NOT NULL,
    checksum_sha256 CHAR(64) NOT NULL,
    upstream_checksum_algorithm VARCHAR(20),
    upstream_checksum VARCHAR(128),
    content_encoding VARCHAR(20) NOT NULL,
    compressed_content BYTEA NOT NULL,
    uncompressed_bytes INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_reference_data_file UNIQUE (snapshot_id, file_name),
    CONSTRAINT ck_reference_data_file_checksum CHECK (
        checksum_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_reference_data_file_upstream_checksum CHECK (
        (
            upstream_checksum_algorithm IS NULL
            AND upstream_checksum IS NULL
        )
        OR (
            upstream_checksum_algorithm = 'md5'
            AND upstream_checksum ~ '^[0-9a-f]{32}$'
        )
        OR (
            upstream_checksum_algorithm = 'sha256'
            AND upstream_checksum ~ '^[0-9a-f]{64}$'
        )
    ),
    CONSTRAINT ck_reference_data_file_encoding CHECK (
        content_encoding = 'gzip'
    ),
    CONSTRAINT ck_reference_data_file_content CHECK (
        OCTET_LENGTH(compressed_content) > 0
        AND uncompressed_bytes > 0
    ),
    CONSTRAINT ck_reference_data_file_url CHECK (
        source_url LIKE 'https://%'
    )
);
