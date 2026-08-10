-- Public Nasdaq delayed pre-trade data is governed by published terms rather
-- than a negotiated customer contract. Raw report bodies remain ephemeral.

ALTER TABLE market_data_provider_contracts
    ADD COLUMN IF NOT EXISTS authorization_basis VARCHAR(40)
        NOT NULL DEFAULT 'NEGOTIATED_CONTRACT',
    ADD COLUMN IF NOT EXISTS policy_verified_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS policy_verified_by VARCHAR(100);

ALTER TABLE market_data_provider_validations
    ADD COLUMN IF NOT EXISTS validation_basis VARCHAR(40)
        NOT NULL DEFAULT 'FIVE_SESSION_ACCEPTANCE';

DO $$
BEGIN
    ALTER TABLE market_data_provider_contracts
        DROP CONSTRAINT IF EXISTS ck_provider_contract_validated;
    ALTER TABLE market_data_provider_contracts
        ADD CONSTRAINT ck_provider_contract_validated
        CHECK (
            status != 'VALIDATED'
            OR (
                transport_verified_at IS NOT NULL
                AND (
                    (
                        authorization_basis = 'NEGOTIATED_CONTRACT'
                        AND legal_reviewed_at IS NOT NULL
                    )
                    OR (
                        authorization_basis =
                            'PUBLIC_NONCOMMERCIAL_TERMS'
                        AND policy_verified_at IS NOT NULL
                        AND policy_verified_by
                            ~ '^operator:[A-Za-z0-9._-]{1,80}$'
                    )
                )
            )
        );

    ALTER TABLE market_data_provider_contracts
        DROP CONSTRAINT IF EXISTS ck_provider_contract_governance_v31;
    ALTER TABLE market_data_provider_contracts
        ADD CONSTRAINT ck_provider_contract_governance_v31
        CHECK (
            authorization_basis IN (
                'NEGOTIATED_CONTRACT',
                'PUBLIC_NONCOMMERCIAL_TERMS'
            )
            AND governance_evidence_status IN (
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
                    authorization_basis = 'NEGOTIATED_CONTRACT'
                    AND status = 'DRAFT'
                    AND terms_checksum_sha256 IS NULL
                    AND raw_storage_allowed = FALSE
                    AND derived_storage_allowed = FALSE
                    AND retention_policy IS NULL
                    AND proposed_by
                        ~ '^operator:[A-Za-z0-9._-]{1,80}$'
                    AND reviewed_by IS NULL
                    AND legal_reviewed_at IS NULL
                    AND transport_verified_at IS NULL
                    AND policy_verified_at IS NULL
                    AND policy_verified_by IS NULL
                )
            )
            AND (
                governance_evidence_status != 'VERIFIED'
                OR (
                    status IN ('VALIDATED', 'REVOKED')
                    AND terms_checksum_sha256 IS NOT NULL
                    AND derived_storage_allowed = TRUE
                    AND LENGTH(BTRIM(retention_policy))
                        BETWEEN 1 AND 500
                    AND proposed_by
                        ~ '^operator:[A-Za-z0-9._-]{1,80}$'
                    AND reviewed_by
                        ~ '^operator:[A-Za-z0-9._-]{1,80}$'
                    AND transport_verified_at IS NOT NULL
                    AND (
                        (
                            authorization_basis =
                                'NEGOTIATED_CONTRACT'
                            AND raw_storage_allowed = TRUE
                            AND legal_reviewed_at IS NOT NULL
                            AND policy_verified_at IS NULL
                            AND policy_verified_by IS NULL
                        )
                        OR (
                            authorization_basis =
                                'PUBLIC_NONCOMMERCIAL_TERMS'
                            AND raw_storage_allowed = FALSE
                            AND policy_verified_at IS NOT NULL
                            AND policy_verified_by
                                ~ '^operator:[A-Za-z0-9._-]{1,80}$'
                        )
                    )
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
            validation_basis IN (
                'FIVE_SESSION_ACCEPTANCE',
                'CONTINUOUS_FILE_VALIDATION'
            )
            AND governance_evidence_status IN (
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
                    AND (
                        (
                            validation_basis =
                                'FIVE_SESSION_ACCEPTANCE'
                            AND acceptance_session_count >= 5
                        )
                        OR (
                            validation_basis =
                                'CONTINUOUS_FILE_VALIDATION'
                            AND acceptance_session_count >= 1
                        )
                    )
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

CREATE OR REPLACE FUNCTION enforce_provider_contract_insert_v33()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.authorization_basis = 'NEGOTIATED_CONTRACT' THEN
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
            OR NEW.policy_verified_at IS NOT NULL
            OR NEW.policy_verified_by IS NOT NULL
            OR NEW.revoked_at IS NOT NULL
            OR NEW.revoked_by IS NOT NULL
            OR NEW.revocation_reason IS NOT NULL
        ) THEN
            RAISE EXCEPTION
                'negotiated provider contracts must start as pending drafts';
        END IF;
        RETURN NEW;
    END IF;

    IF (
        NEW.authorization_basis != 'PUBLIC_NONCOMMERCIAL_TERMS'
        OR NEW.provider != 'nasdaq-nordic'
        OR NEW.data_type != 'delayed-pre-trade-equity'
        OR NEW.mic != 'XSTO'
        OR NEW.delivery_mode != 'DELAYED_15M'
        OR NEW.transport != 'PUBLIC_CSV'
        OR NEW.usage_scope != 'INTERNAL_ANALYSIS_AND_PAPER'
        OR NEW.external_distribution
        OR NEW.reference_symbols_included
        OR NEW.status != 'VALIDATED'
        OR NEW.governance_evidence_status != 'VERIFIED'
        OR NEW.terms_checksum_sha256
            !~ '^[0-9a-f]{64}$'
        OR NEW.raw_storage_allowed
        OR NOT NEW.derived_storage_allowed
        OR LENGTH(BTRIM(NEW.retention_policy)) NOT BETWEEN 1 AND 500
        OR NEW.proposed_by
            !~ '^operator:[A-Za-z0-9._-]{1,80}$'
        OR NEW.reviewed_by
            !~ '^operator:[A-Za-z0-9._-]{1,80}$'
        OR NEW.policy_verified_at IS NULL
        OR NEW.policy_verified_by
            !~ '^operator:[A-Za-z0-9._-]{1,80}$'
        OR NEW.transport_verified_at IS NULL
        OR NEW.policy_verified_at > NOW() + INTERVAL '5 minutes'
        OR NEW.transport_verified_at > NOW() + INTERVAL '5 minutes'
        OR NEW.revoked_at IS NOT NULL
        OR NEW.revoked_by IS NOT NULL
        OR NEW.revocation_reason IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            'public delayed provider authorization is incomplete';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_provider_contract_insert_v31
    ON market_data_provider_contracts;
DROP TRIGGER IF EXISTS trg_provider_contract_insert_v33
    ON market_data_provider_contracts;
CREATE TRIGGER trg_provider_contract_insert_v33
BEFORE INSERT ON market_data_provider_contracts
FOR EACH ROW
EXECUTE FUNCTION enforce_provider_contract_insert_v33();

CREATE OR REPLACE FUNCTION enforce_provider_validation_insert_v33()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    contract market_data_provider_contracts%ROWTYPE;
    snapshot reference_data_snapshots%ROWTYPE;
    membership_count INTEGER;
    verified_count INTEGER;
    minimum_sessions INTEGER;
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

    minimum_sessions := CASE
        WHEN (
            contract.authorization_basis =
                'PUBLIC_NONCOMMERCIAL_TERMS'
            AND NEW.validation_basis =
                'CONTINUOUS_FILE_VALIDATION'
        ) THEN 1
        ELSE 5
    END;

    IF (
        contract.id IS NULL
        OR contract.status != 'VALIDATED'
        OR contract.governance_evidence_status != 'VERIFIED'
        OR NEW.status != 'PASSED'
        OR NEW.governance_evidence_status != 'VERIFIED'
        OR (
            contract.authorization_basis =
                'PUBLIC_NONCOMMERCIAL_TERMS'
            AND NEW.validation_basis !=
                'CONTINUOUS_FILE_VALIDATION'
        )
        OR (
            contract.authorization_basis = 'NEGOTIATED_CONTRACT'
            AND NEW.validation_basis !=
                'FIVE_SESSION_ACCEPTANCE'
        )
        OR NEW.product_covered_instruments != NEW.expected_instruments
        OR NEW.symbol_mapped_instruments != NEW.expected_instruments
        OR NEW.acceptance_session_count < minimum_sessions
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
DROP TRIGGER IF EXISTS trg_provider_validation_insert_v33
    ON market_data_provider_validations;
CREATE TRIGGER trg_provider_validation_insert_v33
BEFORE INSERT ON market_data_provider_validations
FOR EACH ROW
EXECUTE FUNCTION enforce_provider_validation_insert_v33();

CREATE OR REPLACE FUNCTION enforce_pre_trade_provider_governance_v33()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    contract market_data_provider_contracts%ROWTYPE;
    validation market_data_provider_validations%ROWTYPE;
    snapshot reference_data_snapshots%ROWTYPE;
    minimum_sessions INTEGER;
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

    minimum_sessions := CASE
        WHEN (
            contract.authorization_basis =
                'PUBLIC_NONCOMMERCIAL_TERMS'
            AND validation.validation_basis =
                'CONTINUOUS_FILE_VALIDATION'
        ) THEN 1
        ELSE 5
    END;

    IF (
        contract.id IS NULL
        OR contract.status != 'VALIDATED'
        OR contract.governance_evidence_status != 'VERIFIED'
        OR contract.terms_checksum_sha256 IS NULL
        OR NOT contract.derived_storage_allowed
        OR (
            contract.authorization_basis = 'NEGOTIATED_CONTRACT'
            AND NOT contract.raw_storage_allowed
        )
        OR (
            contract.authorization_basis =
                'PUBLIC_NONCOMMERCIAL_TERMS'
            AND (
                contract.raw_storage_allowed
                OR contract.policy_verified_at IS NULL
                OR contract.policy_verified_by IS NULL
            )
        )
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
        OR validation.acceptance_session_count < minimum_sessions
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
DROP TRIGGER IF EXISTS trg_pre_trade_provider_governance_v33
    ON pre_trade_batches;
CREATE TRIGGER trg_pre_trade_provider_governance_v33
BEFORE INSERT ON pre_trade_batches
FOR EACH ROW
EXECUTE FUNCTION enforce_pre_trade_provider_governance_v33();
