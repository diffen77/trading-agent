-- Allow an append-only, checksum-bound stream reseed after an operational gap.
-- Normal minute continuity remains mandatory and a reset can be consumed once.

CREATE TABLE IF NOT EXISTS pre_trade_stream_resets (
    id BIGSERIAL PRIMARY KEY,
    provider_contract_id BIGINT NOT NULL
        REFERENCES market_data_provider_contracts(id) ON DELETE RESTRICT,
    previous_batch_id BIGINT NOT NULL
        REFERENCES pre_trade_batches(id) ON DELETE RESTRICT,
    reference_snapshot_id BIGINT NOT NULL
        REFERENCES reference_data_snapshots(id) ON DELETE RESTRICT,
    source VARCHAR(100) NOT NULL,
    mic CHAR(4) NOT NULL,
    next_report_minute TIMESTAMPTZ NOT NULL,
    next_file_name VARCHAR(255) NOT NULL,
    next_source_checksum_sha256 CHAR(64) NOT NULL,
    next_reference_checksum_sha256 CHAR(64) NOT NULL,
    reason VARCHAR(40) NOT NULL,
    authorized_at TIMESTAMPTZ NOT NULL,
    authorized_by VARCHAR(100) NOT NULL,
    evidence_checksum_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_pre_trade_stream_reset_minute UNIQUE (
        provider_contract_id,
        next_report_minute
    ),
    CONSTRAINT ck_pre_trade_stream_reset_identity CHECK (
        LENGTH(BTRIM(source)) BETWEEN 1 AND 100
        AND mic ~ '^[A-Z0-9]{4}$'
        AND next_file_name
            ~ '^NordicEquity-pretrade-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{4}[.]csv$'
        AND next_source_checksum_sha256 ~ '^[0-9a-f]{64}$'
        AND next_reference_checksum_sha256 ~ '^[0-9a-f]{64}$'
        AND evidence_checksum_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_pre_trade_stream_reset_authority CHECK (
        reason = 'STALE_CURSOR_RECOVERY'
        AND authorized_by ~ '^operator:[A-Za-z0-9._-]{1,80}$'
        AND next_report_minute < authorized_at
    )
);

ALTER TABLE pre_trade_batches
    ADD COLUMN IF NOT EXISTS stream_reset_id BIGINT
        REFERENCES pre_trade_stream_resets(id) ON DELETE RESTRICT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_pre_trade_batch_stream_reset
    ON pre_trade_batches(stream_reset_id)
    WHERE stream_reset_id IS NOT NULL;

CREATE OR REPLACE FUNCTION validate_pre_trade_stream_reset_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    contract market_data_provider_contracts%ROWTYPE;
    previous_batch pre_trade_batches%ROWTYPE;
    latest_batch pre_trade_batches%ROWTYPE;
    reference_snapshot reference_data_snapshots%ROWTYPE;
    observed_delivery_seconds NUMERIC;
BEGIN
    SELECT *
    INTO contract
    FROM market_data_provider_contracts
    WHERE id = NEW.provider_contract_id
    FOR SHARE;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            'pre-trade-contract:' || NEW.provider_contract_id::TEXT,
            0
        )
    );

    SELECT *
    INTO previous_batch
    FROM pre_trade_batches
    WHERE id = NEW.previous_batch_id
    FOR SHARE;

    SELECT *
    INTO latest_batch
    FROM pre_trade_batches candidate
    WHERE candidate.provider_contract_id = NEW.provider_contract_id
    ORDER BY candidate.report_minute DESC, candidate.id DESC
    LIMIT 1;

    SELECT *
    INTO reference_snapshot
    FROM reference_data_snapshots
    WHERE id = NEW.reference_snapshot_id
    FOR SHARE;

    observed_delivery_seconds := EXTRACT(
        EPOCH FROM NEW.authorized_at - NEW.next_report_minute
    );
    IF (
        contract.id IS NULL
        OR previous_batch.id IS NULL
        OR latest_batch.id IS NULL
        OR reference_snapshot.id IS NULL
        OR previous_batch.id != latest_batch.id
        OR previous_batch.provider_contract_id != contract.id
        OR NEW.next_report_minute <= previous_batch.covered_through
        OR NEW.source != previous_batch.source
        OR NEW.mic != previous_batch.mic
        OR NEW.mic != contract.mic
        OR NEW.mic != reference_snapshot.mic
        OR NEW.authorized_at < previous_batch.received_at
        OR contract.status != 'VALIDATED'
        OR observed_delivery_seconds < contract.nominal_delay_seconds
        OR observed_delivery_seconds > (
            contract.nominal_delay_seconds
            + contract.max_transport_lag_seconds
        )
    ) THEN
        RAISE EXCEPTION
            'pre-trade stream reset evidence is not runtime-valid';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_pre_trade_stream_reset_insert
    ON pre_trade_stream_resets;
CREATE TRIGGER trg_pre_trade_stream_reset_insert
BEFORE INSERT ON pre_trade_stream_resets
FOR EACH ROW EXECUTE FUNCTION validate_pre_trade_stream_reset_insert();

DROP TRIGGER IF EXISTS pre_trade_stream_resets_append_only
    ON pre_trade_stream_resets;
CREATE TRIGGER pre_trade_stream_resets_append_only
BEFORE UPDATE OR DELETE ON pre_trade_stream_resets
FOR EACH ROW
EXECUTE FUNCTION reject_operational_evidence_mutation();

CREATE OR REPLACE FUNCTION validate_pre_trade_batch_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    contract market_data_provider_contracts%ROWTYPE;
    validation market_data_provider_validations%ROWTYPE;
    reference_snapshot reference_data_snapshots%ROWTYPE;
    previous_batch pre_trade_batches%ROWTYPE;
    stream_reset pre_trade_stream_resets%ROWTYPE;
    frozen_count INTEGER;
    observed_delivery_seconds NUMERIC;
    has_stream_gap BOOLEAN;
BEGIN
    SELECT *
    INTO contract
    FROM market_data_provider_contracts
    WHERE id = NEW.provider_contract_id
    FOR SHARE;

    SELECT *
    INTO reference_snapshot
    FROM reference_data_snapshots
    WHERE id = NEW.reference_snapshot_id
    FOR SHARE;

    IF contract.id IS NULL OR reference_snapshot.id IS NULL THEN
        RAISE EXCEPTION
            'pre-trade batch requires provider and reference evidence';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            'pre-trade-contract:' || contract.id::TEXT,
            0
        )
    );

    SELECT *
    INTO previous_batch
    FROM pre_trade_batches candidate
    WHERE candidate.provider_contract_id = contract.id
    ORDER BY candidate.report_minute DESC, candidate.id DESC
    LIMIT 1;

    has_stream_gap := (
        previous_batch.id IS NOT NULL
        AND NEW.report_minute != previous_batch.covered_through
    );

    IF NEW.stream_reset_id IS NOT NULL THEN
        SELECT *
        INTO stream_reset
        FROM pre_trade_stream_resets
        WHERE id = NEW.stream_reset_id
        FOR SHARE;
    END IF;

    IF (
        previous_batch.id IS NOT NULL
        AND (
            NEW.source != previous_batch.source
            OR NEW.mic != previous_batch.mic
            OR NEW.received_at < previous_batch.received_at
        )
    ) THEN
        RAISE EXCEPTION
            'pre-trade batch breaks persisted stream continuity';
    END IF;

    IF has_stream_gap AND NEW.stream_reset_id IS NULL THEN
        RAISE EXCEPTION
            'pre-trade batch breaks persisted stream continuity';
    END IF;

    IF (
        (previous_batch.id IS NULL AND NEW.stream_reset_id IS NOT NULL)
        OR (
            previous_batch.id IS NOT NULL
            AND NOT has_stream_gap
            AND NEW.stream_reset_id IS NOT NULL
        )
        OR (
            has_stream_gap
            AND (
                stream_reset.id IS NULL
                OR stream_reset.provider_contract_id != contract.id
                OR stream_reset.previous_batch_id != previous_batch.id
                OR stream_reset.reference_snapshot_id
                    != NEW.reference_snapshot_id
                OR stream_reset.source != NEW.source
                OR stream_reset.mic != NEW.mic
                OR stream_reset.next_report_minute != NEW.report_minute
                OR stream_reset.next_file_name != NEW.file_name
                OR stream_reset.next_source_checksum_sha256
                    != NEW.source_checksum_sha256
                OR stream_reset.next_reference_checksum_sha256
                    != NEW.reference_checksum_sha256
                OR stream_reset.authorized_at != NEW.received_at
            )
        )
    ) THEN
        RAISE EXCEPTION
            'pre-trade batch stream reset evidence is missing or mismatched';
    END IF;

    SELECT candidate.*
    INTO validation
    FROM market_data_provider_validations candidate
    WHERE candidate.contract_id = contract.id
    ORDER BY candidate.validated_at DESC, candidate.id DESC
    LIMIT 1;

    SELECT COUNT(*)::INTEGER
    INTO frozen_count
    FROM reference_snapshot_instruments membership
    WHERE
        membership.snapshot_id = reference_snapshot.id
        AND membership.frozen_evidence_status = 'VERIFIED';

    IF (
        contract.status != 'VALIDATED'
        OR contract.legal_reviewed_at IS NULL
        OR contract.transport_verified_at IS NULL
        OR contract.legal_reviewed_at > NEW.received_at
        OR contract.transport_verified_at > NEW.received_at
        OR NEW.received_at::DATE NOT BETWEEN
            contract.valid_from AND contract.valid_until
    ) THEN
        RAISE EXCEPTION
            'pre-trade provider contract is not runtime-valid';
    END IF;

    IF (
        validation.id IS NULL
        OR validation.id != NEW.provider_validation_id
        OR validation.status != 'PASSED'
        OR validation.validated_at > NEW.received_at
        OR validation.valid_until < NEW.received_at
        OR validation.expected_instruments
            != reference_snapshot.instrument_count
        OR validation.product_covered_instruments
            != reference_snapshot.instrument_count
        OR validation.symbol_mapped_instruments
            != reference_snapshot.instrument_count
        OR validation.sample_file_count < 1
        OR validation.sample_quote_count < 1
        OR validation.max_observed_delivery_seconds IS NULL
        OR validation.max_observed_delivery_seconds > (
            contract.nominal_delay_seconds
            + contract.max_transport_lag_seconds
        )
    ) THEN
        RAISE EXCEPTION
            'pre-trade provider validation is not runtime-valid';
    END IF;

    IF (
        NEW.provider != contract.provider
        OR NEW.data_type != contract.data_type
        OR NEW.mic != contract.mic
        OR NEW.source NOT LIKE contract.provider || '%'
        OR contract.data_type NOT IN (
            'delayed-pre-trade-equity',
            'realtime-equity-level-1'
        )
        OR contract.usage_scope NOT IN (
            'INTERNAL_ANALYSIS_AND_PAPER',
            'AUTOMATED_LIVE_TRADING'
        )
    ) THEN
        RAISE EXCEPTION
            'pre-trade batch does not match provider contract';
    END IF;

    IF (
        reference_snapshot.mic != NEW.mic
        OR reference_snapshot.received_at > NEW.received_at
        OR frozen_count != reference_snapshot.instrument_count
        OR NEW.state_count > reference_snapshot.instrument_count
    ) THEN
        RAISE EXCEPTION
            'pre-trade batch reference membership is incomplete or mismatched';
    END IF;

    observed_delivery_seconds := EXTRACT(
        EPOCH FROM NEW.received_at - NEW.report_minute
    );
    IF (
        observed_delivery_seconds < contract.nominal_delay_seconds
        OR observed_delivery_seconds > (
            contract.nominal_delay_seconds
            + contract.max_transport_lag_seconds
        )
    ) THEN
        RAISE EXCEPTION
            'pre-trade batch delivery is outside provider bounds';
    END IF;

    RETURN NEW;
END;
$$;
