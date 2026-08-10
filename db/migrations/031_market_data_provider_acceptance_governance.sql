-- Exact provider terms, storage rights, and append-only acceptance evidence.
-- Existing rows intentionally remain LEGACY_UNVERIFIED and fail runtime gates.

ALTER TABLE market_data_provider_contracts
    ADD COLUMN IF NOT EXISTS governance_evidence_status VARCHAR(30)
        NOT NULL DEFAULT 'LEGACY_UNVERIFIED',
    ADD COLUMN IF NOT EXISTS terms_checksum_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS raw_storage_allowed BOOLEAN
        NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS derived_storage_allowed BOOLEAN
        NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS retention_policy VARCHAR(500),
    ADD COLUMN IF NOT EXISTS proposed_by VARCHAR(100),
    ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR(100),
    ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS revoked_by VARCHAR(100),
    ADD COLUMN IF NOT EXISTS revocation_reason VARCHAR(500);

ALTER TABLE market_data_provider_validations
    ADD COLUMN IF NOT EXISTS governance_evidence_status VARCHAR(30)
        NOT NULL DEFAULT 'LEGACY_UNVERIFIED',
    ADD COLUMN IF NOT EXISTS reference_snapshot_id BIGINT
        REFERENCES reference_data_snapshots(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS reference_checksum_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS acceptance_session_count INTEGER,
    ADD COLUMN IF NOT EXISTS first_session_date DATE,
    ADD COLUMN IF NOT EXISTS last_session_date DATE,
    ADD COLUMN IF NOT EXISTS acceptance_checksum_sha256 CHAR(64);

DO $$
BEGIN
    ALTER TABLE market_data_provider_contracts
        DROP CONSTRAINT IF EXISTS ck_provider_contract_governance_v31;
    ALTER TABLE market_data_provider_contracts
        ADD CONSTRAINT ck_provider_contract_governance_v31
        CHECK (
                governance_evidence_status IN (
                    'LEGACY_UNVERIFIED',
                    'PENDING',
                    'VERIFIED'
                )
                AND (
                    terms_checksum_sha256 IS NULL
                    OR terms_checksum_sha256 ~ '^[0-9a-f]{64}$'
                )
                AND (
                    governance_evidence_status != 'PENDING'
                    OR (
                        status = 'DRAFT'
                        AND terms_checksum_sha256 IS NULL
                        AND raw_storage_allowed = FALSE
                        AND derived_storage_allowed = FALSE
                        AND retention_policy IS NULL
                        AND proposed_by
                            ~ '^operator:[A-Za-z0-9._-]{1,80}$'
                        AND reviewed_by IS NULL
                        AND legal_reviewed_at IS NULL
                        AND transport_verified_at IS NULL
                    )
                )
                AND (
                    governance_evidence_status != 'VERIFIED'
                    OR (
                        status IN ('VALIDATED', 'REVOKED')
                        AND terms_checksum_sha256 IS NOT NULL
                        AND raw_storage_allowed = TRUE
                        AND derived_storage_allowed = TRUE
                        AND LENGTH(BTRIM(retention_policy))
                            BETWEEN 1 AND 500
                        AND proposed_by
                            ~ '^operator:[A-Za-z0-9._-]{1,80}$'
                        AND reviewed_by
                            ~ '^operator:[A-Za-z0-9._-]{1,80}$'
                        AND legal_reviewed_at IS NOT NULL
                        AND transport_verified_at IS NOT NULL
                    )
                )
                AND (
                    (
                        status = 'REVOKED'
                        AND revoked_at IS NOT NULL
                        AND revoked_by
                            ~ '^operator:[A-Za-z0-9._-]{1,80}$'
                        AND LENGTH(BTRIM(revocation_reason))
                            BETWEEN 1 AND 500
                    )
                    OR (
                        status != 'REVOKED'
                        AND revoked_at IS NULL
                        AND revoked_by IS NULL
                        AND revocation_reason IS NULL
                    )
                )
        );

    ALTER TABLE market_data_provider_validations
        DROP CONSTRAINT IF EXISTS ck_provider_validation_governance_v31;
    ALTER TABLE market_data_provider_validations
        ADD CONSTRAINT ck_provider_validation_governance_v31
        CHECK (
                governance_evidence_status IN (
                    'LEGACY_UNVERIFIED',
                    'PENDING',
                    'VERIFIED'
                )
                AND (
                    reference_checksum_sha256 IS NULL
                    OR reference_checksum_sha256 ~ '^[0-9a-f]{64}$'
                )
                AND (
                    acceptance_checksum_sha256 IS NULL
                    OR acceptance_checksum_sha256 ~ '^[0-9a-f]{64}$'
                )
                AND (
                    acceptance_session_count IS NULL
                    OR acceptance_session_count BETWEEN 0 AND 10000
                )
                AND (
                    first_session_date IS NULL
                    OR last_session_date IS NULL
                    OR last_session_date >= first_session_date
                )
                AND (
                    governance_evidence_status != 'VERIFIED'
                    OR (
                        status = 'PASSED'
                        AND acceptance_session_count >= 5
                        AND first_session_date IS NOT NULL
                        AND last_session_date IS NOT NULL
                        AND acceptance_checksum_sha256 IS NOT NULL
                        AND product_covered_instruments
                            = expected_instruments
                        AND symbol_mapped_instruments
                            = expected_instruments
                        AND sample_file_count > 0
                        AND sample_quote_count > 0
                        AND max_observed_delivery_seconds IS NOT NULL
                    )
                )
        );
END;
$$;

CREATE INDEX IF NOT EXISTS idx_provider_validation_reference_snapshot
    ON market_data_provider_validations(
        reference_snapshot_id,
        validated_at DESC
    )
    WHERE reference_snapshot_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_provider_validation_acceptance_checksum
    ON market_data_provider_validations(
        contract_id,
        acceptance_checksum_sha256
    )
    WHERE acceptance_checksum_sha256 IS NOT NULL;

CREATE OR REPLACE FUNCTION enforce_provider_contract_insert_v31()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF (
        NEW.status != 'DRAFT'
        OR NEW.governance_evidence_status != 'PENDING'
        OR NEW.terms_checksum_sha256 IS NOT NULL
        OR NEW.raw_storage_allowed
        OR NEW.derived_storage_allowed
        OR NEW.retention_policy IS NOT NULL
        OR NEW.proposed_by
            !~ '^operator:[A-Za-z0-9._-]{1,80}$'
        OR NEW.reviewed_by IS NOT NULL
        OR NEW.legal_reviewed_at IS NOT NULL
        OR NEW.transport_verified_at IS NOT NULL
        OR NEW.revoked_at IS NOT NULL
        OR NEW.revoked_by IS NOT NULL
        OR NEW.revocation_reason IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            'provider contracts must be inserted as pending drafts';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_provider_contract_insert_v31
    ON market_data_provider_contracts;
CREATE TRIGGER trg_provider_contract_insert_v31
BEFORE INSERT ON market_data_provider_contracts
FOR EACH ROW
EXECUTE FUNCTION enforce_provider_contract_insert_v31();

CREATE OR REPLACE FUNCTION protect_referenced_provider_contract()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'provider contract evidence is append-only';
    END IF;

    IF (
        OLD.status = 'DRAFT'
        AND OLD.governance_evidence_status = 'PENDING'
        AND NEW.status = 'VALIDATED'
        AND NEW.governance_evidence_status = 'VERIFIED'
        AND NEW.terms_checksum_sha256 ~ '^[0-9a-f]{64}$'
        AND NEW.raw_storage_allowed
        AND NEW.derived_storage_allowed
        AND LENGTH(BTRIM(NEW.retention_policy)) BETWEEN 1 AND 500
        AND NEW.reviewed_by ~ '^operator:[A-Za-z0-9._-]{1,80}$'
        AND NEW.legal_reviewed_at IS NOT NULL
        AND NEW.transport_verified_at IS NOT NULL
        AND NEW.legal_reviewed_at <= NOW() + INTERVAL '5 minutes'
        AND NEW.transport_verified_at <= NOW() + INTERVAL '5 minutes'
        AND NEW.revoked_at IS NULL
        AND NEW.revoked_by IS NULL
        AND NEW.revocation_reason IS NULL
        AND (
            to_jsonb(NEW)
            - 'status'
            - 'governance_evidence_status'
            - 'terms_checksum_sha256'
            - 'raw_storage_allowed'
            - 'derived_storage_allowed'
            - 'retention_policy'
            - 'legal_reviewed_at'
            - 'transport_verified_at'
            - 'reviewed_by'
            - 'updated_at'
        ) = (
            to_jsonb(OLD)
            - 'status'
            - 'governance_evidence_status'
            - 'terms_checksum_sha256'
            - 'raw_storage_allowed'
            - 'derived_storage_allowed'
            - 'retention_policy'
            - 'legal_reviewed_at'
            - 'transport_verified_at'
            - 'reviewed_by'
            - 'updated_at'
        )
    ) THEN
        NEW.updated_at := NOW();
        RETURN NEW;
    END IF;

    IF (
        OLD.status = 'VALIDATED'
        AND OLD.governance_evidence_status = 'VERIFIED'
        AND NEW.status = 'REVOKED'
        AND NEW.governance_evidence_status = 'VERIFIED'
        AND NEW.revoked_at IS NOT NULL
        AND NEW.revoked_at <= NOW() + INTERVAL '5 minutes'
        AND NEW.revoked_at >= OLD.updated_at
        AND NEW.revoked_by ~ '^operator:[A-Za-z0-9._-]{1,80}$'
        AND LENGTH(BTRIM(NEW.revocation_reason)) BETWEEN 1 AND 500
        AND (
            to_jsonb(NEW)
            - 'status'
            - 'revoked_at'
            - 'revoked_by'
            - 'revocation_reason'
            - 'updated_at'
        ) = (
            to_jsonb(OLD)
            - 'status'
            - 'revoked_at'
            - 'revoked_by'
            - 'revocation_reason'
            - 'updated_at'
        )
    ) THEN
        NEW.updated_at := NOW();
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'provider contract evidence is immutable and append-only';
END;
$$;

CREATE OR REPLACE FUNCTION protect_referenced_provider_validation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'provider validation evidence is append-only';
END;
$$;

CREATE OR REPLACE FUNCTION enforce_provider_validation_insert_v31()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    contract market_data_provider_contracts%ROWTYPE;
    snapshot reference_data_snapshots%ROWTYPE;
    membership_count INTEGER;
    verified_count INTEGER;
BEGIN
    SELECT *
    INTO contract
    FROM market_data_provider_contracts
    WHERE id = NEW.contract_id
    FOR SHARE;

    IF contract.data_type IN (
        'delayed-post-trade-equity',
        'delayed-pre-trade-equity',
        'realtime-equity-level-1'
    ) THEN
        SELECT *
        INTO snapshot
        FROM reference_data_snapshots
        WHERE id = NEW.reference_snapshot_id
        FOR SHARE;

        SELECT
            COUNT(*)::INTEGER,
            COUNT(*) FILTER (
                WHERE frozen_evidence_status = 'VERIFIED'
            )::INTEGER
        INTO membership_count, verified_count
        FROM reference_snapshot_instruments
        WHERE snapshot_id = NEW.reference_snapshot_id;
    END IF;

    IF (
        contract.id IS NULL
        OR contract.status != 'VALIDATED'
        OR contract.governance_evidence_status != 'VERIFIED'
        OR NEW.status != 'PASSED'
        OR NEW.governance_evidence_status != 'VERIFIED'
        OR NEW.product_covered_instruments != NEW.expected_instruments
        OR NEW.symbol_mapped_instruments != NEW.expected_instruments
        OR NEW.acceptance_session_count < 5
        OR NEW.first_session_date IS NULL
        OR NEW.last_session_date IS NULL
        OR NEW.last_session_date < NEW.first_session_date
        OR NEW.last_session_date
            > (NEW.validated_at AT TIME ZONE 'Europe/Stockholm')::DATE
        OR NEW.valid_until::DATE > contract.valid_until
        OR NEW.max_observed_delivery_seconds
            < contract.nominal_delay_seconds
        OR NEW.max_observed_delivery_seconds > (
            contract.nominal_delay_seconds
            + contract.max_transport_lag_seconds
        )
        OR (
            contract.data_type IN (
                'delayed-post-trade-equity',
                'delayed-pre-trade-equity',
                'realtime-equity-level-1'
            )
            AND (
                snapshot.id IS NULL
                OR snapshot.mic != contract.mic
                OR NEW.reference_checksum_sha256
                    != snapshot.universe_checksum_sha256
                OR NEW.expected_instruments != snapshot.instrument_count
                OR membership_count != snapshot.instrument_count
                OR verified_count != snapshot.instrument_count
            )
        )
        OR (
            contract.data_type = 'delayed-index-level'
            AND (
                NEW.reference_snapshot_id IS NOT NULL
                OR NEW.reference_checksum_sha256 IS NOT NULL
                OR NEW.expected_instruments != 1
            )
        )
        OR contract.data_type NOT IN (
            'delayed-post-trade-equity',
            'delayed-pre-trade-equity',
            'realtime-equity-level-1',
            'delayed-index-level'
        )
    ) THEN
        RAISE EXCEPTION
            'provider validation lacks verified acceptance evidence';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_provider_validation_insert_v31
    ON market_data_provider_validations;
CREATE TRIGGER trg_provider_validation_insert_v31
BEFORE INSERT ON market_data_provider_validations
FOR EACH ROW
EXECUTE FUNCTION enforce_provider_validation_insert_v31();

CREATE OR REPLACE FUNCTION enforce_pre_trade_provider_governance_v31()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    contract market_data_provider_contracts%ROWTYPE;
    validation market_data_provider_validations%ROWTYPE;
    snapshot reference_data_snapshots%ROWTYPE;
BEGIN
    SELECT * INTO contract
    FROM market_data_provider_contracts
    WHERE id = NEW.provider_contract_id
    FOR SHARE;

    SELECT * INTO validation
    FROM market_data_provider_validations
    WHERE id = NEW.provider_validation_id
    FOR SHARE;

    SELECT * INTO snapshot
    FROM reference_data_snapshots
    WHERE id = NEW.reference_snapshot_id
    FOR SHARE;

    IF (
        contract.id IS NULL
        OR contract.status != 'VALIDATED'
        OR contract.governance_evidence_status != 'VERIFIED'
        OR contract.terms_checksum_sha256 IS NULL
        OR NOT contract.raw_storage_allowed
        OR NOT contract.derived_storage_allowed
        OR contract.retention_policy IS NULL
        OR contract.reviewed_by IS NULL
        OR contract.usage_scope != 'INTERNAL_ANALYSIS_AND_PAPER'
        OR contract.external_distribution
        OR validation.id IS NULL
        OR validation.contract_id != contract.id
        OR validation.status != 'PASSED'
        OR validation.governance_evidence_status != 'VERIFIED'
        OR validation.reference_snapshot_id != NEW.reference_snapshot_id
        OR validation.reference_checksum_sha256
            != snapshot.universe_checksum_sha256
        OR validation.acceptance_session_count < 5
        OR validation.acceptance_checksum_sha256 IS NULL
        OR validation.first_session_date IS NULL
        OR validation.last_session_date IS NULL
        OR validation.last_session_date
            > (NEW.received_at AT TIME ZONE 'Europe/Stockholm')::DATE
    ) THEN
        RAISE EXCEPTION
            'pre-trade provider governance is not verified';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_pre_trade_provider_governance_v31
    ON pre_trade_batches;
CREATE TRIGGER trg_pre_trade_provider_governance_v31
BEFORE INSERT ON pre_trade_batches
FOR EACH ROW
EXECUTE FUNCTION enforce_pre_trade_provider_governance_v31();
