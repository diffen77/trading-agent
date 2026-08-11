-- Legal/storage entitlement and host-key provenance for Nasdaq aliases.

CREATE TABLE IF NOT EXISTS reference_data_entitlements (
    id BIGSERIAL PRIMARY KEY,
    contract_key VARCHAR(100) NOT NULL UNIQUE,
    provider VARCHAR(100) NOT NULL,
    product_name VARCHAR(255) NOT NULL,
    mic CHAR(4) NOT NULL,
    transport VARCHAR(30) NOT NULL,
    usage_scope VARCHAR(50) NOT NULL,
    external_distribution BOOLEAN NOT NULL DEFAULT FALSE,
    raw_storage_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    derived_storage_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    retention_policy VARCHAR(500) NOT NULL,
    terms_url TEXT NOT NULL,
    terms_checksum_sha256 CHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    valid_from DATE NOT NULL,
    valid_until DATE NOT NULL,
    legal_reviewed_at TIMESTAMPTZ,
    storage_reviewed_at TIMESTAMPTZ,
    entitlement_verified_at TIMESTAMPTZ,
    host_key_algorithm VARCHAR(100),
    host_key_fingerprint_sha256 VARCHAR(50),
    reviewed_by VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_reference_entitlement_key CHECK (
        contract_key ~ '^[a-z0-9][a-z0-9-]{1,99}$'
    ),
    CONSTRAINT ck_reference_entitlement_provider CHECK (
        provider ~ '^[a-z0-9][a-z0-9-]{1,99}$'
    ),
    CONSTRAINT ck_reference_entitlement_mic CHECK (
        mic ~ '^[A-Z0-9]{4}$'
    ),
    CONSTRAINT ck_reference_entitlement_transport CHECK (
        transport = 'SFTP'
    ),
    CONSTRAINT ck_reference_entitlement_usage CHECK (
        usage_scope = 'INTERNAL_ANALYSIS_AND_PAPER'
    ),
    CONSTRAINT ck_reference_entitlement_terms CHECK (
        terms_url LIKE 'https://%'
        AND terms_checksum_sha256 ~ '^[0-9a-f]{64}$'
        AND LENGTH(BTRIM(retention_policy)) BETWEEN 1 AND 500
    ),
    CONSTRAINT ck_reference_entitlement_status CHECK (
        status IN ('DRAFT', 'VALIDATED', 'REVOKED')
    ),
    CONSTRAINT ck_reference_entitlement_dates CHECK (
        valid_until >= valid_from
    ),
    CONSTRAINT ck_reference_entitlement_host_key CHECK (
        (
            host_key_algorithm IS NULL
            AND host_key_fingerprint_sha256 IS NULL
        )
        OR (
            host_key_algorithm ~
                '^[A-Za-z0-9][A-Za-z0-9@._+-]{2,99}$'
            AND host_key_fingerprint_sha256 ~
                '^SHA256:[A-Za-z0-9+/]{43}$'
        )
    ),
    CONSTRAINT ck_reference_entitlement_reviewed_by CHECK (
        LENGTH(BTRIM(reviewed_by)) BETWEEN 1 AND 100
    ),
    CONSTRAINT ck_reference_entitlement_validated CHECK (
        status != 'VALIDATED'
        OR (
            external_distribution = FALSE
            AND raw_storage_allowed = TRUE
            AND derived_storage_allowed = TRUE
            AND legal_reviewed_at IS NOT NULL
            AND storage_reviewed_at IS NOT NULL
            AND entitlement_verified_at IS NOT NULL
            AND host_key_algorithm IS NOT NULL
            AND host_key_fingerprint_sha256 IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_reference_data_entitlement_runtime
    ON reference_data_entitlements(
        provider,
        mic,
        status,
        valid_until
    );

ALTER TABLE instrument_alias_snapshots
    ADD COLUMN IF NOT EXISTS entitlement_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_instrument_alias_snapshot_entitlement'
          AND conrelid = 'instrument_alias_snapshots'::regclass
    ) THEN
        ALTER TABLE instrument_alias_snapshots
            ADD CONSTRAINT fk_instrument_alias_snapshot_entitlement
            FOREIGN KEY (entitlement_id)
            REFERENCES reference_data_entitlements(id)
            ON DELETE RESTRICT;
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_instrument_alias_snapshot_entitlement
    ON instrument_alias_snapshots(entitlement_id);

CREATE OR REPLACE FUNCTION validate_instrument_alias_snapshot_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    reference_snapshot reference_data_snapshots%ROWTYPE;
    entitlement reference_data_entitlements%ROWTYPE;
    frozen_count INTEGER;
    verified_count INTEGER;
BEGIN
    SELECT *
    INTO reference_snapshot
    FROM reference_data_snapshots
    WHERE id = NEW.reference_snapshot_id
    FOR SHARE;

    IF (
        reference_snapshot.id IS NULL
        OR reference_snapshot.provider != 'esma-firds'
        OR reference_snapshot.mic != NEW.mic
        OR reference_snapshot.instrument_count != NEW.instrument_count
        OR reference_snapshot.snapshot_date > NEW.business_date
        OR NEW.business_date - reference_snapshot.snapshot_date > 10
    ) THEN
        RAISE EXCEPTION
            'alias snapshot does not match a current frozen FIRDS universe';
    END IF;

    SELECT *
    INTO entitlement
    FROM reference_data_entitlements
    WHERE id = NEW.entitlement_id
    FOR SHARE;

    IF (
        entitlement.id IS NULL
        OR entitlement.provider != NEW.provider
        OR entitlement.mic != NEW.mic
        OR entitlement.transport != 'SFTP'
        OR entitlement.usage_scope != 'INTERNAL_ANALYSIS_AND_PAPER'
        OR entitlement.status != 'VALIDATED'
        OR entitlement.external_distribution
        OR NOT entitlement.raw_storage_allowed
        OR NOT entitlement.derived_storage_allowed
        OR NEW.business_date > (
            NOW() AT TIME ZONE 'Europe/Stockholm'
        )::DATE
        OR NEW.received_at > NOW()
        OR NEW.business_date NOT BETWEEN
            entitlement.valid_from AND entitlement.valid_until
        OR entitlement.legal_reviewed_at IS NULL
        OR entitlement.legal_reviewed_at > NEW.received_at
        OR entitlement.storage_reviewed_at IS NULL
        OR entitlement.storage_reviewed_at > NEW.received_at
        OR entitlement.entitlement_verified_at IS NULL
        OR entitlement.entitlement_verified_at > NEW.received_at
        OR entitlement.host_key_algorithm IS NULL
        OR entitlement.host_key_fingerprint_sha256 IS NULL
    ) THEN
        RAISE EXCEPTION
            'alias snapshot lacks valid reference-data entitlement';
    END IF;

    SELECT
        COUNT(*)::INTEGER,
        COUNT(*) FILTER (
            WHERE frozen_evidence_status = 'VERIFIED'
        )::INTEGER
    INTO frozen_count, verified_count
    FROM reference_snapshot_instruments
    WHERE snapshot_id = NEW.reference_snapshot_id;

    IF (
        frozen_count != NEW.instrument_count
        OR verified_count != NEW.instrument_count
    ) THEN
        RAISE EXCEPTION
            'alias snapshot requires complete verified FIRDS membership';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_instrument_alias_head_write()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    candidate instrument_alias_snapshots%ROWTYPE;
    current_snapshot instrument_alias_snapshots%ROWTYPE;
    entitlement reference_data_entitlements%ROWTYPE;
    member_count INTEGER;
    stockholm_date DATE;
BEGIN
    SELECT *
    INTO candidate
    FROM instrument_alias_snapshots
    WHERE id = NEW.snapshot_id
    FOR SHARE;

    SELECT *
    INTO entitlement
    FROM reference_data_entitlements
    WHERE id = candidate.entitlement_id
    FOR SHARE;

    SELECT COUNT(*)::INTEGER
    INTO member_count
    FROM instrument_alias_snapshot_members
    WHERE snapshot_id = NEW.snapshot_id;

    stockholm_date := (
        NOW() AT TIME ZONE 'Europe/Stockholm'
    )::DATE;
    IF (
        candidate.id IS NULL
        OR candidate.provider != NEW.provider
        OR candidate.mic != NEW.mic
        OR member_count != candidate.instrument_count
        OR entitlement.id IS NULL
        OR entitlement.status != 'VALIDATED'
        OR entitlement.provider != candidate.provider
        OR entitlement.mic != candidate.mic
        OR entitlement.transport != 'SFTP'
        OR entitlement.usage_scope != 'INTERNAL_ANALYSIS_AND_PAPER'
        OR entitlement.external_distribution
        OR NOT entitlement.raw_storage_allowed
        OR NOT entitlement.derived_storage_allowed
        OR candidate.business_date > stockholm_date
        OR candidate.received_at > NOW()
        OR stockholm_date NOT BETWEEN
            entitlement.valid_from AND entitlement.valid_until
        OR entitlement.legal_reviewed_at IS NULL
        OR entitlement.legal_reviewed_at > NOW()
        OR entitlement.storage_reviewed_at IS NULL
        OR entitlement.storage_reviewed_at > NOW()
        OR entitlement.entitlement_verified_at IS NULL
        OR entitlement.entitlement_verified_at > NOW()
        OR entitlement.host_key_algorithm IS NULL
        OR entitlement.host_key_fingerprint_sha256 IS NULL
    ) THEN
        RAISE EXCEPTION
            'alias head requires complete entitled snapshot';
    END IF;

    IF TG_OP = 'UPDATE' THEN
        SELECT *
        INTO current_snapshot
        FROM instrument_alias_snapshots
        WHERE id = OLD.snapshot_id;

        IF (
            candidate.business_date < current_snapshot.business_date
            OR (
                candidate.business_date = current_snapshot.business_date
                AND candidate.id != current_snapshot.id
            )
        ) THEN
            RAISE EXCEPTION
                'alias head cannot move backwards or fork one date';
        END IF;
        NEW.updated_at := NOW();
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION guard_reference_data_entitlement_change()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'reference-data entitlement evidence is append-only';
    END IF;
    IF (
        OLD.status = 'VALIDATED'
        AND NEW.status = 'REVOKED'
        AND (
            to_jsonb(NEW) - 'status' - 'updated_at'
        ) = (
            to_jsonb(OLD) - 'status' - 'updated_at'
        )
    ) THEN
        NEW.updated_at := NOW();
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'reference-data entitlement evidence is append-only';
END;
$$;

CREATE TRIGGER trg_reference_data_entitlement_immutable
BEFORE UPDATE OR DELETE ON reference_data_entitlements
FOR EACH ROW EXECUTE FUNCTION guard_reference_data_entitlement_change();
