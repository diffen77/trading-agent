-- Freeze a distinct execution-price source for forward paper benchmarks.

ALTER TABLE paper_benchmark_experiments
    ADD COLUMN IF NOT EXISTS execution_price_source VARCHAR(40)
        NOT NULL DEFAULT 'LAST_TRADE_PLUS_BPS',
    ADD COLUMN IF NOT EXISTS execution_provider_contract_id BIGINT
        REFERENCES market_data_provider_contracts(id) ON DELETE RESTRICT;

UPDATE paper_benchmark_experiments
SET execution_provider_contract_id = quote_provider_contract_id
WHERE execution_provider_contract_id IS NULL
  AND quote_provider_contract_id IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_paper_benchmark_execution_contract'
    ) THEN
        ALTER TABLE paper_benchmark_experiments
            ADD CONSTRAINT ck_paper_benchmark_execution_contract CHECK (
                execution_price_source IN (
                    'LAST_TRADE_PLUS_BPS',
                    'TOP_OF_BOOK_PLUS_SLIPPAGE'
                )
                AND (
                    (
                        execution_price_source = 'LAST_TRADE_PLUS_BPS'
                        AND execution_provider_contract_id
                            IS NOT DISTINCT FROM quote_provider_contract_id
                    )
                    OR (
                        execution_price_source
                            = 'TOP_OF_BOOK_PLUS_SLIPPAGE'
                        AND execution_provider_contract_id IS NOT NULL
                        AND spread_bps = 0
                    )
                )
                AND (
                    execution_provider_contract_id IS NULL
                    OR benchmark_provider_contract_id IS NULL
                    OR execution_provider_contract_id
                        != benchmark_provider_contract_id
                )
                AND (
                    (
                        NOT (
                            preregistration_payload
                                ? 'execution_price_source'
                        )
                        AND NOT (
                            preregistration_payload
                                ? 'execution_provider_contract_key'
                        )
                        AND execution_price_source
                            = 'LAST_TRADE_PLUS_BPS'
                        AND execution_provider_contract_id
                            IS NOT DISTINCT FROM quote_provider_contract_id
                    )
                    OR (
                        preregistration_payload
                            ? 'execution_price_source'
                        AND preregistration_payload
                            ? 'execution_provider_contract_key'
                        AND preregistration_payload
                                ->>'execution_price_source'
                            = execution_price_source
                    )
                )
            );
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_paper_benchmark_execution_provider
    ON paper_benchmark_experiments(execution_provider_contract_id);

CREATE OR REPLACE FUNCTION initialize_paper_benchmark_audit()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM strategy_versions strategy
        WHERE strategy.id = NEW.strategy_version_id
          AND strategy.version
                = NEW.preregistration_payload->>'strategy_version'
    ) THEN
        RAISE EXCEPTION
            'preregistration strategy evidence does not match';
    END IF;
    IF (
        NEW.quote_provider_contract_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1
            FROM market_data_provider_contracts contract
            WHERE contract.id = NEW.quote_provider_contract_id
              AND contract.contract_key
                    = NEW.preregistration_payload
                        ->>'quote_provider_contract_key'
        )
    ) THEN
        RAISE EXCEPTION
            'preregistration quote provider evidence does not match';
    END IF;
    IF (
        NEW.benchmark_provider_contract_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1
            FROM market_data_provider_contracts contract
            WHERE contract.id = NEW.benchmark_provider_contract_id
              AND contract.contract_key
                    = NEW.preregistration_payload
                        ->>'benchmark_provider_contract_key'
        )
    ) THEN
        RAISE EXCEPTION
            'preregistration benchmark provider evidence does not match';
    END IF;
    IF (
        NOT (
            NEW.preregistration_payload
                ? 'execution_price_source'
        )
        OR NOT (
            NEW.preregistration_payload
                ? 'execution_provider_contract_key'
        )
        OR NEW.preregistration_payload->>'execution_price_source'
            != NEW.execution_price_source
        OR NEW.execution_provider_contract_id IS NULL
        OR NOT EXISTS (
            SELECT 1
            FROM market_data_provider_contracts contract
            WHERE contract.id = NEW.execution_provider_contract_id
              AND contract.contract_key
                    = NEW.preregistration_payload
                        ->>'execution_provider_contract_key'
        )
        OR NEW.execution_provider_contract_id
            = NEW.benchmark_provider_contract_id
        OR (
            NEW.execution_price_source = 'LAST_TRADE_PLUS_BPS'
            AND NEW.execution_provider_contract_id
                IS DISTINCT FROM NEW.quote_provider_contract_id
        )
        OR (
            NEW.execution_price_source
                = 'TOP_OF_BOOK_PLUS_SLIPPAGE'
            AND NEW.spread_bps != 0
        )
    ) THEN
        RAISE EXCEPTION
            'preregistration execution provider evidence does not match';
    END IF;
    NEW.last_transition_by = NEW.proposed_by;
    NEW.last_transition_at = NEW.proposed_at;
    NEW.last_transition_reason = 'PREREGISTERED';
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_pre_trade_fill_evidence()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    state_id BIGINT;
    state_instrument_id BIGINT;
    state_contract_id BIGINT;
    state_reference_snapshot_id BIGINT;
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
    expected_reference_price NUMERIC(24, 8);
    expected_execution_price NUMERIC(24, 8);
    executable_quantity NUMERIC(28, 8);
    consumed_quantity NUMERIC(28, 8);
    running_execution_price_source VARCHAR(40);
    running_execution_provider_contract_id BIGINT;
    running_reference_snapshot_id BIGINT;
    running_slippage_bps NUMERIC(10, 4);
    price_evidence_matches BOOLEAN;
BEGIN
    IF NEW.source_book_state_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF NEW.source_quote_id IS NOT NULL THEN
        RAISE EXCEPTION
            'trade must use exactly one price evidence source';
    END IF;

    SELECT
        batch.provider_contract_id,
        batch.reference_snapshot_id
    INTO
        state_contract_id,
        state_reference_snapshot_id
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

    SELECT
        experiment.execution_price_source,
        experiment.execution_provider_contract_id,
        experiment.reference_snapshot_id,
        experiment.slippage_bps
    INTO
        running_execution_price_source,
        running_execution_provider_contract_id,
        running_reference_snapshot_id,
        running_slippage_bps
    FROM paper_benchmark_experiments experiment
    WHERE experiment.status = 'RUNNING';

    IF running_execution_price_source
        = 'TOP_OF_BOOK_PLUS_SLIPPAGE'
    THEN
        expected_reference_price := ROUND(
            (state_bid_price + state_ask_price) / 2,
            8
        );
        expected_execution_price := ROUND(
            expected_side_price * (
                1 + (
                    CASE WHEN NEW.action = 'BUY' THEN 1 ELSE -1 END
                ) * running_slippage_bps / 10000
            ),
            8
        );
        price_evidence_matches := (
            state_contract_id
                = running_execution_provider_contract_id
            AND state_reference_snapshot_id
                = running_reference_snapshot_id
            AND (
                (
                    NEW.quote_price IS NULL
                    AND NEW.fee_amount IS NULL
                    AND NEW.spread_cost IS NULL
                    AND NEW.slippage_cost IS NULL
                    AND NEW.net_cash_effect IS NULL
                    AND ROUND(NEW.price, 8)
                        = ROUND(expected_side_price, 8)
                )
                OR (
                    NEW.quote_price IS NOT NULL
                    AND NEW.fee_amount IS NOT NULL
                    AND NEW.spread_cost IS NOT NULL
                    AND NEW.slippage_cost IS NOT NULL
                    AND NEW.net_cash_effect IS NOT NULL
                    AND ROUND(NEW.quote_price, 8)
                        = expected_reference_price
                    AND ROUND(NEW.price, 8)
                        = expected_execution_price
                )
            )
        );
    ELSE
        price_evidence_matches := (
            ROUND(COALESCE(NEW.quote_price, NEW.price), 8)
                = ROUND(expected_side_price, 8)
        );
    END IF;

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
        OR state_bid_price > state_ask_price
        OR state_ticker != NEW.ticker
        OR expected_side_price IS NULL
        OR executable_quantity IS NULL
        OR consumed_quantity + NEW.shares > executable_quantity
        OR state_trading_system != 'CLOB'
        OR state_trading_phase != 'COTR'
        OR NOT COALESCE(price_evidence_matches, FALSE)
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

CREATE OR REPLACE FUNCTION enforce_paper_benchmark_execution_contract()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    reference_instrument_count INTEGER;
    payload_has_execution_contract BOOLEAN;
BEGIN
    payload_has_execution_contract := (
        NEW.preregistration_payload ? 'execution_price_source'
        AND NEW.preregistration_payload
            ? 'execution_provider_contract_key'
    );

    IF payload_has_execution_contract THEN
        IF (
            NEW.preregistration_payload->>'execution_price_source'
                != NEW.execution_price_source
            OR NEW.execution_provider_contract_id IS NULL
            OR NOT EXISTS (
                SELECT 1
                FROM market_data_provider_contracts contract
                WHERE contract.id = NEW.execution_provider_contract_id
                  AND contract.contract_key
                        = NEW.preregistration_payload
                            ->>'execution_provider_contract_key'
            )
        ) THEN
            RAISE EXCEPTION
                'preregistration execution provider evidence does not match';
        END IF;
    ELSIF (
        OLD.preregistration_payload ? 'execution_price_source'
        OR OLD.preregistration_payload
            ? 'execution_provider_contract_key'
        OR NEW.execution_price_source != 'LAST_TRADE_PLUS_BPS'
        OR NEW.execution_provider_contract_id
            IS DISTINCT FROM NEW.quote_provider_contract_id
    ) THEN
        RAISE EXCEPTION
            'legacy preregistration cannot weaken execution evidence';
    END IF;

    IF (
        NEW.execution_provider_contract_id
            = NEW.benchmark_provider_contract_id
        OR (
            NEW.execution_price_source = 'LAST_TRADE_PLUS_BPS'
            AND NEW.execution_provider_contract_id
                IS DISTINCT FROM NEW.quote_provider_contract_id
        )
        OR (
            NEW.execution_price_source
                = 'TOP_OF_BOOK_PLUS_SLIPPAGE'
            AND NEW.spread_bps != 0
        )
    ) THEN
        RAISE EXCEPTION
            'paper benchmark execution contract is inconsistent';
    END IF;

    IF OLD.status != 'DRAFT' AND ROW(
        OLD.execution_price_source,
        OLD.execution_provider_contract_id
    ) IS DISTINCT FROM ROW(
        NEW.execution_price_source,
        NEW.execution_provider_contract_id
    ) THEN
        RAISE EXCEPTION
            'approved paper benchmark execution contract is immutable';
    END IF;

    IF (
        OLD.status = 'DRAFT'
        AND NEW.status = 'APPROVED'
    ) OR (
        OLD.status IN ('APPROVED', 'PAUSED')
        AND NEW.status = 'RUNNING'
    ) THEN
        SELECT snapshot.instrument_count
        INTO reference_instrument_count
        FROM reference_data_snapshots snapshot
        WHERE snapshot.id = NEW.reference_snapshot_id
          AND snapshot.mic = 'XSTO'
          AND snapshot.instrument_count >= 300
          AND snapshot.universe_checksum_sha256
                = NEW.universe_checksum_sha256;
        IF reference_instrument_count IS NULL THEN
            RAISE EXCEPTION
                'reference snapshot evidence does not match XSTO universe';
        END IF;

        IF NEW.execution_price_source
            = 'TOP_OF_BOOK_PLUS_SLIPPAGE'
        THEN
            IF NOT paper_benchmark_provider_evidence_ready(
                NEW.execution_provider_contract_id,
                reference_instrument_count,
                ARRAY[
                    'delayed-pre-trade-equity',
                    'realtime-equity-level-1'
                ]
            ) THEN
                RAISE EXCEPTION
                    'execution provider evidence is not runtime-ready';
            END IF;
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_benchmark_execution_contract
    ON paper_benchmark_experiments;
CREATE TRIGGER trg_paper_benchmark_execution_contract
BEFORE UPDATE ON paper_benchmark_experiments
FOR EACH ROW
EXECUTE FUNCTION enforce_paper_benchmark_execution_contract();

CREATE OR REPLACE FUNCTION tag_paper_benchmark_trade()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    running_experiment_id BIGINT;
    running_strategy_version VARCHAR(50);
    running_strategy_is_ready BOOLEAN;
    running_quote_provider_contract_id BIGINT;
    running_execution_price_source VARCHAR(40);
    running_execution_provider_contract_id BIGINT;
    running_benchmark_provider_contract_id BIGINT;
    running_reference_snapshot_id BIGINT;
    running_reference_instrument_count INTEGER;
    blocking_experiment_status VARCHAR(20);
    running_fee_bps DECIMAL(10, 4);
    running_spread_bps DECIMAL(10, 4);
    running_slippage_bps DECIMAL(10, 4);
    expected_execution_price DECIMAL(20, 8);
    expected_total_value DECIMAL(20, 8);
    expected_fee_amount DECIMAL(20, 8);
    expected_spread_cost DECIMAL(20, 8);
    expected_slippage_cost DECIMAL(20, 8);
    expected_net_cash_effect DECIMAL(20, 8);
    source_input_price DECIMAL(20, 8);
    source_reference_price DECIMAL(20, 8);
    source_quote_price DECIMAL(20, 8);
    source_quote_event_time TIMESTAMPTZ;
    source_quote_received_at TIMESTAMPTZ;
    source_quote_ticker VARCHAR(20);
    source_quote_source VARCHAR(100);
    quote_provider_name VARCHAR(100);
    quote_max_delivery_seconds INTEGER;
    source_book_bid DECIMAL(20, 8);
    source_book_ask DECIMAL(20, 8);
    source_book_ticker VARCHAR(20);
    source_book_contract_id BIGINT;
    source_book_reference_snapshot_id BIGINT;
BEGIN
    SELECT
        experiment.id,
        strategy.version,
        (
            strategy.status = 'ACTIVE'
            AND strategy.config_hash = experiment.strategy_config_hash
        ),
        experiment.quote_provider_contract_id,
        experiment.execution_price_source,
        experiment.execution_provider_contract_id,
        experiment.benchmark_provider_contract_id,
        experiment.reference_snapshot_id,
        snapshot.instrument_count,
        experiment.fee_bps,
        experiment.spread_bps,
        experiment.slippage_bps
    INTO
        running_experiment_id,
        running_strategy_version,
        running_strategy_is_ready,
        running_quote_provider_contract_id,
        running_execution_price_source,
        running_execution_provider_contract_id,
        running_benchmark_provider_contract_id,
        running_reference_snapshot_id,
        running_reference_instrument_count,
        running_fee_bps,
        running_spread_bps,
        running_slippage_bps
    FROM paper_benchmark_experiments experiment
    JOIN strategy_versions strategy
      ON strategy.id = experiment.strategy_version_id
    JOIN reference_data_snapshots snapshot
      ON snapshot.id = experiment.reference_snapshot_id
    WHERE experiment.status = 'RUNNING';

    IF running_experiment_id IS NULL THEN
        SELECT status
        INTO blocking_experiment_status
        FROM paper_benchmark_experiments
        WHERE status IN ('APPROVED', 'PAUSED')
        ORDER BY id
        LIMIT 1;
        IF blocking_experiment_status IS NOT NULL THEN
            RAISE EXCEPTION
                'paper trades are blocked while an experiment is approved or paused';
        END IF;
        IF NEW.benchmark_experiment_id IS NOT NULL THEN
            RAISE EXCEPTION
                'trade cannot reference an experiment that is not running';
        END IF;
        NEW.quote_price = COALESCE(NEW.quote_price, NEW.price);
        NEW.fee_amount = COALESCE(NEW.fee_amount, 0);
        NEW.spread_cost = COALESCE(NEW.spread_cost, 0);
        NEW.slippage_cost = COALESCE(NEW.slippage_cost, 0);
        NEW.net_cash_effect = COALESCE(
            NEW.net_cash_effect,
            NEW.total_value
        );
        IF (
            NEW.quote_price != NEW.price
            OR NEW.fee_amount != 0
            OR NEW.spread_cost != 0
            OR NEW.slippage_cost != 0
            OR NEW.net_cash_effect != NEW.total_value
        ) THEN
            RAISE EXCEPTION
                'trade costs require a running benchmark experiment';
        END IF;
        RETURN NEW;
    END IF;

    IF NOT running_strategy_is_ready THEN
        RAISE EXCEPTION
            'running experiment strategy is no longer active or hash-matched';
    END IF;
    IF (
        NOT paper_benchmark_provider_evidence_ready(
            running_quote_provider_contract_id,
            running_reference_instrument_count,
            ARRAY[
                'delayed-post-trade-equity',
                'realtime-equity-level-1'
            ]
        )
        OR NOT paper_benchmark_provider_evidence_ready(
            running_benchmark_provider_contract_id,
            1,
            ARRAY[
                'delayed-index-level',
                'realtime-index-level'
            ]
        )
        OR (
            running_execution_price_source
                = 'TOP_OF_BOOK_PLUS_SLIPPAGE'
            AND NOT paper_benchmark_provider_evidence_ready(
                running_execution_provider_contract_id,
                running_reference_instrument_count,
                ARRAY[
                    'delayed-pre-trade-equity',
                    'realtime-equity-level-1'
                ]
            )
        )
    ) THEN
        RAISE EXCEPTION
            'running experiment provider evidence is not current';
    END IF;

    IF running_execution_price_source = 'LAST_TRADE_PLUS_BPS' THEN
        IF (
            NEW.source_quote_id IS NULL
            OR NEW.source_book_state_id IS NOT NULL
        ) THEN
            RAISE EXCEPTION
                'trade requires a matching fresh source quote in an open XSTO session';
        END IF;
        SELECT
            quote.last_price,
            quote.event_time,
            quote.received_at,
            company.ticker,
            quote.source,
            contract.provider,
            (
                contract.nominal_delay_seconds
                + contract.max_transport_lag_seconds
            )
        INTO
            source_quote_price,
            source_quote_event_time,
            source_quote_received_at,
            source_quote_ticker,
            source_quote_source,
            quote_provider_name,
            quote_max_delivery_seconds
        FROM market_quotes quote
        JOIN instruments instrument
          ON instrument.id = quote.instrument_id
        JOIN companies company
          ON company.instrument_id = instrument.id
        JOIN market_data_provider_contracts contract
          ON contract.id = running_execution_provider_contract_id
        WHERE quote.id = NEW.source_quote_id
          AND instrument.mic = 'XSTO'
          AND instrument.status = 'ACTIVE';
        IF (
            source_quote_price IS NULL
            OR source_quote_ticker != NEW.ticker
            OR source_quote_source NOT LIKE quote_provider_name || '%'
            OR source_quote_event_time > NEW.executed_at
            OR source_quote_received_at > NEW.executed_at
            OR NEW.executed_at > NOW()
            OR EXTRACT(
                EPOCH FROM (
                    NEW.executed_at - source_quote_event_time
                )
            ) > quote_max_delivery_seconds
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
                'trade requires a matching fresh source quote in an open XSTO session';
        END IF;
        source_input_price := source_quote_price;
        source_reference_price := source_quote_price;
    ELSE
        IF (
            NEW.source_book_state_id IS NULL
            OR NEW.source_quote_id IS NOT NULL
        ) THEN
            RAISE EXCEPTION
                'trade requires the frozen top-of-book execution source';
        END IF;
        SELECT
            book.bid_price,
            book.ask_price,
            company.ticker,
            batch.provider_contract_id,
            batch.reference_snapshot_id
        INTO
            source_book_bid,
            source_book_ask,
            source_book_ticker,
            source_book_contract_id,
            source_book_reference_snapshot_id
        FROM pre_trade_book_states book
        JOIN pre_trade_batches batch
          ON batch.id = book.batch_id
        JOIN instruments instrument
          ON instrument.id = book.instrument_id
        JOIN companies company
          ON company.instrument_id = instrument.id
        WHERE book.id = NEW.source_book_state_id;
        IF (
            source_book_bid IS NULL
            OR source_book_ask IS NULL
            OR source_book_bid > source_book_ask
            OR source_book_ticker != NEW.ticker
            OR source_book_contract_id
                != running_execution_provider_contract_id
            OR source_book_reference_snapshot_id
                != running_reference_snapshot_id
        ) THEN
            RAISE EXCEPTION
                'trade requires matching benchmark top-of-book evidence';
        END IF;
        source_input_price := CASE
            WHEN NEW.action = 'BUY' THEN source_book_ask
            ELSE source_book_bid
        END;
        source_reference_price := ROUND(
            (source_book_bid + source_book_ask) / 2,
            8
        );
    END IF;

    IF (
        NEW.quote_price IS NULL
        AND NEW.fee_amount IS NULL
        AND NEW.spread_cost IS NULL
        AND NEW.slippage_cost IS NULL
        AND NEW.net_cash_effect IS NULL
    ) THEN
        IF ROUND(NEW.price, 8) != source_input_price THEN
            RAISE EXCEPTION
                'trade input price does not match its execution source';
        END IF;
        NEW.quote_price = source_reference_price;
    ELSIF (
        NEW.quote_price IS NULL
        OR NEW.fee_amount IS NULL
        OR NEW.spread_cost IS NULL
        OR NEW.slippage_cost IS NULL
        OR NEW.net_cash_effect IS NULL
    ) THEN
        RAISE EXCEPTION
            'trade execution cost evidence must be complete';
    END IF;

    IF running_execution_price_source = 'LAST_TRADE_PLUS_BPS' THEN
        expected_execution_price = ROUND(
            source_input_price * (
                1 + (
                    CASE WHEN NEW.action = 'BUY' THEN 1 ELSE -1 END
                ) * (
                    running_spread_bps / 2
                    + running_slippage_bps
                ) / 10000
            ),
            8
        );
        expected_spread_cost = ROUND(
            source_input_price * NEW.shares
                * running_spread_bps / 2 / 10000,
            2
        );
        expected_slippage_cost = ROUND(
            source_input_price * NEW.shares
                * running_slippage_bps / 10000,
            2
        );
    ELSE
        expected_execution_price = ROUND(
            source_input_price * (
                1 + (
                    CASE WHEN NEW.action = 'BUY' THEN 1 ELSE -1 END
                ) * running_slippage_bps / 10000
            ),
            8
        );
        expected_spread_cost = ROUND(
            (source_book_ask - source_book_bid)
                * NEW.shares / 2,
            2
        );
        expected_slippage_cost = ROUND(
            source_input_price * NEW.shares
                * running_slippage_bps / 10000,
            2
        );
    END IF;
    expected_total_value = ROUND(
        expected_execution_price * NEW.shares,
        2
    );
    expected_fee_amount = ROUND(
        expected_total_value * running_fee_bps / 10000,
        2
    );
    expected_net_cash_effect = CASE
        WHEN NEW.action = 'BUY'
            THEN expected_total_value + expected_fee_amount
        ELSE expected_total_value - expected_fee_amount
    END;

    IF NEW.fee_amount IS NULL THEN
        NEW.price = expected_execution_price;
        NEW.total_value = expected_total_value;
        NEW.fee_amount = expected_fee_amount;
        NEW.spread_cost = expected_spread_cost;
        NEW.slippage_cost = expected_slippage_cost;
        NEW.net_cash_effect = expected_net_cash_effect;
    ELSIF (
        NEW.quote_price != source_reference_price
        OR NEW.price != expected_execution_price
        OR NEW.total_value != expected_total_value
        OR NEW.fee_amount != expected_fee_amount
        OR NEW.spread_cost != expected_spread_cost
        OR NEW.slippage_cost != expected_slippage_cost
        OR NEW.net_cash_effect != expected_net_cash_effect
    ) THEN
        RAISE EXCEPTION
            'trade execution costs do not match the frozen model';
    END IF;

    IF NEW.benchmark_experiment_id IS NULL THEN
        NEW.benchmark_experiment_id = running_experiment_id;
    ELSIF NEW.benchmark_experiment_id != running_experiment_id THEN
        RAISE EXCEPTION 'trade references the wrong running experiment';
    END IF;
    IF NEW.strategy_version != running_strategy_version THEN
        RAISE EXCEPTION
            'trade strategy version does not match the running experiment';
    END IF;
    RETURN NEW;
END;
$$;
