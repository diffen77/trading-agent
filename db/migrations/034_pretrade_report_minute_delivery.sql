-- A delayed Nasdaq report is timestamped by its report minute. Delivery age
-- must therefore be measured from report_minute, not covered_through
-- (report_minute + one minute), which undercounted the delay by 60 seconds.

CREATE OR REPLACE FUNCTION validate_pre_trade_batch_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    contract market_data_provider_contracts%ROWTYPE;
    validation market_data_provider_validations%ROWTYPE;
    reference_snapshot reference_data_snapshots%ROWTYPE;
    previous_batch pre_trade_batches%ROWTYPE;
    frozen_count INTEGER;
    observed_delivery_seconds NUMERIC;
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

    IF (
        previous_batch.id IS NOT NULL
        AND (
            NEW.report_minute != previous_batch.covered_through
            OR NEW.source != previous_batch.source
            OR NEW.mic != previous_batch.mic
            OR NEW.received_at < previous_batch.received_at
        )
    ) THEN
        RAISE EXCEPTION
            'pre-trade batch breaks persisted stream continuity';
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
