-- Extend the isolated object archive to retained ESMA reference source files.
-- Public Nasdaq pre-trade report bodies remain ephemeral by policy.

CREATE TABLE reference_data_object_archives (
    id BIGSERIAL PRIMARY KEY,
    reference_data_file_id BIGINT NOT NULL UNIQUE
        REFERENCES reference_data_files(id) ON DELETE RESTRICT,
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
    CONSTRAINT ck_reference_data_object_archive_storage CHECK (
        storage_backend = 'garage-s3-v1'
        AND bucket ~ '^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$'
        AND bucket NOT LIKE '%..%'
        AND object_key ~ (
            '^trading-agent/reference-data/[a-z0-9-]+/'
            'reference-universe/[0-9]{4}/[0-9]{2}/[0-9]{2}/'
            '[1-9][0-9]*-[0-9a-f]{64}[.]gz$'
        )
        AND LENGTH(object_key) <= 1024
        AND compressed_checksum_sha256 ~ '^[0-9a-f]{64}$'
        AND compressed_bytes > 0
    )
);

CREATE INDEX idx_reference_data_object_archives_latest
    ON reference_data_object_archives(archived_at DESC, id DESC);

CREATE TRIGGER trg_reference_data_object_archives_no_update
BEFORE UPDATE OR DELETE ON reference_data_object_archives
FOR EACH ROW
EXECUTE FUNCTION reject_object_archive_mutation();
