-- Append-only Level 1 file batches, ordered side updates, restart cursor,
-- and materialized best bid/offer state.

DROP TRIGGER IF EXISTS trg_reference_snapshot_instrument_immutable
    ON reference_snapshot_instruments;

ALTER TABLE reference_snapshot_instruments
    ADD COLUMN IF NOT EXISTS symbol VARCHAR(32),
    ADD COLUMN IF NOT EXISTS name VARCHAR(255),
    ADD COLUMN IF NOT EXISTS mic CHAR(4),
    ADD COLUMN IF NOT EXISTS currency CHAR(3),
    ADD COLUMN IF NOT EXISTS instrument_type VARCHAR(40),
    ADD COLUMN IF NOT EXISTS primary_listing BOOLEAN,
    ADD COLUMN IF NOT EXISTS status VARCHAR(20),
    ADD COLUMN IF NOT EXISTS first_trade_date DATE,
    ADD COLUMN IF NOT EXISTS last_trade_date DATE,
    ADD COLUMN IF NOT EXISTS source VARCHAR(100),
    ADD COLUMN IF NOT EXISTS cfi_code CHAR(6),
    ADD COLUMN IF NOT EXISTS frozen_evidence_status VARCHAR(30)
        NOT NULL DEFAULT 'LEGACY_UNVERIFIED';

UPDATE reference_snapshot_instruments membership
SET
    symbol = instrument.symbol,
    name = instrument.name,
    mic = snapshot.mic,
    currency = instrument.currency,
    instrument_type = instrument.instrument_type,
    primary_listing = TRUE,
    status = 'ACTIVE',
    first_trade_date = instrument.first_trade_date,
    last_trade_date = instrument.last_trade_date,
    source = snapshot.provider,
    cfi_code = instrument.cfi_code
FROM
    instruments instrument,
    reference_data_snapshots snapshot
WHERE
    instrument.id = membership.instrument_id
    AND snapshot.id = membership.snapshot_id
    AND (
        membership.name IS NULL
        OR membership.mic IS NULL
        OR membership.currency IS NULL
        OR membership.instrument_type IS NULL
        OR membership.primary_listing IS NULL
        OR membership.status IS NULL
        OR membership.source IS NULL
    );

ALTER TABLE reference_snapshot_instruments
    ALTER COLUMN name SET NOT NULL,
    ALTER COLUMN mic SET NOT NULL,
    ALTER COLUMN currency SET NOT NULL,
    ALTER COLUMN instrument_type SET NOT NULL,
    ALTER COLUMN primary_listing SET NOT NULL,
    ALTER COLUMN status SET NOT NULL,
    ALTER COLUMN source SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_reference_snapshot_frozen_identity'
    ) THEN
        ALTER TABLE reference_snapshot_instruments
            ADD CONSTRAINT ck_reference_snapshot_frozen_identity CHECK (
                mic ~ '^[A-Z0-9]{4}$'
                AND currency ~ '^[A-Z]{3}$'
                AND status = 'ACTIVE'
                AND primary_listing
                AND instrument_type IN (
                    'COMMON_STOCK',
                    'PREFERRED_STOCK',
                    'DEPOSITARY_RECEIPT'
                )
                AND (
                    cfi_code IS NULL
                    OR cfi_code ~ '^[A-Z0-9]{6}$'
                )
                AND frozen_evidence_status IN (
                    'VERIFIED',
                    'LEGACY_UNVERIFIED'
                )
                AND (
                    last_trade_date IS NULL
                    OR first_trade_date IS NULL
                    OR last_trade_date >= first_trade_date
                )
            );
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION populate_reference_snapshot_frozen_evidence()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    instrument instruments%ROWTYPE;
BEGIN
    SELECT *
    INTO instrument
    FROM instruments
    WHERE id = NEW.instrument_id;

    IF instrument.id IS NULL THEN
        RAISE EXCEPTION
            'snapshot instrument requires existing instrument evidence';
    END IF;

    NEW.symbol := instrument.symbol;
    NEW.name := instrument.name;
    NEW.mic := instrument.mic;
    NEW.currency := instrument.currency;
    NEW.instrument_type := instrument.instrument_type;
    NEW.primary_listing := instrument.primary_listing;
    NEW.status := instrument.status;
    NEW.first_trade_date := instrument.first_trade_date;
    NEW.last_trade_date := instrument.last_trade_date;
    NEW.source := instrument.source;
    NEW.cfi_code := instrument.cfi_code;
    NEW.frozen_evidence_status := 'VERIFIED';
    RETURN NEW;
END;
$$;

CREATE TRIGGER aaa_reference_snapshot_frozen_evidence
BEFORE INSERT ON reference_snapshot_instruments
FOR EACH ROW
EXECUTE FUNCTION populate_reference_snapshot_frozen_evidence();

CREATE TRIGGER trg_reference_snapshot_instrument_immutable
BEFORE UPDATE OR DELETE ON reference_snapshot_instruments
FOR EACH ROW
EXECUTE FUNCTION forbid_reference_snapshot_instrument_change();

CREATE TABLE IF NOT EXISTS pre_trade_batches (
    id BIGSERIAL PRIMARY KEY,
    provider_contract_id BIGINT NOT NULL
        REFERENCES market_data_provider_contracts(id) ON DELETE RESTRICT,
    provider_validation_id BIGINT NOT NULL
        REFERENCES market_data_provider_validations(id) ON DELETE RESTRICT,
    reference_snapshot_id BIGINT NOT NULL
        REFERENCES reference_data_snapshots(id) ON DELETE RESTRICT,
    provider VARCHAR(100) NOT NULL,
    data_type VARCHAR(50) NOT NULL,
    source VARCHAR(100) NOT NULL,
    mic CHAR(4) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    source_url TEXT NOT NULL,
    source_checksum_sha256 CHAR(64) NOT NULL,
    reference_checksum_sha256 CHAR(64) NOT NULL,
    payload_checksum_sha256 CHAR(64) NOT NULL,
    report_minute TIMESTAMPTZ NOT NULL,
    covered_through TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    input_row_count INTEGER NOT NULL,
    update_count INTEGER NOT NULL,
    state_count INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_pre_trade_batch_minute UNIQUE (
        provider_contract_id, report_minute
    ),
    CONSTRAINT uq_pre_trade_batch_file UNIQUE (
        provider_contract_id, file_name
    ),
    CONSTRAINT ck_pre_trade_batch_text CHECK (
        LENGTH(BTRIM(provider)) BETWEEN 1 AND 100
        AND LENGTH(BTRIM(data_type)) BETWEEN 1 AND 50
        AND LENGTH(BTRIM(source)) BETWEEN 1 AND 100
        AND LENGTH(BTRIM(file_name)) BETWEEN 1 AND 255
        AND file_name NOT LIKE '%/%'
        AND file_name NOT LIKE E'%\\\\%'
        AND file_name NOT IN ('.', '..')
    ),
    CONSTRAINT ck_pre_trade_batch_mic CHECK (
        mic ~ '^[A-Z0-9]{4}$'
    ),
    CONSTRAINT ck_pre_trade_batch_url CHECK (
        source_url LIKE 'https://%'
    ),
    CONSTRAINT ck_pre_trade_batch_checksums CHECK (
        source_checksum_sha256 ~ '^[0-9a-f]{64}$'
        AND reference_checksum_sha256 ~ '^[0-9a-f]{64}$'
        AND payload_checksum_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_pre_trade_batch_times CHECK (
        report_minute = DATE_TRUNC('minute', report_minute)
        AND covered_through = report_minute + INTERVAL '1 minute'
        AND received_at >= covered_through
    ),
    CONSTRAINT ck_pre_trade_batch_counts CHECK (
        input_row_count BETWEEN 1 AND 5000000
        AND update_count BETWEEN 0 AND 250000
        AND update_count <= input_row_count
        AND state_count BETWEEN 0 AND 1000000
    )
);

CREATE INDEX IF NOT EXISTS idx_pre_trade_batch_latest
    ON pre_trade_batches(
        provider_contract_id,
        report_minute DESC,
        id DESC
    );

CREATE TABLE IF NOT EXISTS pre_trade_updates (
    id BIGSERIAL PRIMARY KEY,
    batch_id BIGINT NOT NULL
        REFERENCES pre_trade_batches(id) ON DELETE RESTRICT,
    instrument_id BIGINT NOT NULL
        REFERENCES instruments(id) ON DELETE RESTRICT,
    isin CHAR(12) NOT NULL,
    source_sequence INTEGER NOT NULL,
    side VARCHAR(4) NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    publication_time TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    source VARCHAR(100) NOT NULL,
    mic CHAR(4) NOT NULL,
    currency CHAR(3) NOT NULL,
    price NUMERIC(24, 8),
    quantity NUMERIC(28, 8),
    order_count INTEGER,
    trading_system VARCHAR(20) NOT NULL,
    trading_phase VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_pre_trade_update_sequence UNIQUE (
        batch_id, source_sequence
    ),
    CONSTRAINT ck_pre_trade_update_identity CHECK (
        isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'
        AND mic ~ '^[A-Z0-9]{4}$'
        AND currency ~ '^[A-Z]{3}$'
        AND LENGTH(BTRIM(source)) BETWEEN 1 AND 100
    ),
    CONSTRAINT ck_pre_trade_update_side CHECK (
        side IN ('BUY', 'SELL')
    ),
    CONSTRAINT ck_pre_trade_update_values CHECK (
        (
            price IS NULL
            AND quantity IS NULL
            AND order_count IS NULL
        )
        OR (
            price > 0
            AND quantity > 0
            AND order_count > 0
        )
    ),
    CONSTRAINT ck_pre_trade_update_sequence CHECK (
        source_sequence BETWEEN 1 AND 5000000
    ),
    CONSTRAINT ck_pre_trade_update_times CHECK (
        event_time <= publication_time
        AND publication_time <= received_at
    ),
    CONSTRAINT ck_pre_trade_update_market_state CHECK (
        trading_system ~ '^[A-Z0-9_-]{1,20}$'
        AND trading_phase ~ '^[A-Z0-9_-]{1,20}$'
    )
);

CREATE INDEX IF NOT EXISTS idx_pre_trade_update_instrument_time
    ON pre_trade_updates(
        instrument_id,
        event_time DESC,
        id DESC
    );

CREATE TABLE IF NOT EXISTS pre_trade_stream_cursors (
    batch_id BIGINT PRIMARY KEY
        REFERENCES pre_trade_batches(id) ON DELETE RESTRICT,
    source VARCHAR(100) NOT NULL,
    mic CHAR(4) NOT NULL,
    covered_through TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    reference_checksum_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_pre_trade_cursor_identity CHECK (
        LENGTH(BTRIM(source)) BETWEEN 1 AND 100
        AND mic ~ '^[A-Z0-9]{4}$'
        AND reference_checksum_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_pre_trade_cursor_times CHECK (
        covered_through <= received_at
    )
);

CREATE TABLE IF NOT EXISTS pre_trade_book_states (
    id BIGSERIAL PRIMARY KEY,
    batch_id BIGINT NOT NULL
        REFERENCES pre_trade_batches(id) ON DELETE RESTRICT,
    instrument_id BIGINT NOT NULL
        REFERENCES instruments(id) ON DELETE RESTRICT,
    isin CHAR(12) NOT NULL,
    source VARCHAR(100) NOT NULL,
    mic CHAR(4) NOT NULL,
    reference_checksum_sha256 CHAR(64) NOT NULL,
    currency CHAR(3) NOT NULL,
    bid_price NUMERIC(24, 8),
    bid_quantity NUMERIC(28, 8),
    bid_order_count INTEGER,
    bid_event_time TIMESTAMPTZ,
    bid_publication_time TIMESTAMPTZ,
    ask_price NUMERIC(24, 8),
    ask_quantity NUMERIC(28, 8),
    ask_order_count INTEGER,
    ask_event_time TIMESTAMPTZ,
    ask_publication_time TIMESTAMPTZ,
    covered_through TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    trading_system VARCHAR(20) NOT NULL,
    trading_phase VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_pre_trade_book_state UNIQUE (
        batch_id, instrument_id
    ),
    CONSTRAINT ck_pre_trade_state_identity CHECK (
        isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'
        AND mic ~ '^[A-Z0-9]{4}$'
        AND currency ~ '^[A-Z]{3}$'
        AND LENGTH(BTRIM(source)) BETWEEN 1 AND 100
        AND reference_checksum_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_pre_trade_state_bid_values CHECK (
        (
            bid_price IS NULL
            AND bid_quantity IS NULL
            AND bid_order_count IS NULL
        )
        OR (
            bid_price > 0
            AND bid_quantity > 0
            AND bid_order_count > 0
        )
    ),
    CONSTRAINT ck_pre_trade_state_ask_values CHECK (
        (
            ask_price IS NULL
            AND ask_quantity IS NULL
            AND ask_order_count IS NULL
        )
        OR (
            ask_price > 0
            AND ask_quantity > 0
            AND ask_order_count > 0
        )
    ),
    CONSTRAINT ck_pre_trade_state_bid_times CHECK (
        (
            bid_event_time IS NULL
            AND bid_publication_time IS NULL
            AND bid_price IS NULL
        )
        OR (
            bid_event_time IS NOT NULL
            AND bid_publication_time IS NOT NULL
            AND bid_event_time <= bid_publication_time
        )
    ),
    CONSTRAINT ck_pre_trade_state_ask_times CHECK (
        (
            ask_event_time IS NULL
            AND ask_publication_time IS NULL
            AND ask_price IS NULL
        )
        OR (
            ask_event_time IS NOT NULL
            AND ask_publication_time IS NOT NULL
            AND ask_event_time <= ask_publication_time
        )
    ),
    CONSTRAINT ck_pre_trade_state_spread CHECK (
        bid_price IS NULL
        OR ask_price IS NULL
        OR bid_price <= ask_price
    ),
    CONSTRAINT ck_pre_trade_state_times CHECK (
        covered_through <= received_at
        AND (
            bid_publication_time IS NULL
            OR bid_publication_time <= covered_through
        )
        AND (
            ask_publication_time IS NULL
            OR ask_publication_time <= covered_through
        )
    ),
    CONSTRAINT ck_pre_trade_state_market_state CHECK (
        trading_system ~ '^[A-Z0-9_-]{1,20}$'
        AND trading_phase ~ '^[A-Z0-9_-]{1,20}$'
    )
);

CREATE INDEX IF NOT EXISTS idx_pre_trade_state_instrument_latest
    ON pre_trade_book_states(
        instrument_id,
        covered_through DESC,
        id DESC
    );

ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS source_book_state_id BIGINT
        REFERENCES pre_trade_book_states(id) ON DELETE RESTRICT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_trades_single_price_evidence'
    ) THEN
        ALTER TABLE trades
            ADD CONSTRAINT ck_trades_single_price_evidence CHECK (
                source_quote_id IS NULL
                OR source_book_state_id IS NULL
            );
    END IF;
END
$$;

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
        EPOCH FROM NEW.received_at - NEW.covered_through
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

CREATE TRIGGER trg_pre_trade_batch_insert
BEFORE INSERT ON pre_trade_batches
FOR EACH ROW EXECUTE FUNCTION validate_pre_trade_batch_insert();

CREATE OR REPLACE FUNCTION validate_pre_trade_update_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    batch pre_trade_batches%ROWTYPE;
    member_isin CHAR(12);
BEGIN
    SELECT *
    INTO batch
    FROM pre_trade_batches
    WHERE id = NEW.batch_id
    FOR SHARE;

    SELECT membership.isin
    INTO member_isin
    FROM reference_snapshot_instruments membership
    WHERE
        membership.snapshot_id = batch.reference_snapshot_id
        AND membership.instrument_id = NEW.instrument_id;

    IF (
        batch.id IS NULL
        OR member_isin IS NULL
        OR TRIM(member_isin) != TRIM(NEW.isin)
        OR NEW.source != batch.source
        OR NEW.mic != batch.mic
        OR NEW.received_at != batch.received_at
        OR NEW.source_sequence > batch.input_row_count
        OR NEW.event_time < batch.report_minute
        OR NEW.event_time >= batch.covered_through
        OR NEW.publication_time < batch.report_minute
        OR NEW.publication_time >= batch.covered_through
    ) THEN
        RAISE EXCEPTION
            'pre-trade update does not match its batch evidence';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pre_trade_stream_cursors cursor
        WHERE cursor.batch_id = NEW.batch_id
    ) THEN
        RAISE EXCEPTION
            'pre-trade batch is already sealed';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_pre_trade_update_insert
BEFORE INSERT ON pre_trade_updates
FOR EACH ROW EXECUTE FUNCTION validate_pre_trade_update_insert();

CREATE OR REPLACE FUNCTION validate_pre_trade_cursor_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    batch pre_trade_batches%ROWTYPE;
    stored_updates INTEGER;
    stored_states INTEGER;
BEGIN
    SELECT *
    INTO batch
    FROM pre_trade_batches
    WHERE id = NEW.batch_id
    FOR UPDATE;

    IF (
        batch.id IS NULL
        OR NEW.source != batch.source
        OR NEW.mic != batch.mic
        OR NEW.covered_through != batch.covered_through
        OR NEW.received_at != batch.received_at
        OR NEW.reference_checksum_sha256
            != batch.reference_checksum_sha256
    ) THEN
        RAISE EXCEPTION
            'pre-trade cursor does not match its batch evidence';
    END IF;

    SELECT COUNT(*)::INTEGER
    INTO stored_updates
    FROM pre_trade_updates
    WHERE batch_id = NEW.batch_id;

    SELECT COUNT(*)::INTEGER
    INTO stored_states
    FROM pre_trade_book_states
    WHERE batch_id = NEW.batch_id;

    IF (
        stored_updates != batch.update_count
        OR stored_states != batch.state_count
    ) THEN
        RAISE EXCEPTION
            'pre-trade cursor cannot seal incomplete batch evidence';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_pre_trade_cursor_insert
BEFORE INSERT ON pre_trade_stream_cursors
FOR EACH ROW EXECUTE FUNCTION validate_pre_trade_cursor_insert();

CREATE OR REPLACE FUNCTION validate_pre_trade_state_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    batch pre_trade_batches%ROWTYPE;
    member_isin CHAR(12);
BEGIN
    SELECT *
    INTO batch
    FROM pre_trade_batches
    WHERE id = NEW.batch_id
    FOR SHARE;

    SELECT membership.isin
    INTO member_isin
    FROM reference_snapshot_instruments membership
    WHERE
        membership.snapshot_id = batch.reference_snapshot_id
        AND membership.instrument_id = NEW.instrument_id;

    IF (
        batch.id IS NULL
        OR member_isin IS NULL
        OR TRIM(member_isin) != TRIM(NEW.isin)
        OR NEW.source != batch.source
        OR NEW.mic != batch.mic
        OR NEW.reference_checksum_sha256
            != batch.reference_checksum_sha256
        OR NEW.covered_through != batch.covered_through
        OR NEW.received_at != batch.received_at
    ) THEN
        RAISE EXCEPTION
            'pre-trade state does not match its batch evidence';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pre_trade_stream_cursors cursor
        WHERE cursor.batch_id = NEW.batch_id
    ) THEN
        RAISE EXCEPTION
            'pre-trade batch is already sealed';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_pre_trade_state_insert
BEFORE INSERT ON pre_trade_book_states
FOR EACH ROW EXECUTE FUNCTION validate_pre_trade_state_insert();

CREATE OR REPLACE FUNCTION validate_pre_trade_batch_complete()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    target_batch_id BIGINT;
    batch pre_trade_batches%ROWTYPE;
    stored_updates INTEGER;
    stored_cursors INTEGER;
    stored_states INTEGER;
BEGIN
    IF TG_TABLE_NAME = 'pre_trade_batches' THEN
        target_batch_id := (TO_JSONB(NEW)->>'id')::BIGINT;
    ELSE
        target_batch_id := (TO_JSONB(NEW)->>'batch_id')::BIGINT;
    END IF;

    SELECT *
    INTO batch
    FROM pre_trade_batches
    WHERE id = target_batch_id;

    SELECT COUNT(*)::INTEGER
    INTO stored_updates
    FROM pre_trade_updates
    WHERE batch_id = target_batch_id;

    SELECT COUNT(*)::INTEGER
    INTO stored_cursors
    FROM pre_trade_stream_cursors
    WHERE batch_id = target_batch_id;

    SELECT COUNT(*)::INTEGER
    INTO stored_states
    FROM pre_trade_book_states
    WHERE batch_id = target_batch_id;

    IF (
        batch.id IS NULL
        OR stored_updates != batch.update_count
        OR stored_cursors != 1
        OR stored_states != batch.state_count
    ) THEN
        RAISE EXCEPTION
            'pre-trade batch is incomplete or has surplus evidence';
    END IF;

    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER pre_trade_batch_complete
AFTER INSERT ON pre_trade_batches
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION validate_pre_trade_batch_complete();

CREATE TRIGGER pre_trade_batches_append_only
BEFORE UPDATE OR DELETE ON pre_trade_batches
FOR EACH ROW
EXECUTE FUNCTION reject_operational_evidence_mutation();

CREATE TRIGGER pre_trade_updates_append_only
BEFORE UPDATE OR DELETE ON pre_trade_updates
FOR EACH ROW
EXECUTE FUNCTION reject_operational_evidence_mutation();

CREATE TRIGGER pre_trade_stream_cursors_append_only
BEFORE UPDATE OR DELETE ON pre_trade_stream_cursors
FOR EACH ROW
EXECUTE FUNCTION reject_operational_evidence_mutation();

CREATE TRIGGER pre_trade_book_states_append_only
BEFORE UPDATE OR DELETE ON pre_trade_book_states
FOR EACH ROW
EXECUTE FUNCTION reject_operational_evidence_mutation();

CREATE OR REPLACE FUNCTION protect_referenced_reference_snapshot()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pre_trade_batches batch
        WHERE batch.reference_snapshot_id = OLD.id
    ) THEN
        RAISE EXCEPTION
            'referenced pre-trade reference snapshot is immutable';
    END IF;
    RETURN OLD;
END;
$$;

CREATE TRIGGER protect_pre_trade_reference_snapshot
BEFORE UPDATE OR DELETE ON reference_data_snapshots
FOR EACH ROW
EXECUTE FUNCTION protect_referenced_reference_snapshot();

CREATE OR REPLACE FUNCTION enforce_pre_trade_fill_evidence()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    state_id BIGINT;
    state_instrument_id BIGINT;
    state_contract_id BIGINT;
    state_ticker VARCHAR(20);
    state_bid_price NUMERIC(24, 8);
    state_ask_price NUMERIC(24, 8);
    state_bid_quantity NUMERIC(28, 8);
    state_ask_quantity NUMERIC(28, 8);
    state_bid_event_time TIMESTAMPTZ;
    state_bid_publication_time TIMESTAMPTZ;
    state_ask_event_time TIMESTAMPTZ;
    state_ask_publication_time TIMESTAMPTZ;
    state_trading_system VARCHAR(20);
    state_trading_phase VARCHAR(20);
    state_covered_through TIMESTAMPTZ;
    state_received_at TIMESTAMPTZ;
    batch_received_at TIMESTAMPTZ;
    nominal_delay_seconds INTEGER;
    max_transport_lag_seconds INTEGER;
    contract_status VARCHAR(20);
    contract_valid_from DATE;
    contract_valid_until DATE;
    legal_reviewed_at TIMESTAMPTZ;
    transport_verified_at TIMESTAMPTZ;
    validation_status VARCHAR(20);
    validation_validated_at TIMESTAMPTZ;
    validation_valid_until TIMESTAMPTZ;
    expected_side_price NUMERIC(24, 8);
    executable_quantity NUMERIC(28, 8);
    consumed_quantity NUMERIC(28, 8);
BEGIN
    IF NEW.source_book_state_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF NEW.source_quote_id IS NOT NULL THEN
        RAISE EXCEPTION
            'trade must use exactly one price evidence source';
    END IF;

    SELECT batch.provider_contract_id
    INTO state_contract_id
    FROM pre_trade_book_states book
    JOIN pre_trade_batches batch
      ON batch.id = book.batch_id
    WHERE book.id = NEW.source_book_state_id;

    IF state_contract_id IS NULL THEN
        RAISE EXCEPTION
            'trade requires a matching fresh two-sided pre-trade book';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            'pre-trade-contract:' || state_contract_id::TEXT,
            0
        )
    );

    SELECT
        book.id,
        book.instrument_id,
        company.ticker,
        book.bid_price,
        book.ask_price,
        book.bid_quantity,
        book.ask_quantity,
        book.bid_event_time,
        book.bid_publication_time,
        book.ask_event_time,
        book.ask_publication_time,
        book.trading_system,
        book.trading_phase,
        book.covered_through,
        book.received_at,
        batch.received_at,
        provider.nominal_delay_seconds,
        provider.max_transport_lag_seconds,
        provider.status,
        provider.valid_from,
        provider.valid_until,
        provider.legal_reviewed_at,
        provider.transport_verified_at,
        proof.status,
        proof.validated_at,
        proof.valid_until
    INTO
        state_id,
        state_instrument_id,
        state_ticker,
        state_bid_price,
        state_ask_price,
        state_bid_quantity,
        state_ask_quantity,
        state_bid_event_time,
        state_bid_publication_time,
        state_ask_event_time,
        state_ask_publication_time,
        state_trading_system,
        state_trading_phase,
        state_covered_through,
        state_received_at,
        batch_received_at,
        nominal_delay_seconds,
        max_transport_lag_seconds,
        contract_status,
        contract_valid_from,
        contract_valid_until,
        legal_reviewed_at,
        transport_verified_at,
        validation_status,
        validation_validated_at,
        validation_valid_until
    FROM pre_trade_book_states book
    JOIN pre_trade_batches batch
      ON batch.id = book.batch_id
    JOIN instruments instrument
      ON instrument.id = book.instrument_id
    JOIN companies company
      ON company.instrument_id = instrument.id
    JOIN market_data_provider_contracts provider
      ON provider.id = batch.provider_contract_id
    JOIN market_data_provider_validations proof
      ON proof.id = batch.provider_validation_id
     AND proof.id = (
        SELECT latest_proof.id
        FROM market_data_provider_validations latest_proof
        WHERE latest_proof.contract_id = provider.id
        ORDER BY
            latest_proof.validated_at DESC,
            latest_proof.id DESC
        LIMIT 1
    )
    WHERE
        book.id = NEW.source_book_state_id
        AND batch.provider_contract_id = state_contract_id
        AND batch.id = (
            SELECT latest_batch.id
            FROM pre_trade_batches latest_batch
            JOIN pre_trade_stream_cursors seal
              ON seal.batch_id = latest_batch.id
            WHERE latest_batch.provider_contract_id = state_contract_id
            ORDER BY
                latest_batch.report_minute DESC,
                latest_batch.id DESC
            LIMIT 1
        )
    FOR UPDATE OF book;

    expected_side_price := CASE
        WHEN NEW.action = 'BUY' THEN state_ask_price
        ELSE state_bid_price
    END;
    executable_quantity := CASE
        WHEN NEW.action = 'BUY' THEN state_ask_quantity
        ELSE state_bid_quantity
    END;

    SELECT COALESCE(SUM(trade.shares), 0)
    INTO consumed_quantity
    FROM trades trade
    JOIN pre_trade_book_states used_book
      ON used_book.id = trade.source_book_state_id
    JOIN pre_trade_batches used_batch
      ON used_batch.id = used_book.batch_id
    WHERE
        used_batch.provider_contract_id = state_contract_id
        AND used_book.instrument_id = state_instrument_id
        AND trade.action = NEW.action
        AND (
            (
                NEW.action = 'BUY'
                AND used_book.ask_event_time = state_ask_event_time
                AND used_book.ask_publication_time
                    = state_ask_publication_time
                AND used_book.ask_price = state_ask_price
                AND used_book.ask_quantity = state_ask_quantity
            )
            OR (
                NEW.action = 'SELL'
                AND used_book.bid_event_time = state_bid_event_time
                AND used_book.bid_publication_time
                    = state_bid_publication_time
                AND used_book.bid_price = state_bid_price
                AND used_book.bid_quantity = state_bid_quantity
            )
        );

    IF (
        state_id IS NULL
        OR state_bid_price IS NULL
        OR state_ask_price IS NULL
        OR state_ticker != NEW.ticker
        OR expected_side_price IS NULL
        OR executable_quantity IS NULL
        OR consumed_quantity + NEW.shares > executable_quantity
        OR state_trading_system != 'CLOB'
        OR state_trading_phase != 'COTR'
        OR ROUND(COALESCE(NEW.quote_price, NEW.price), 8)
            != ROUND(expected_side_price, 8)
        OR batch_received_at > NEW.executed_at
        OR state_received_at > NEW.executed_at
        OR state_covered_through > NEW.executed_at
        OR NEW.executed_at > NOW() + INTERVAL '1 second'
        OR EXTRACT(
            EPOCH FROM NEW.executed_at - state_covered_through
        ) > (
            nominal_delay_seconds
            + max_transport_lag_seconds
            + 60
        )
        OR EXTRACT(
            EPOCH FROM NEW.executed_at - state_received_at
        ) > 60
        OR contract_status != 'VALIDATED'
        OR NEW.executed_at::DATE NOT BETWEEN
            contract_valid_from AND contract_valid_until
        OR legal_reviewed_at > NEW.executed_at
        OR transport_verified_at > NEW.executed_at
        OR validation_status != 'PASSED'
        OR validation_validated_at > NEW.executed_at
        OR validation_valid_until < NEW.executed_at
        OR NOT EXISTS (
            SELECT 1
            FROM market_sessions session
            WHERE session.mic = 'XSTO'
              AND session.status IN ('OPEN', 'HALF_DAY')
              AND NEW.executed_at >= session.opens_at
              AND NEW.executed_at < session.closes_at
        )
    ) THEN
        RAISE EXCEPTION
            'trade requires a matching fresh two-sided pre-trade book';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_enforce_pre_trade_fill
BEFORE INSERT ON trades
FOR EACH ROW
EXECUTE FUNCTION enforce_pre_trade_fill_evidence();

CREATE OR REPLACE FUNCTION protect_referenced_provider_validation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pre_trade_batches batch
        WHERE batch.provider_validation_id = OLD.id
    ) THEN
        RAISE EXCEPTION
            'referenced pre-trade provider validation is immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER protect_pre_trade_provider_validation
BEFORE UPDATE OR DELETE ON market_data_provider_validations
FOR EACH ROW
EXECUTE FUNCTION protect_referenced_provider_validation();

CREATE OR REPLACE FUNCTION protect_referenced_provider_contract()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pre_trade_batches batch
        WHERE batch.provider_contract_id = OLD.id
    ) THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;

    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'referenced pre-trade provider contract cannot be deleted';
    END IF;

    IF (
        ROW(
            OLD.contract_key,
            OLD.provider,
            OLD.product_name,
            OLD.data_type,
            OLD.mic,
            OLD.delivery_mode,
            OLD.transport,
            OLD.nominal_delay_seconds,
            OLD.max_transport_lag_seconds,
            OLD.usage_scope,
            OLD.non_display_category,
            OLD.external_distribution,
            OLD.reference_symbols_included,
            OLD.terms_url,
            OLD.valid_from,
            OLD.valid_until,
            OLD.legal_reviewed_at,
            OLD.transport_verified_at
        )
        IS DISTINCT FROM
        ROW(
            NEW.contract_key,
            NEW.provider,
            NEW.product_name,
            NEW.data_type,
            NEW.mic,
            NEW.delivery_mode,
            NEW.transport,
            NEW.nominal_delay_seconds,
            NEW.max_transport_lag_seconds,
            NEW.usage_scope,
            NEW.non_display_category,
            NEW.external_distribution,
            NEW.reference_symbols_included,
            NEW.terms_url,
            NEW.valid_from,
            NEW.valid_until,
            NEW.legal_reviewed_at,
            NEW.transport_verified_at
        )
    ) THEN
        RAISE EXCEPTION
            'referenced pre-trade provider contract evidence is immutable';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER protect_pre_trade_provider_contract
BEFORE UPDATE OR DELETE ON market_data_provider_contracts
FOR EACH ROW
EXECUTE FUNCTION protect_referenced_provider_contract();
