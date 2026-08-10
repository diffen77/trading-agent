-- Append-only candidate predictions and forward outcome labels.
-- The model may be evaluated, but it cannot mutate policy or evidence.

ALTER TABLE prospects
    ADD COLUMN IF NOT EXISTS is_current BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS policy_version VARCHAR(50),
    ADD COLUMN IF NOT EXISTS source_book_state_id BIGINT
        REFERENCES pre_trade_book_states(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS feature_checksum_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS evidence_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_prospects_current_rank
    ON prospects(is_current, priority, confidence DESC)
    WHERE status = 'watching';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_prospects_current_evidence'
    ) THEN
        ALTER TABLE prospects
            ADD CONSTRAINT ck_prospects_current_evidence CHECK (
                (
                    NOT is_current
                    AND (
                        feature_checksum_sha256 IS NULL
                        OR feature_checksum_sha256 ~ '^[0-9a-f]{64}$'
                    )
                )
                OR (
                    is_current
                    AND policy_version
                        ~ '^[a-z0-9][a-z0-9._-]{2,49}$'
                    AND source_book_state_id IS NOT NULL
                    AND feature_checksum_sha256 ~ '^[0-9a-f]{64}$'
                    AND evidence_at IS NOT NULL
                )
            );
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS candidate_predictions (
    id BIGSERIAL PRIMARY KEY,
    ai_decision_id BIGINT NOT NULL
        REFERENCES ai_decisions(id) ON DELETE RESTRICT,
    policy_version VARCHAR(50) NOT NULL,
    strategy_version VARCHAR(50) NOT NULL,
    strategy_config_hash CHAR(64) NOT NULL,
    ticker VARCHAR(20) NOT NULL
        REFERENCES companies(ticker) ON DELETE RESTRICT,
    observed_at TIMESTAMPTZ NOT NULL,
    source_book_state_id BIGINT NOT NULL
        REFERENCES pre_trade_book_states(id) ON DELETE RESTRICT,
    feature_window_start TIMESTAMPTZ NOT NULL,
    feature_window_end TIMESTAMPTZ NOT NULL,
    latest_price NUMERIC(24, 8) NOT NULL,
    signal_rank INTEGER NOT NULL,
    signal_score NUMERIC(9, 4) NOT NULL,
    eligible BOOLEAN NOT NULL,
    reason_code VARCHAR(40) NOT NULL,
    feature_json JSONB NOT NULL,
    feature_checksum_sha256 CHAR(64) NOT NULL,
    model_action VARCHAR(10) NOT NULL,
    model_confidence NUMERIC(5, 2),
    expected_horizons_minutes INTEGER[] NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_candidate_prediction_identity UNIQUE (
        ai_decision_id,
        policy_version,
        ticker
    ),
    CONSTRAINT ck_candidate_prediction_identity CHECK (
        policy_version ~ '^[a-z0-9][a-z0-9._-]{2,49}$'
        AND strategy_version ~ '^[a-z0-9][a-z0-9._-]{2,49}$'
        AND strategy_config_hash ~ '^[0-9a-f]{64}$'
        AND feature_checksum_sha256 ~ '^[0-9a-f]{64}$'
        AND reason_code ~ '^[A-Z][A-Z0-9_]{1,39}$'
    ),
    CONSTRAINT ck_candidate_prediction_values CHECK (
        latest_price > 0
        AND signal_rank BETWEEN 1 AND 100
        AND signal_score BETWEEN 0 AND 100
        AND feature_window_start
            = feature_window_end - INTERVAL '60 minutes'
        AND feature_window_end <= observed_at
        AND JSONB_TYPEOF(feature_json) = 'object'
    ),
    CONSTRAINT ck_candidate_prediction_action CHECK (
        model_action IN ('BUY', 'SELL', 'HOLD', 'ABSTAIN')
        AND (
            model_confidence IS NULL
            OR model_confidence BETWEEN 0 AND 100
        )
        AND (
            model_action = 'ABSTAIN'
            OR model_confidence IS NOT NULL
        )
    ),
    CONSTRAINT ck_candidate_prediction_horizons CHECK (
        expected_horizons_minutes <@ ARRAY[30, 60, 120]
        AND CARDINALITY(expected_horizons_minutes) <= 3
    )
);

CREATE INDEX IF NOT EXISTS idx_candidate_predictions_policy_time
    ON candidate_predictions(policy_version, observed_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_candidate_predictions_ticker_time
    ON candidate_predictions(ticker, observed_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_candidate_predictions_outcome_due
    ON candidate_predictions(observed_at, id);

CREATE TABLE IF NOT EXISTS candidate_prediction_entries (
    id BIGSERIAL PRIMARY KEY,
    prediction_id BIGINT NOT NULL UNIQUE
        REFERENCES candidate_predictions(id) ON DELETE RESTRICT,
    entry_event_time TIMESTAMPTZ NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL,
    source_book_state_id BIGINT NOT NULL
        REFERENCES pre_trade_book_states(id) ON DELETE RESTRICT,
    entry_price NUMERIC(24, 8) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_candidate_prediction_entry_values CHECK (
        entry_price > 0
        AND entry_event_time <= evaluated_at
    )
);

CREATE INDEX IF NOT EXISTS idx_candidate_entries_event_time
    ON candidate_prediction_entries(entry_event_time, prediction_id);

CREATE TABLE IF NOT EXISTS candidate_prediction_outcomes (
    id BIGSERIAL PRIMARY KEY,
    prediction_id BIGINT NOT NULL
        REFERENCES candidate_predictions(id) ON DELETE RESTRICT,
    entry_id BIGINT NOT NULL
        REFERENCES candidate_prediction_entries(id) ON DELETE RESTRICT,
    horizon_minutes INTEGER NOT NULL,
    target_event_time TIMESTAMPTZ NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL,
    source_book_state_id BIGINT NOT NULL
        REFERENCES pre_trade_book_states(id) ON DELETE RESTRICT,
    observed_price NUMERIC(24, 8) NOT NULL,
    return_bps NUMERIC(18, 8) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_candidate_prediction_outcome UNIQUE (
        prediction_id,
        horizon_minutes
    ),
    CONSTRAINT uq_candidate_outcome_entry_horizon UNIQUE (
        entry_id,
        horizon_minutes
    ),
    CONSTRAINT ck_candidate_prediction_outcome_values CHECK (
        horizon_minutes IN (30, 60, 120)
        AND observed_price > 0
        AND target_event_time <= evaluated_at
    )
);

CREATE INDEX IF NOT EXISTS idx_candidate_outcomes_evaluated
    ON candidate_prediction_outcomes(evaluated_at DESC, id DESC);

CREATE OR REPLACE FUNCTION validate_candidate_prediction_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    parent_decision ai_decisions%ROWTYPE;
    source_state pre_trade_book_states%ROWTYPE;
    source_batch pre_trade_batches%ROWTYPE;
    company_instrument_id BIGINT;
    expected_checksum CHAR(64);
    expected_horizons INTEGER[];
    expected_price NUMERIC(24, 8);
BEGIN
    SELECT *
    INTO parent_decision
    FROM ai_decisions
    WHERE id = NEW.ai_decision_id
    FOR SHARE;

    SELECT *
    INTO source_state
    FROM pre_trade_book_states
    WHERE id = NEW.source_book_state_id
    FOR SHARE;

    SELECT *
    INTO source_batch
    FROM pre_trade_batches
    WHERE id = source_state.batch_id
    FOR SHARE;

    SELECT instrument_id
    INTO company_instrument_id
    FROM companies
    WHERE ticker = NEW.ticker;

    expected_checksum := ENCODE(
        SHA256(CONVERT_TO(NEW.feature_json::TEXT, 'UTF8')),
        'hex'
    );
    expected_price := (
        source_state.bid_price + source_state.ask_price
    ) / 2;

    SELECT COALESCE(ARRAY_AGG(horizon ORDER BY horizon), ARRAY[]::INTEGER[])
    INTO expected_horizons
    FROM UNNEST(ARRAY[30, 60, 120]) AS horizon
    WHERE EXISTS (
        SELECT 1
        FROM market_sessions market_session
        WHERE market_session.mic = 'XSTO'
          AND market_session.status IN ('OPEN', 'HALF_DAY')
          AND DATE_TRUNC('minute', parent_decision.timestamp)
                >= market_session.opens_at
          AND DATE_TRUNC('minute', parent_decision.timestamp)
                < market_session.closes_at
          AND DATE_TRUNC('minute', parent_decision.timestamp)
                + horizon * INTERVAL '1 minute'
                < market_session.closes_at
    );

    IF (
        parent_decision.id IS NULL
        OR source_state.id IS NULL
        OR source_batch.id IS NULL
        OR company_instrument_id IS NULL
        OR company_instrument_id != source_state.instrument_id
        OR parent_decision.strategy_version IS NULL
        OR parent_decision.strategy_version != NEW.strategy_version
        OR parent_decision.strategy_config_hash
            != NEW.strategy_config_hash
        OR parent_decision.timestamp != NEW.observed_at
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
        OR source_batch.report_minute != NEW.feature_window_end
        OR NEW.latest_price != expected_price
        OR NEW.feature_checksum_sha256 != expected_checksum
        OR NEW.expected_horizons_minutes != expected_horizons
        OR (NEW.feature_json ->> 'book_state_id')::BIGINT
            != source_state.id
        OR NEW.feature_json ->> 'source' != source_state.source
        OR (
            NEW.feature_json ->> 'last_report_minute'
        )::TIMESTAMPTZ != source_batch.report_minute
    ) THEN
        RAISE EXCEPTION
            'candidate prediction evidence does not match parent decision';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_candidate_prediction_insert
    ON candidate_predictions;
CREATE TRIGGER trg_candidate_prediction_insert
BEFORE INSERT ON candidate_predictions
FOR EACH ROW
EXECUTE FUNCTION validate_candidate_prediction_insert();

CREATE OR REPLACE FUNCTION reject_candidate_prediction_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'candidate prediction journal is append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_candidate_predictions_append_only
    ON candidate_predictions;
CREATE TRIGGER trg_candidate_predictions_append_only
BEFORE UPDATE OR DELETE ON candidate_predictions
FOR EACH ROW
EXECUTE FUNCTION reject_candidate_prediction_mutation();

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
BEGIN
    SELECT *
    INTO prediction
    FROM candidate_predictions
    WHERE id = NEW.prediction_id
    FOR SHARE;

    SELECT *
    INTO prediction_state
    FROM pre_trade_book_states
    WHERE id = prediction.source_book_state_id
    FOR SHARE;

    SELECT *
    INTO prediction_batch
    FROM pre_trade_batches
    WHERE id = prediction_state.batch_id
    FOR SHARE;

    SELECT *
    INTO entry_state
    FROM pre_trade_book_states
    WHERE id = NEW.source_book_state_id
    FOR SHARE;

    SELECT *
    INTO entry_batch
    FROM pre_trade_batches
    WHERE id = entry_state.batch_id
    FOR SHARE;

    expected_price := (
        entry_state.bid_price + entry_state.ask_price
    ) / 2;

    IF (
        prediction.id IS NULL
        OR prediction_state.id IS NULL
        OR prediction_batch.id IS NULL
        OR entry_state.id IS NULL
        OR entry_batch.id IS NULL
        OR entry_state.instrument_id != prediction_state.instrument_id
        OR entry_batch.provider_contract_id
            != prediction_batch.provider_contract_id
        OR NEW.entry_event_time
            != DATE_TRUNC('minute', prediction.observed_at)
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

DROP TRIGGER IF EXISTS trg_candidate_entry_insert
    ON candidate_prediction_entries;
CREATE TRIGGER trg_candidate_entry_insert
BEFORE INSERT ON candidate_prediction_entries
FOR EACH ROW
EXECUTE FUNCTION validate_candidate_entry_insert();

CREATE OR REPLACE FUNCTION reject_candidate_entry_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'candidate entry journal is append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_candidate_entries_append_only
    ON candidate_prediction_entries;
CREATE TRIGGER trg_candidate_entries_append_only
BEFORE UPDATE OR DELETE ON candidate_prediction_entries
FOR EACH ROW
EXECUTE FUNCTION reject_candidate_entry_mutation();

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
BEGIN
    SELECT *
    INTO prediction
    FROM candidate_predictions
    WHERE id = NEW.prediction_id
    FOR SHARE;

    SELECT *
    INTO entry
    FROM candidate_prediction_entries
    WHERE id = NEW.entry_id
    FOR SHARE;

    SELECT *
    INTO source_state
    FROM pre_trade_book_states
    WHERE id = NEW.source_book_state_id
    FOR SHARE;

    SELECT *
    INTO source_batch
    FROM pre_trade_batches
    WHERE id = source_state.batch_id
    FOR SHARE;

    SELECT *
    INTO prediction_state
    FROM pre_trade_book_states
    WHERE id = prediction.source_book_state_id
    FOR SHARE;

    SELECT *
    INTO prediction_batch
    FROM pre_trade_batches
    WHERE id = prediction_state.batch_id
    FOR SHARE;

    expected_price := (
        source_state.bid_price + source_state.ask_price
    ) / 2;
    expected_return_bps := (
        (expected_price / entry.entry_price) - 1
    ) * 10000;

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
        OR NEW.target_event_time != (
            entry.entry_event_time
            + NEW.horizon_minutes * INTERVAL '1 minute'
        )
        OR source_batch.report_minute != NEW.target_event_time
        OR source_state.received_at > NEW.evaluated_at
        OR NEW.observed_price != expected_price
        OR ABS(NEW.return_bps - expected_return_bps) > 0.00000001
        OR NOT EXISTS (
            SELECT 1
            FROM market_sessions market_session
            WHERE market_session.mic = 'XSTO'
              AND market_session.status IN ('OPEN', 'HALF_DAY')
              AND entry.entry_event_time
                    >= market_session.opens_at
              AND entry.entry_event_time
                    < market_session.closes_at
              AND NEW.target_event_time
                    < market_session.closes_at
        )
    ) THEN
        RAISE EXCEPTION
            'candidate outcome evidence does not match prediction';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_candidate_outcome_insert
    ON candidate_prediction_outcomes;
CREATE TRIGGER trg_candidate_outcome_insert
BEFORE INSERT ON candidate_prediction_outcomes
FOR EACH ROW
EXECUTE FUNCTION validate_candidate_outcome_insert();

CREATE OR REPLACE FUNCTION reject_candidate_outcome_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'candidate outcome journal is append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_candidate_outcomes_append_only
    ON candidate_prediction_outcomes;
CREATE TRIGGER trg_candidate_outcomes_append_only
BEFORE UPDATE OR DELETE ON candidate_prediction_outcomes
FOR EACH ROW
EXECUTE FUNCTION reject_candidate_outcome_mutation();
