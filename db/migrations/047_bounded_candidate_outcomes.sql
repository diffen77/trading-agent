-- Preserve point-in-time evidence while tolerating a bounded unavailable book
-- minute. The journal stores the actual event minute selected from the source.

CREATE OR REPLACE FUNCTION validate_candidate_entry_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    prediction candidate_predictions%ROWTYPE;
    prediction_state pre_trade_book_states%ROWTYPE;
    prediction_batch pre_trade_batches%ROWTYPE;
    entry_state pre_trade_book_states%ROWTYPE;
    entry_batch pre_trade_batches%ROWTYPE;
    expected_price NUMERIC(24, 8);
    requested_event_time TIMESTAMPTZ;
BEGIN
    SELECT * INTO prediction
    FROM candidate_predictions
    WHERE id = NEW.prediction_id
    FOR SHARE;

    SELECT * INTO prediction_state
    FROM pre_trade_book_states
    WHERE id = prediction.source_book_state_id
    FOR SHARE;

    SELECT * INTO prediction_batch
    FROM pre_trade_batches
    WHERE id = prediction_state.batch_id
    FOR SHARE;

    SELECT * INTO entry_state
    FROM pre_trade_book_states
    WHERE id = NEW.source_book_state_id
    FOR SHARE;

    SELECT * INTO entry_batch
    FROM pre_trade_batches
    WHERE id = entry_state.batch_id
    FOR SHARE;

    requested_event_time := DATE_TRUNC('minute', prediction.observed_at);
    expected_price := (entry_state.bid_price + entry_state.ask_price) / 2;

    IF (
        prediction.id IS NULL
        OR prediction_state.id IS NULL
        OR prediction_batch.id IS NULL
        OR entry_state.id IS NULL
        OR entry_batch.id IS NULL
        OR entry_state.instrument_id != prediction_state.instrument_id
        OR entry_batch.provider_contract_id
            != prediction_batch.provider_contract_id
        OR NEW.entry_event_time < requested_event_time
        OR NEW.entry_event_time
            > requested_event_time + INTERVAL '2 minutes'
        OR entry_batch.report_minute != NEW.entry_event_time
        OR entry_state.bid_price IS NULL
        OR entry_state.ask_price IS NULL
        OR entry_state.bid_quantity <= 0
        OR entry_state.ask_quantity <= 0
        OR entry_state.trading_system != 'CLOB'
        OR entry_state.trading_phase != 'COTR'
        OR NOT EXISTS (
            SELECT 1
            FROM pre_trade_stream_cursors seal
            WHERE seal.batch_id = entry_batch.id
        )
        OR entry_state.received_at > NEW.evaluated_at
        OR NEW.entry_price != expected_price
    ) THEN
        RAISE EXCEPTION
            'candidate entry evidence does not match prediction time';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_candidate_outcome_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    prediction candidate_predictions%ROWTYPE;
    entry candidate_prediction_entries%ROWTYPE;
    source_state pre_trade_book_states%ROWTYPE;
    source_batch pre_trade_batches%ROWTYPE;
    prediction_state pre_trade_book_states%ROWTYPE;
    prediction_batch pre_trade_batches%ROWTYPE;
    expected_price NUMERIC(24, 8);
    expected_return_bps NUMERIC(18, 8);
    requested_target_time TIMESTAMPTZ;
BEGIN
    SELECT * INTO prediction
    FROM candidate_predictions
    WHERE id = NEW.prediction_id
    FOR SHARE;

    SELECT * INTO entry
    FROM candidate_prediction_entries
    WHERE id = NEW.entry_id
    FOR SHARE;

    SELECT * INTO source_state
    FROM pre_trade_book_states
    WHERE id = NEW.source_book_state_id
    FOR SHARE;

    SELECT * INTO source_batch
    FROM pre_trade_batches
    WHERE id = source_state.batch_id
    FOR SHARE;

    SELECT * INTO prediction_state
    FROM pre_trade_book_states
    WHERE id = prediction.source_book_state_id
    FOR SHARE;

    SELECT * INTO prediction_batch
    FROM pre_trade_batches
    WHERE id = prediction_state.batch_id
    FOR SHARE;

    requested_target_time := entry.entry_event_time
        + NEW.horizon_minutes * INTERVAL '1 minute';
    expected_price := (source_state.bid_price + source_state.ask_price) / 2;
    expected_return_bps :=
        ((expected_price / entry.entry_price) - 1) * 10000;

    IF (
        prediction.id IS NULL
        OR entry.id IS NULL
        OR entry.prediction_id != prediction.id
        OR NOT NEW.horizon_minutes
            = ANY(prediction.expected_horizons_minutes)
        OR source_state.id IS NULL
        OR source_batch.id IS NULL
        OR prediction_state.id IS NULL
        OR prediction_batch.id IS NULL
        OR source_state.instrument_id != prediction_state.instrument_id
        OR source_batch.provider_contract_id
            != prediction_batch.provider_contract_id
        OR source_state.bid_price IS NULL
        OR source_state.ask_price IS NULL
        OR source_state.bid_quantity <= 0
        OR source_state.ask_quantity <= 0
        OR source_state.trading_system != 'CLOB'
        OR source_state.trading_phase != 'COTR'
        OR NOT EXISTS (
            SELECT 1
            FROM pre_trade_stream_cursors seal
            WHERE seal.batch_id = source_batch.id
        )
        OR NEW.target_event_time < requested_target_time
        OR NEW.target_event_time
            > requested_target_time + INTERVAL '2 minutes'
        OR source_batch.report_minute != NEW.target_event_time
        OR source_state.received_at > NEW.evaluated_at
        OR NEW.observed_price != expected_price
        OR ABS(NEW.return_bps - expected_return_bps) > 0.00000001
        OR NOT EXISTS (
            SELECT 1
            FROM market_sessions market_session
            WHERE market_session.mic = 'XSTO'
              AND market_session.status IN ('OPEN', 'HALF_DAY')
              AND entry.entry_event_time >= market_session.opens_at
              AND entry.entry_event_time < market_session.closes_at
              AND NEW.target_event_time < market_session.closes_at
        )
    ) THEN
        RAISE EXCEPTION
            'candidate outcome evidence does not match prediction';
    END IF;
    RETURN NEW;
END;
$$;
