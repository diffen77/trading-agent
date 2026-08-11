-- Immutable, pre-registered forward paper benchmark against OMXSGI.

CREATE TABLE IF NOT EXISTS paper_benchmark_experiments (
    id BIGSERIAL PRIMARY KEY,
    experiment_key VARCHAR(100) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    strategy_version_id BIGINT NOT NULL
        REFERENCES strategy_versions(id) ON DELETE RESTRICT,
    strategy_config_hash CHAR(64) NOT NULL,
    release_sha VARCHAR(64) NOT NULL,
    release_manifest_sha256 CHAR(64) NOT NULL,
    agent_image_digest_sha256 CHAR(64) NOT NULL,
    model_backend VARCHAR(30) NOT NULL,
    model_name VARCHAR(200) NOT NULL,
    model_evidence_sha256 CHAR(64) NOT NULL,
    reference_snapshot_id BIGINT
        REFERENCES reference_data_snapshots(id) ON DELETE RESTRICT,
    universe_checksum_sha256 CHAR(64),
    quote_provider_contract_id BIGINT
        REFERENCES market_data_provider_contracts(id) ON DELETE RESTRICT,
    benchmark_provider_contract_id BIGINT
        REFERENCES market_data_provider_contracts(id) ON DELETE RESTRICT,
    benchmark_symbol VARCHAR(20) NOT NULL DEFAULT 'OMXSGI',
    benchmark_kind VARCHAR(30) NOT NULL DEFAULT 'TOTAL_RETURN_GROSS',
    benchmark_source_url TEXT NOT NULL,
    benchmark_terms_url TEXT NOT NULL,
    initial_capital DECIMAL(20, 2) NOT NULL DEFAULT 20000,
    fee_bps DECIMAL(10, 4) NOT NULL,
    spread_bps DECIMAL(10, 4) NOT NULL,
    slippage_bps DECIMAL(10, 4) NOT NULL,
    min_trading_sessions INTEGER NOT NULL DEFAULT 252,
    min_closed_trades INTEGER NOT NULL DEFAULT 30,
    max_drawdown_pct DECIMAL(8, 4) NOT NULL DEFAULT 15,
    min_data_coverage_pct DECIMAL(8, 4) NOT NULL DEFAULT 99.5,
    preregistration_payload JSONB NOT NULL,
    preregistration_canonical_json TEXT NOT NULL,
    preregistration_hash CHAR(64) NOT NULL,
    proposed_by VARCHAR(100) NOT NULL,
    proposed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_transition_by VARCHAR(100),
    last_transition_at TIMESTAMPTZ,
    last_transition_reason TEXT,
    approved_by VARCHAR(100),
    approved_at TIMESTAMPTZ,
    started_by VARCHAR(100),
    started_at TIMESTAMPTZ,
    benchmark_start_level DECIMAL(20, 8),
    benchmark_start_event_time TIMESTAMPTZ,
    benchmark_start_available_at TIMESTAMPTZ,
    benchmark_start_checksum_sha256 CHAR(64),
    completed_by VARCHAR(100),
    completed_at TIMESTAMPTZ,
    invalidated_by VARCHAR(100),
    invalidated_at TIMESTAMPTZ,
    invalidation_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_paper_benchmark_key CHECK (
        experiment_key ~ '^[a-z0-9][a-z0-9-]{2,99}$'
    ),
    CONSTRAINT ck_paper_benchmark_status CHECK (
        status IN (
            'DRAFT',
            'APPROVED',
            'RUNNING',
            'PAUSED',
            'COMPLETED',
            'INVALIDATED'
        )
    ),
    CONSTRAINT ck_paper_benchmark_strategy_hash CHECK (
        strategy_config_hash ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_paper_benchmark_release_sha CHECK (
        release_sha ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'
    ),
    CONSTRAINT ck_paper_benchmark_release_evidence CHECK (
        release_manifest_sha256 ~ '^[0-9a-f]{64}$'
        AND agent_image_digest_sha256 ~ '^[0-9a-f]{64}$'
        AND model_evidence_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_paper_benchmark_model CHECK (
        model_backend IN ('openai-compatible', 'anthropic')
        AND LENGTH(BTRIM(model_name)) BETWEEN 1 AND 200
    ),
    CONSTRAINT ck_paper_benchmark_universe CHECK (
        universe_checksum_sha256 IS NULL
        OR universe_checksum_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_paper_benchmark_identity CHECK (
        benchmark_symbol = 'OMXSGI'
        AND benchmark_kind = 'TOTAL_RETURN_GROSS'
    ),
    CONSTRAINT ck_paper_benchmark_urls CHECK (
        benchmark_source_url LIKE 'https://%'
        AND benchmark_terms_url LIKE 'https://%'
    ),
    CONSTRAINT ck_paper_benchmark_capital CHECK (
        initial_capital = 20000
    ),
    CONSTRAINT ck_paper_benchmark_costs CHECK (
        fee_bps BETWEEN 0 AND 1000
        AND spread_bps BETWEEN 0 AND 1000
        AND slippage_bps BETWEEN 0 AND 1000
    ),
    CONSTRAINT ck_paper_benchmark_criteria CHECK (
        min_trading_sessions >= 252
        AND min_closed_trades >= 30
        AND max_drawdown_pct > 0
        AND max_drawdown_pct <= 15
        AND min_data_coverage_pct >= 99.5
        AND min_data_coverage_pct <= 100
    ),
    CONSTRAINT ck_paper_benchmark_payload CHECK (
        jsonb_typeof(preregistration_payload) = 'object'
        AND preregistration_payload <> '{}'::jsonb
        AND preregistration_canonical_json::jsonb
            = preregistration_payload
        AND preregistration_hash ~ '^[0-9a-f]{64}$'
        AND preregistration_hash = ENCODE(
            SHA256(
                CONVERT_TO(
                    preregistration_canonical_json,
                    'UTF8'
                )
            ),
            'hex'
        )
        AND preregistration_payload->>'strategy_config_hash'
            = strategy_config_hash
        AND preregistration_payload->>'experiment_key'
            = experiment_key
        AND preregistration_payload->>'release_sha' = release_sha
        AND preregistration_payload->>'release_manifest_sha256'
            = release_manifest_sha256
        AND preregistration_payload->>'agent_image_digest_sha256'
            = agent_image_digest_sha256
        AND preregistration_payload->>'model_backend' = model_backend
        AND preregistration_payload->>'model_name' = model_name
        AND preregistration_payload->>'model_evidence_sha256'
            = model_evidence_sha256
        AND preregistration_payload->>'universe_checksum_sha256'
            = universe_checksum_sha256
        AND (
            preregistration_payload->>'reference_snapshot_id'
        )::BIGINT = reference_snapshot_id
        AND preregistration_payload->>'benchmark_symbol'
            = benchmark_symbol
        AND preregistration_payload->>'benchmark_kind'
            = benchmark_kind
        AND preregistration_payload->>'benchmark_source_url'
            = benchmark_source_url
        AND preregistration_payload->>'benchmark_terms_url'
            = benchmark_terms_url
        AND (
            preregistration_payload->>'initial_capital'
        )::DECIMAL = initial_capital
        AND (
            preregistration_payload#>>'{costs,fee_bps}'
        )::DECIMAL = fee_bps
        AND (
            preregistration_payload#>>'{costs,spread_bps}'
        )::DECIMAL = spread_bps
        AND (
            preregistration_payload#>>'{costs,slippage_bps}'
        )::DECIMAL = slippage_bps
        AND (
            preregistration_payload#>>'{criteria,min_trading_sessions}'
        )::INTEGER = min_trading_sessions
        AND (
            preregistration_payload#>>'{criteria,min_closed_trades}'
        )::INTEGER = min_closed_trades
        AND (
            preregistration_payload#>>'{criteria,max_drawdown_pct}'
        )::DECIMAL = max_drawdown_pct
        AND (
            preregistration_payload#>>'{criteria,min_data_coverage_pct}'
        )::DECIMAL = min_data_coverage_pct
        AND preregistration_payload->>'proposed_by' = proposed_by
    ),
    CONSTRAINT ck_paper_benchmark_actors CHECK (
        proposed_by ~ '^operator:'
        AND (
            last_transition_by IS NULL
            OR last_transition_by ~ '^operator:'
        )
        AND (approved_by IS NULL OR approved_by ~ '^operator:')
        AND (started_by IS NULL OR started_by ~ '^operator:')
        AND (completed_by IS NULL OR completed_by ~ '^operator:')
        AND (invalidated_by IS NULL OR invalidated_by ~ '^operator:')
    ),
    CONSTRAINT ck_paper_benchmark_approval_pair CHECK (
        (approved_by IS NULL) = (approved_at IS NULL)
    ),
    CONSTRAINT ck_paper_benchmark_last_transition CHECK (
        last_transition_by IS NOT NULL
        AND last_transition_at IS NOT NULL
        AND (
            last_transition_reason IS NULL
            OR LENGTH(BTRIM(last_transition_reason)) BETWEEN 1 AND 2000
        )
    ),
    CONSTRAINT ck_paper_benchmark_start_triplet CHECK (
        (started_by IS NULL)
            = (started_at IS NULL)
        AND (started_at IS NULL)
            = (benchmark_start_level IS NULL)
        AND (benchmark_start_level IS NULL)
            = (benchmark_start_event_time IS NULL)
        AND (benchmark_start_event_time IS NULL)
            = (benchmark_start_available_at IS NULL)
        AND (benchmark_start_available_at IS NULL)
            = (benchmark_start_checksum_sha256 IS NULL)
        AND (
            benchmark_start_level IS NULL
            OR benchmark_start_level > 0
        )
        AND (
            benchmark_start_event_time IS NULL
            OR benchmark_start_event_time
                <= benchmark_start_available_at
        )
        AND (
            benchmark_start_available_at IS NULL
            OR benchmark_start_available_at <= started_at
        )
        AND (
            benchmark_start_checksum_sha256 IS NULL
            OR benchmark_start_checksum_sha256
                ~ '^[0-9a-f]{64}$'
        )
    ),
    CONSTRAINT ck_paper_benchmark_completion_pair CHECK (
        (completed_by IS NULL) = (completed_at IS NULL)
    ),
    CONSTRAINT ck_paper_benchmark_invalidation_triplet CHECK (
        (invalidated_by IS NULL)
            = (invalidated_at IS NULL)
        AND (invalidated_at IS NULL)
            = (invalidation_reason IS NULL)
        AND (
            invalidation_reason IS NULL
            OR LENGTH(BTRIM(invalidation_reason)) BETWEEN 1 AND 2000
        )
    ),
    CONSTRAINT ck_paper_benchmark_evidence_gate CHECK (
        status = 'DRAFT'
        OR (
            reference_snapshot_id IS NOT NULL
            AND universe_checksum_sha256 IS NOT NULL
            AND quote_provider_contract_id IS NOT NULL
            AND benchmark_provider_contract_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_paper_benchmark_lifecycle CHECK (
        (
            status = 'DRAFT'
            AND approved_at IS NULL
            AND started_at IS NULL
            AND completed_at IS NULL
            AND invalidated_at IS NULL
        )
        OR (
            status = 'APPROVED'
            AND approved_at IS NOT NULL
            AND started_at IS NULL
            AND completed_at IS NULL
            AND invalidated_at IS NULL
        )
        OR (
            status IN ('RUNNING', 'PAUSED')
            AND approved_at IS NOT NULL
            AND started_at IS NOT NULL
            AND completed_at IS NULL
            AND invalidated_at IS NULL
        )
        OR (
            status = 'COMPLETED'
            AND approved_at IS NOT NULL
            AND started_at IS NOT NULL
            AND completed_at IS NOT NULL
            AND invalidated_at IS NULL
        )
        OR (
            status = 'INVALIDATED'
            AND completed_at IS NULL
            AND invalidated_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_paper_benchmark_timestamps CHECK (
        proposed_at <= COALESCE(approved_at, proposed_at)
        AND approved_at <= COALESCE(started_at, approved_at)
        AND started_at <= COALESCE(completed_at, started_at)
        AND proposed_at <= COALESCE(invalidated_at, proposed_at)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_paper_benchmark_one_running
    ON paper_benchmark_experiments ((status))
    WHERE status = 'RUNNING';

CREATE INDEX IF NOT EXISTS idx_paper_benchmark_status_created
    ON paper_benchmark_experiments(status, created_at DESC);

CREATE TABLE IF NOT EXISTS paper_benchmark_lifecycle_events (
    id BIGSERIAL PRIMARY KEY,
    experiment_id BIGINT NOT NULL
        REFERENCES paper_benchmark_experiments(id) ON DELETE RESTRICT,
    from_status VARCHAR(20),
    to_status VARCHAR(20) NOT NULL,
    actor VARCHAR(100) NOT NULL,
    reason TEXT,
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_paper_benchmark_event_status CHECK (
        (
            from_status IS NULL
            OR from_status IN (
                'DRAFT',
                'APPROVED',
                'RUNNING',
                'PAUSED',
                'COMPLETED',
                'INVALIDATED'
            )
        )
        AND to_status IN (
            'DRAFT',
            'APPROVED',
            'RUNNING',
            'PAUSED',
            'COMPLETED',
            'INVALIDATED'
        )
    ),
    CONSTRAINT ck_paper_benchmark_event_actor CHECK (
        actor ~ '^operator:'
    ),
    CONSTRAINT ck_paper_benchmark_event_reason CHECK (
        reason IS NULL
        OR LENGTH(BTRIM(reason)) BETWEEN 1 AND 2000
    )
);

CREATE INDEX IF NOT EXISTS idx_paper_benchmark_lifecycle
    ON paper_benchmark_lifecycle_events(experiment_id, occurred_at, id);

CREATE TABLE IF NOT EXISTS paper_benchmark_incidents (
    id BIGSERIAL PRIMARY KEY,
    experiment_id BIGINT NOT NULL
        REFERENCES paper_benchmark_experiments(id) ON DELETE RESTRICT,
    incident_key VARCHAR(100) NOT NULL,
    session_date DATE NOT NULL,
    severity VARCHAR(20) NOT NULL,
    description TEXT NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL,
    source_checksum_sha256 CHAR(64) NOT NULL,
    recorded_by VARCHAR(100) NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_paper_benchmark_incident UNIQUE (
        experiment_id, incident_key
    ),
    CONSTRAINT ck_paper_benchmark_incident_key CHECK (
        incident_key ~ '^[a-z0-9][a-z0-9-]{2,99}$'
    ),
    CONSTRAINT ck_paper_benchmark_incident_severity CHECK (
        severity IN ('WARNING', 'CRITICAL')
    ),
    CONSTRAINT ck_paper_benchmark_incident_description CHECK (
        LENGTH(BTRIM(description)) BETWEEN 1 AND 2000
    ),
    CONSTRAINT ck_paper_benchmark_incident_evidence CHECK (
        detected_at <= recorded_at
        AND source_checksum_sha256 ~ '^[0-9a-f]{64}$'
        AND recorded_by ~ '^operator:'
    )
);

CREATE INDEX IF NOT EXISTS idx_paper_benchmark_incidents
    ON paper_benchmark_incidents(
        experiment_id,
        severity,
        session_date,
        id
    );

CREATE TABLE IF NOT EXISTS paper_benchmark_daily_observations (
    id BIGSERIAL PRIMARY KEY,
    experiment_id BIGINT NOT NULL
        REFERENCES paper_benchmark_experiments(id) ON DELETE RESTRICT,
    session_date DATE NOT NULL,
    net_asset_value DECIMAL(20, 8) NOT NULL,
    session_high_nav DECIMAL(20, 8) NOT NULL,
    session_low_nav DECIMAL(20, 8) NOT NULL,
    cash DECIMAL(20, 8) NOT NULL,
    gross_exposure DECIMAL(20, 8) NOT NULL,
    benchmark_level DECIMAL(20, 8) NOT NULL,
    fees DECIMAL(20, 8) NOT NULL,
    spread_cost DECIMAL(20, 8) NOT NULL,
    slippage_cost DECIMAL(20, 8) NOT NULL,
    expected_quote_points INTEGER NOT NULL,
    received_quote_points INTEGER NOT NULL,
    event_cutoff_at TIMESTAMPTZ NOT NULL,
    data_available_at TIMESTAMPTZ NOT NULL,
    source_checksum_sha256 CHAR(64) NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_paper_benchmark_observation UNIQUE (
        experiment_id, session_date
    ),
    CONSTRAINT ck_paper_benchmark_observation_values CHECK (
        net_asset_value > 0
        AND session_high_nav >= net_asset_value
        AND session_low_nav > 0
        AND session_low_nav <= net_asset_value
        AND cash >= 0
        AND cash <= net_asset_value
        AND gross_exposure >= 0
        AND ABS(
            cash + gross_exposure - net_asset_value
        ) <= 0.01
        AND benchmark_level > 0
        AND fees >= 0
        AND spread_cost >= 0
        AND slippage_cost >= 0
        AND expected_quote_points > 0
        AND received_quote_points BETWEEN 0 AND expected_quote_points
    ),
    CONSTRAINT ck_paper_benchmark_observation_time CHECK (
        data_available_at >= event_cutoff_at
        AND recorded_at >= data_available_at
    ),
    CONSTRAINT ck_paper_benchmark_observation_checksum CHECK (
        source_checksum_sha256 ~ '^[0-9a-f]{64}$'
    )
);

CREATE INDEX IF NOT EXISTS idx_paper_benchmark_observation_date
    ON paper_benchmark_daily_observations(
        experiment_id, session_date DESC
    );

CREATE TABLE IF NOT EXISTS paper_benchmark_evaluations (
    id BIGSERIAL PRIMARY KEY,
    experiment_id BIGINT NOT NULL UNIQUE
        REFERENCES paper_benchmark_experiments(id) ON DELETE RESTRICT,
    trading_sessions INTEGER NOT NULL,
    closed_trades INTEGER NOT NULL,
    net_return_pct DECIMAL(16, 8) NOT NULL,
    benchmark_return_pct DECIMAL(16, 8) NOT NULL,
    excess_return_pct DECIMAL(16, 8) NOT NULL,
    max_drawdown_pct DECIMAL(16, 8) NOT NULL,
    data_coverage_pct DECIMAL(16, 8) NOT NULL,
    critical_incidents INTEGER NOT NULL,
    passed BOOLEAN NOT NULL,
    reason_codes JSONB NOT NULL,
    evaluated_by VARCHAR(100) NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_paper_benchmark_evaluation_values CHECK (
        trading_sessions >= 0
        AND closed_trades >= 0
        AND net_return_pct >= -100
        AND benchmark_return_pct >= -100
        AND max_drawdown_pct BETWEEN -100 AND 0
        AND data_coverage_pct BETWEEN 0 AND 100
        AND critical_incidents >= 0
    ),
    CONSTRAINT ck_paper_benchmark_evaluation_result CHECK (
        jsonb_typeof(reason_codes) = 'array'
        AND (
            (passed AND jsonb_array_length(reason_codes) = 0)
            OR (
                NOT passed
                AND jsonb_array_length(reason_codes) > 0
            )
        )
    ),
    CONSTRAINT ck_paper_benchmark_evaluator CHECK (
        evaluated_by ~ '^operator:'
    )
);

ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS benchmark_experiment_id BIGINT
        REFERENCES paper_benchmark_experiments(id) ON DELETE RESTRICT;

ALTER TABLE trades
    ALTER COLUMN executed_at TYPE TIMESTAMPTZ
        USING executed_at AT TIME ZONE 'UTC',
    ALTER COLUMN closed_at TYPE TIMESTAMPTZ
        USING closed_at AT TIME ZONE 'UTC';

ALTER TABLE trades
    ALTER COLUMN price TYPE DECIMAL(20, 8),
    ALTER COLUMN target_price TYPE DECIMAL(20, 8),
    ALTER COLUMN stop_loss TYPE DECIMAL(20, 8);

ALTER TABLE portfolio
    ALTER COLUMN avg_price TYPE DECIMAL(20, 8),
    ALTER COLUMN current_price TYPE DECIMAL(20, 8);

ALTER TABLE position_lots
    ALTER COLUMN entry_price TYPE DECIMAL(20, 8);

ALTER TABLE trade_lot_allocations
    ALTER COLUMN entry_price TYPE DECIMAL(20, 8),
    ALTER COLUMN exit_price TYPE DECIMAL(20, 8);

ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS closes_position BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS source_quote_id BIGINT
        REFERENCES market_quotes(id) ON DELETE RESTRICT;

ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS quote_price DECIMAL(20, 8),
    ADD COLUMN IF NOT EXISTS fee_amount DECIMAL(20, 8),
    ADD COLUMN IF NOT EXISTS spread_cost DECIMAL(20, 8),
    ADD COLUMN IF NOT EXISTS slippage_cost DECIMAL(20, 8),
    ADD COLUMN IF NOT EXISTS net_cash_effect DECIMAL(20, 8);

UPDATE trades
SET
    quote_price = COALESCE(quote_price, price),
    fee_amount = COALESCE(fee_amount, 0),
    spread_cost = COALESCE(spread_cost, 0),
    slippage_cost = COALESCE(slippage_cost, 0),
    net_cash_effect = COALESCE(net_cash_effect, total_value)
WHERE
    quote_price IS NULL
    OR fee_amount IS NULL
    OR spread_cost IS NULL
    OR slippage_cost IS NULL
    OR net_cash_effect IS NULL;

ALTER TABLE trades
    ALTER COLUMN quote_price SET NOT NULL,
    ALTER COLUMN fee_amount SET NOT NULL,
    ALTER COLUMN spread_cost SET NOT NULL,
    ALTER COLUMN slippage_cost SET NOT NULL,
    ALTER COLUMN net_cash_effect SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_trades_closes_position'
    ) THEN
        ALTER TABLE trades
            ADD CONSTRAINT ck_trades_closes_position CHECK (
                NOT closes_position OR action = 'SELL'
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_trades_execution_costs'
    ) THEN
        ALTER TABLE trades
            ADD CONSTRAINT ck_trades_execution_costs CHECK (
                quote_price > 0
                AND fee_amount >= 0
                AND spread_cost >= 0
                AND slippage_cost >= 0
                AND net_cash_effect > 0
            );
    END IF;
END
$$;

ALTER TABLE position_lots
    ADD COLUMN IF NOT EXISTS entry_fee_total DECIMAL(20, 8)
        NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS remaining_entry_fee DECIMAL(20, 8)
        NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_position_lot_entry_fees'
    ) THEN
        ALTER TABLE position_lots
            ADD CONSTRAINT ck_position_lot_entry_fees CHECK (
                entry_fee_total >= 0
                AND remaining_entry_fee >= 0
                AND remaining_entry_fee <= entry_fee_total
                AND (
                    remaining_shares > 0
                    OR remaining_entry_fee = 0
                )
            );
    END IF;
END
$$;

ALTER TABLE trade_lot_allocations
    ADD COLUMN IF NOT EXISTS gross_realized_pnl DECIMAL(20, 8),
    ADD COLUMN IF NOT EXISTS allocated_entry_fee DECIMAL(20, 8),
    ADD COLUMN IF NOT EXISTS allocated_exit_fee DECIMAL(20, 8);

UPDATE trade_lot_allocations
SET
    gross_realized_pnl = COALESCE(
        gross_realized_pnl,
        realized_pnl
    ),
    allocated_entry_fee = COALESCE(allocated_entry_fee, 0),
    allocated_exit_fee = COALESCE(allocated_exit_fee, 0)
WHERE
    gross_realized_pnl IS NULL
    OR allocated_entry_fee IS NULL
    OR allocated_exit_fee IS NULL;

ALTER TABLE trade_lot_allocations
    ALTER COLUMN gross_realized_pnl SET NOT NULL,
    ALTER COLUMN allocated_entry_fee SET NOT NULL,
    ALTER COLUMN allocated_exit_fee SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_trade_lot_allocation_costs'
    ) THEN
        ALTER TABLE trade_lot_allocations
            ADD CONSTRAINT ck_trade_lot_allocation_costs CHECK (
                allocated_entry_fee >= 0
                AND allocated_exit_fee >= 0
                AND realized_pnl = ROUND(
                    gross_realized_pnl
                    - allocated_entry_fee
                    - allocated_exit_fee,
                    2
                )
            );
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_trades_benchmark_experiment
    ON trades(benchmark_experiment_id, executed_at);

ALTER TABLE ai_decisions
    ADD COLUMN IF NOT EXISTS benchmark_experiment_id BIGINT
        REFERENCES paper_benchmark_experiments(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_ai_decisions_benchmark_experiment
    ON ai_decisions(benchmark_experiment_id, timestamp);

CREATE OR REPLACE FUNCTION paper_benchmark_provider_evidence_ready(
    candidate_contract_id BIGINT,
    expected_instrument_count INTEGER,
    allowed_data_types TEXT[]
)
RETURNS BOOLEAN
LANGUAGE SQL
STABLE
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM market_data_provider_contracts contract
        JOIN LATERAL (
            SELECT candidate.*
            FROM market_data_provider_validations candidate
            WHERE candidate.contract_id = contract.id
            ORDER BY
                candidate.validated_at DESC,
                candidate.id DESC
            LIMIT 1
        ) validation ON TRUE
        WHERE contract.id = candidate_contract_id
          AND contract.mic = 'XSTO'
          AND contract.data_type = ANY(allowed_data_types)
          AND contract.status = 'VALIDATED'
          AND contract.legal_reviewed_at IS NOT NULL
          AND contract.legal_reviewed_at <= NOW()
          AND contract.transport_verified_at IS NOT NULL
          AND contract.transport_verified_at <= NOW()
          AND CURRENT_DATE BETWEEN
                contract.valid_from AND contract.valid_until
          AND validation.status = 'PASSED'
          AND validation.validated_at <= NOW()
          AND validation.valid_until >= NOW()
          AND validation.expected_instruments = expected_instrument_count
          AND validation.product_covered_instruments
                = expected_instrument_count
          AND validation.symbol_mapped_instruments
                = expected_instrument_count
          AND validation.sample_file_count > 0
          AND validation.sample_quote_count > 0
          AND validation.max_observed_delivery_seconds IS NOT NULL
          AND validation.max_observed_delivery_seconds
                <= (
                    contract.nominal_delay_seconds
                    + contract.max_transport_lag_seconds
                )
    );
$$;

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
    NEW.last_transition_by = NEW.proposed_by;
    NEW.last_transition_at = NEW.proposed_at;
    NEW.last_transition_reason = 'PREREGISTERED';
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_benchmark_initialize_audit
BEFORE INSERT ON paper_benchmark_experiments
FOR EACH ROW EXECUTE FUNCTION initialize_paper_benchmark_audit();

CREATE OR REPLACE FUNCTION enforce_paper_benchmark_transition()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    reference_instrument_count INTEGER;
    strategy_is_ready BOOLEAN;
    benchmark_max_delivery_seconds INTEGER;
    ledger_is_clean BOOLEAN;
BEGIN
    IF NEW.status = OLD.status AND ROW(
        NEW.last_transition_by,
        NEW.last_transition_at,
        NEW.last_transition_reason
    ) IS DISTINCT FROM ROW(
        OLD.last_transition_by,
        OLD.last_transition_at,
        OLD.last_transition_reason
    ) THEN
        RAISE EXCEPTION
            'lifecycle audit fields require a status transition';
    END IF;

    IF NEW.status != OLD.status THEN
        IF NEW.status = 'APPROVED' THEN
            NEW.last_transition_by = NEW.approved_by;
            NEW.last_transition_at = NEW.approved_at;
            NEW.last_transition_reason = 'APPROVED';
        ELSIF OLD.status = 'APPROVED' AND NEW.status = 'RUNNING' THEN
            NEW.last_transition_by = NEW.started_by;
            NEW.last_transition_at = NEW.started_at;
            NEW.last_transition_reason = 'STARTED';
        ELSIF NEW.status = 'COMPLETED' THEN
            NEW.last_transition_by = NEW.completed_by;
            NEW.last_transition_at = NEW.completed_at;
            NEW.last_transition_reason = 'EVALUATED';
        ELSIF NEW.status = 'INVALIDATED' THEN
            NEW.last_transition_by = NEW.invalidated_by;
            NEW.last_transition_at = NEW.invalidated_at;
            NEW.last_transition_reason = NEW.invalidation_reason;
        ELSIF (
            NEW.last_transition_by IS NULL
            OR NEW.last_transition_by !~ '^operator:'
            OR NEW.last_transition_at IS NULL
            OR NEW.last_transition_at <= OLD.last_transition_at
        ) THEN
            RAISE EXCEPTION
                'pause or resume requires a new operator audit event';
        END IF;
    END IF;

    IF (
        OLD.status = 'DRAFT'
        AND NEW.status = 'APPROVED'
    ) OR (
        OLD.status IN ('APPROVED', 'PAUSED')
        AND NEW.status = 'RUNNING'
    ) THEN
        IF NEW.quote_provider_contract_id
            = NEW.benchmark_provider_contract_id
        THEN
            RAISE EXCEPTION
                'quote and benchmark require separate provider evidence';
        END IF;

        IF OLD.status IN ('DRAFT', 'APPROVED', 'PAUSED') THEN
            SELECT EXISTS (
                SELECT 1
                FROM strategy_versions strategy
                WHERE strategy.id = NEW.strategy_version_id
                  AND strategy.status = 'ACTIVE'
                  AND strategy.config_hash = NEW.strategy_config_hash
            )
            INTO strategy_is_ready;
            IF NOT strategy_is_ready THEN
                RAISE EXCEPTION
                    'strategy version is not active or its hash does not match';
            END IF;
        END IF;

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

        IF NOT paper_benchmark_provider_evidence_ready(
            NEW.quote_provider_contract_id,
            reference_instrument_count,
            ARRAY[
                'delayed-post-trade-equity',
                'realtime-equity-level-1'
            ]
        ) THEN
            RAISE EXCEPTION
                'quote provider evidence is not runtime-ready';
        END IF;
        IF NOT paper_benchmark_provider_evidence_ready(
            NEW.benchmark_provider_contract_id,
            1,
            ARRAY[
                'delayed-index-level',
                'realtime-index-level'
            ]
        ) THEN
            RAISE EXCEPTION
                'benchmark provider evidence is not runtime-ready';
        END IF;
        IF OLD.status = 'APPROVED' AND NEW.status = 'RUNNING' THEN
            SELECT
                nominal_delay_seconds + max_transport_lag_seconds
            INTO benchmark_max_delivery_seconds
            FROM market_data_provider_contracts
            WHERE id = NEW.benchmark_provider_contract_id;
            IF (
                NEW.benchmark_start_event_time IS NULL
                OR NEW.benchmark_start_available_at IS NULL
                OR NEW.benchmark_start_checksum_sha256 IS NULL
                OR NEW.benchmark_start_available_at
                    < NEW.benchmark_start_event_time
                OR NEW.benchmark_start_available_at > NEW.started_at
                OR EXTRACT(
                    EPOCH FROM (
                        NEW.benchmark_start_available_at
                        - NEW.benchmark_start_event_time
                    )
                ) > benchmark_max_delivery_seconds
            ) THEN
                RAISE EXCEPTION
                    'benchmark start evidence is missing, future-dated, or stale';
            END IF;
            SELECT (
                (SELECT COUNT(*) FROM balance) = 1
                AND EXISTS (
                    SELECT 1
                    FROM balance
                    WHERE cash = NEW.initial_capital
                      AND total_value = NEW.initial_capital
                )
                AND NOT EXISTS (SELECT 1 FROM portfolio)
                AND NOT EXISTS (
                    SELECT 1
                    FROM position_lots
                    WHERE remaining_shares > 0
                )
            )
            INTO ledger_is_clean;
            IF NOT ledger_is_clean THEN
                RAISE EXCEPTION
                    'benchmark start requires the clean initial paper ledger';
            END IF;
        END IF;
    END IF;

    IF OLD.status != 'DRAFT' AND ROW(
        OLD.strategy_version_id,
        OLD.strategy_config_hash,
        OLD.release_sha,
        OLD.release_manifest_sha256,
        OLD.agent_image_digest_sha256,
        OLD.model_backend,
        OLD.model_name,
        OLD.model_evidence_sha256,
        OLD.reference_snapshot_id,
        OLD.universe_checksum_sha256,
        OLD.quote_provider_contract_id,
        OLD.benchmark_provider_contract_id,
        OLD.benchmark_symbol,
        OLD.benchmark_kind,
        OLD.benchmark_source_url,
        OLD.benchmark_terms_url,
        OLD.initial_capital,
        OLD.fee_bps,
        OLD.spread_bps,
        OLD.slippage_bps,
        OLD.min_trading_sessions,
        OLD.min_closed_trades,
        OLD.max_drawdown_pct,
        OLD.min_data_coverage_pct,
        OLD.preregistration_payload,
        OLD.preregistration_canonical_json,
        OLD.preregistration_hash,
        OLD.proposed_by,
        OLD.proposed_at
    ) IS DISTINCT FROM ROW(
        NEW.strategy_version_id,
        NEW.strategy_config_hash,
        NEW.release_sha,
        NEW.release_manifest_sha256,
        NEW.agent_image_digest_sha256,
        NEW.model_backend,
        NEW.model_name,
        NEW.model_evidence_sha256,
        NEW.reference_snapshot_id,
        NEW.universe_checksum_sha256,
        NEW.quote_provider_contract_id,
        NEW.benchmark_provider_contract_id,
        NEW.benchmark_symbol,
        NEW.benchmark_kind,
        NEW.benchmark_source_url,
        NEW.benchmark_terms_url,
        NEW.initial_capital,
        NEW.fee_bps,
        NEW.spread_bps,
        NEW.slippage_bps,
        NEW.min_trading_sessions,
        NEW.min_closed_trades,
        NEW.max_drawdown_pct,
        NEW.min_data_coverage_pct,
        NEW.preregistration_payload,
        NEW.preregistration_canonical_json,
        NEW.preregistration_hash,
        NEW.proposed_by,
        NEW.proposed_at
    ) THEN
        RAISE EXCEPTION
            'approved paper benchmark preregistration is immutable';
    END IF;

    IF OLD.started_at IS NOT NULL AND ROW(
        OLD.started_by,
        OLD.started_at,
        OLD.benchmark_start_level,
        OLD.benchmark_start_event_time,
        OLD.benchmark_start_available_at,
        OLD.benchmark_start_checksum_sha256
    ) IS DISTINCT FROM ROW(
        NEW.started_by,
        NEW.started_at,
        NEW.benchmark_start_level,
        NEW.benchmark_start_event_time,
        NEW.benchmark_start_available_at,
        NEW.benchmark_start_checksum_sha256
    ) THEN
        RAISE EXCEPTION
            'paper benchmark start evidence is immutable';
    END IF;

    IF NEW.status != OLD.status AND NOT (
        (OLD.status = 'DRAFT' AND NEW.status IN ('APPROVED', 'INVALIDATED'))
        OR (
            OLD.status = 'APPROVED'
            AND NEW.status IN ('RUNNING', 'INVALIDATED')
        )
        OR (
            OLD.status = 'RUNNING'
            AND NEW.status IN ('PAUSED', 'COMPLETED', 'INVALIDATED')
        )
        OR (
            OLD.status = 'PAUSED'
            AND NEW.status IN ('RUNNING', 'COMPLETED', 'INVALIDATED')
        )
    ) THEN
        RAISE EXCEPTION
            'invalid paper benchmark status transition: % -> %',
            OLD.status,
            NEW.status;
    END IF;

    IF NEW.status = 'COMPLETED' AND NOT EXISTS (
        SELECT 1
        FROM paper_benchmark_evaluations evaluation
        WHERE evaluation.experiment_id = NEW.id
    ) THEN
        RAISE EXCEPTION
            'paper benchmark cannot complete without an evaluation';
    END IF;

    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_benchmark_transition
BEFORE UPDATE ON paper_benchmark_experiments
FOR EACH ROW EXECUTE FUNCTION enforce_paper_benchmark_transition();

CREATE OR REPLACE FUNCTION append_paper_benchmark_lifecycle_event()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO paper_benchmark_lifecycle_events (
            experiment_id,
            from_status,
            to_status,
            actor,
            reason,
            occurred_at
        )
        VALUES (
            NEW.id,
            NULL,
            NEW.status,
            NEW.last_transition_by,
            NEW.last_transition_reason,
            NEW.last_transition_at
        );
    ELSIF NEW.status != OLD.status THEN
        INSERT INTO paper_benchmark_lifecycle_events (
            experiment_id,
            from_status,
            to_status,
            actor,
            reason,
            occurred_at
        )
        VALUES (
            NEW.id,
            OLD.status,
            NEW.status,
            NEW.last_transition_by,
            NEW.last_transition_reason,
            NEW.last_transition_at
        );
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_benchmark_lifecycle_insert
AFTER INSERT ON paper_benchmark_experiments
FOR EACH ROW EXECUTE FUNCTION append_paper_benchmark_lifecycle_event();

CREATE TRIGGER trg_paper_benchmark_lifecycle_update
AFTER UPDATE ON paper_benchmark_experiments
FOR EACH ROW EXECUTE FUNCTION append_paper_benchmark_lifecycle_event();

CREATE OR REPLACE FUNCTION forbid_paper_benchmark_delete()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'paper benchmark audit records are append-only';
END;
$$;

CREATE TRIGGER trg_paper_benchmark_no_delete
BEFORE DELETE ON paper_benchmark_experiments
FOR EACH ROW EXECUTE FUNCTION forbid_paper_benchmark_delete();

CREATE TRIGGER trg_paper_benchmark_lifecycle_immutable
BEFORE UPDATE OR DELETE ON paper_benchmark_lifecycle_events
FOR EACH ROW EXECUTE FUNCTION forbid_paper_benchmark_delete();

CREATE TRIGGER trg_paper_benchmark_incident_immutable
BEFORE UPDATE OR DELETE ON paper_benchmark_incidents
FOR EACH ROW EXECUTE FUNCTION forbid_paper_benchmark_delete();

CREATE OR REPLACE FUNCTION enforce_paper_benchmark_incident()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    experiment_status VARCHAR(20);
    experiment_started_at TIMESTAMPTZ;
BEGIN
    SELECT status, started_at
    INTO experiment_status, experiment_started_at
    FROM paper_benchmark_experiments
    WHERE id = NEW.experiment_id
    FOR UPDATE;
    IF experiment_status NOT IN ('RUNNING', 'PAUSED') THEN
        RAISE EXCEPTION
            'incident requires a running or paused experiment';
    END IF;
    IF NEW.detected_at < experiment_started_at THEN
        RAISE EXCEPTION
            'incident cannot predate the experiment start';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM market_sessions session
        WHERE session.mic = 'XSTO'
          AND session.session_date = NEW.session_date
          AND session.status = 'OPEN'
    ) THEN
        RAISE EXCEPTION
            'incident requires an official open XSTO session';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_benchmark_incident_insert
BEFORE INSERT ON paper_benchmark_incidents
FOR EACH ROW EXECUTE FUNCTION enforce_paper_benchmark_incident();

CREATE OR REPLACE FUNCTION enforce_paper_benchmark_observation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    experiment_started_at TIMESTAMPTZ;
    experiment_status VARCHAR(20);
    reference_instrument_count INTEGER;
    max_delivery_seconds INTEGER;
    quote_provider_contract_id BIGINT;
    benchmark_provider_contract_id BIGINT;
    session_close TIMESTAMPTZ;
    derived_fees DECIMAL(20, 8);
    derived_spread_cost DECIMAL(20, 8);
    derived_slippage_cost DECIMAL(20, 8);
BEGIN
    IF TG_OP != 'INSERT' THEN
        RAISE EXCEPTION 'paper benchmark observations are append-only';
    END IF;

    SELECT
        experiment.status,
        experiment.started_at,
        snapshot.instrument_count,
        experiment.quote_provider_contract_id,
        experiment.benchmark_provider_contract_id,
        GREATEST(
            quote_contract.nominal_delay_seconds
                + quote_contract.max_transport_lag_seconds,
            benchmark_contract.nominal_delay_seconds
                + benchmark_contract.max_transport_lag_seconds
        )
    INTO
        experiment_status,
        experiment_started_at,
        reference_instrument_count,
        quote_provider_contract_id,
        benchmark_provider_contract_id,
        max_delivery_seconds
    FROM paper_benchmark_experiments experiment
    JOIN reference_data_snapshots snapshot
      ON snapshot.id = experiment.reference_snapshot_id
    JOIN market_data_provider_contracts quote_contract
      ON quote_contract.id = experiment.quote_provider_contract_id
    JOIN market_data_provider_contracts benchmark_contract
      ON benchmark_contract.id
        = experiment.benchmark_provider_contract_id
    WHERE experiment.id = NEW.experiment_id
    FOR UPDATE OF experiment;

    IF experiment_status IS NULL THEN
        RAISE EXCEPTION 'paper benchmark experiment does not exist';
    END IF;
    IF experiment_status != 'RUNNING' THEN
        RAISE EXCEPTION
            'paper benchmark observation requires RUNNING status';
    END IF;
    IF NEW.session_date
        < (experiment_started_at AT TIME ZONE 'Europe/Stockholm')::DATE
    THEN
        RAISE EXCEPTION 'observation predates the experiment start';
    END IF;
    IF NEW.expected_quote_points != reference_instrument_count THEN
        RAISE EXCEPTION
            'expected quote points must match the frozen XSTO universe';
    END IF;
    IF (
        NOT paper_benchmark_provider_evidence_ready(
            quote_provider_contract_id,
            reference_instrument_count,
            ARRAY[
                'delayed-post-trade-equity',
                'realtime-equity-level-1'
            ]
        )
        OR NOT paper_benchmark_provider_evidence_ready(
            benchmark_provider_contract_id,
            1,
            ARRAY[
                'delayed-index-level',
                'realtime-index-level'
            ]
        )
    ) THEN
        RAISE EXCEPTION
            'daily observation requires current provider evidence';
    END IF;

    SELECT closes_at
    INTO session_close
    FROM market_sessions
    WHERE mic = 'XSTO'
      AND session_date = NEW.session_date
      AND status = 'OPEN';

    IF session_close IS NULL THEN
        RAISE EXCEPTION
            'observation requires an official open XSTO session';
    END IF;
    IF NEW.event_cutoff_at != session_close THEN
        RAISE EXCEPTION
            'daily observation cutoff must equal the official XSTO close';
    END IF;
    IF EXTRACT(
        EPOCH FROM (
            NEW.data_available_at - NEW.event_cutoff_at
        )
    ) > max_delivery_seconds THEN
        RAISE EXCEPTION
            'daily observation exceeds the licensed delivery window';
    END IF;

    SELECT
        COALESCE(SUM(fee_amount), 0),
        COALESCE(SUM(spread_cost), 0),
        COALESCE(SUM(slippage_cost), 0)
    INTO
        derived_fees,
        derived_spread_cost,
        derived_slippage_cost
    FROM trades
    WHERE benchmark_experiment_id = NEW.experiment_id
      AND (
          executed_at AT TIME ZONE 'Europe/Stockholm'
      )::DATE = NEW.session_date;
    IF (
        NEW.fees != derived_fees
        OR NEW.spread_cost != derived_spread_cost
        OR NEW.slippage_cost != derived_slippage_cost
    ) THEN
        RAISE EXCEPTION
            'daily costs must match the immutable trade ledger';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_benchmark_observation_insert
BEFORE INSERT ON paper_benchmark_daily_observations
FOR EACH ROW EXECUTE FUNCTION enforce_paper_benchmark_observation();

CREATE TRIGGER trg_paper_benchmark_observation_immutable
BEFORE UPDATE OR DELETE ON paper_benchmark_daily_observations
FOR EACH ROW EXECUTE FUNCTION enforce_paper_benchmark_observation();

CREATE OR REPLACE FUNCTION populate_paper_benchmark_evaluation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    experiment paper_benchmark_experiments%ROWTYPE;
    final_nav DECIMAL(20, 8);
    final_benchmark DECIMAL(20, 8);
    expected_points BIGINT;
    received_points BIGINT;
BEGIN
    SELECT *
    INTO experiment
    FROM paper_benchmark_experiments
    WHERE id = NEW.experiment_id
    FOR UPDATE;

    IF experiment.status NOT IN ('RUNNING', 'PAUSED') THEN
        RAISE EXCEPTION
            'paper benchmark evaluation requires RUNNING or PAUSED status';
    END IF;

    SELECT
        COUNT(*),
        COALESCE(SUM(expected_quote_points), 0),
        COALESCE(SUM(received_quote_points), 0)
    INTO
        NEW.trading_sessions,
        expected_points,
        received_points
    FROM paper_benchmark_daily_observations
    WHERE experiment_id = NEW.experiment_id;

    SELECT COUNT(*)
    INTO NEW.closed_trades
    FROM trades
    WHERE benchmark_experiment_id = NEW.experiment_id
      AND action = 'SELL'
      AND closes_position;

    SELECT COUNT(*)
    INTO NEW.critical_incidents
    FROM paper_benchmark_incidents
    WHERE experiment_id = NEW.experiment_id
      AND severity = 'CRITICAL';

    IF NEW.trading_sessions < experiment.min_trading_sessions THEN
        RAISE EXCEPTION
            'paper benchmark requires at least % trading sessions',
            experiment.min_trading_sessions;
    END IF;
    IF NEW.closed_trades < experiment.min_closed_trades THEN
        RAISE EXCEPTION
            'paper benchmark requires at least % closed trades',
            experiment.min_closed_trades;
    END IF;

    SELECT net_asset_value, benchmark_level
    INTO final_nav, final_benchmark
    FROM paper_benchmark_daily_observations
    WHERE experiment_id = NEW.experiment_id
    ORDER BY session_date DESC
    LIMIT 1;

    NEW.net_return_pct = (
        final_nav / experiment.initial_capital - 1
    ) * 100;
    NEW.benchmark_return_pct = (
        final_benchmark / experiment.benchmark_start_level - 1
    ) * 100;
    NEW.excess_return_pct =
        NEW.net_return_pct - NEW.benchmark_return_pct;
    NEW.data_coverage_pct =
        received_points::DECIMAL / expected_points * 100;

    SELECT COALESCE(
        MIN(
            (
                point.session_low_nav
                / GREATEST(experiment.initial_capital, point.running_peak)
                - 1
            ) * 100
        ),
        0
    )
    INTO NEW.max_drawdown_pct
    FROM (
        SELECT
            session_low_nav,
            MAX(session_high_nav) OVER (
                ORDER BY session_date
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS running_peak
        FROM paper_benchmark_daily_observations
        WHERE experiment_id = NEW.experiment_id
    ) point;

    NEW.reason_codes = '[]'::JSONB;
    IF NEW.net_return_pct <= 0 THEN
        NEW.reason_codes =
            NEW.reason_codes || JSONB_BUILD_ARRAY('NET_RETURN_NOT_POSITIVE');
    END IF;
    IF NEW.excess_return_pct <= 0 THEN
        NEW.reason_codes =
            NEW.reason_codes || JSONB_BUILD_ARRAY('BENCHMARK_NOT_OUTPERFORMED');
    END IF;
    IF NEW.max_drawdown_pct < -experiment.max_drawdown_pct THEN
        NEW.reason_codes =
            NEW.reason_codes || JSONB_BUILD_ARRAY('MAX_DRAWDOWN_EXCEEDED');
    END IF;
    IF NEW.data_coverage_pct < experiment.min_data_coverage_pct THEN
        NEW.reason_codes = NEW.reason_codes
            || JSONB_BUILD_ARRAY('DATA_COVERAGE_BELOW_MINIMUM');
    END IF;
    IF NEW.critical_incidents > 0 THEN
        NEW.reason_codes =
            NEW.reason_codes || JSONB_BUILD_ARRAY('CRITICAL_INCIDENTS_PRESENT');
    END IF;
    NEW.passed = JSONB_ARRAY_LENGTH(NEW.reason_codes) = 0;
    NEW.evaluated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_benchmark_evaluation_insert
BEFORE INSERT ON paper_benchmark_evaluations
FOR EACH ROW EXECUTE FUNCTION populate_paper_benchmark_evaluation();

CREATE TRIGGER trg_paper_benchmark_evaluation_immutable
BEFORE UPDATE OR DELETE ON paper_benchmark_evaluations
FOR EACH ROW EXECUTE FUNCTION forbid_paper_benchmark_delete();

CREATE OR REPLACE FUNCTION complete_paper_benchmark_after_evaluation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE paper_benchmark_experiments
    SET
        status = 'COMPLETED',
        completed_by = NEW.evaluated_by,
        completed_at = NEW.evaluated_at
    WHERE id = NEW.experiment_id;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_benchmark_complete
AFTER INSERT ON paper_benchmark_evaluations
FOR EACH ROW EXECUTE FUNCTION complete_paper_benchmark_after_evaluation();

CREATE OR REPLACE FUNCTION tag_paper_benchmark_trade()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    running_experiment_id BIGINT;
    running_strategy_version VARCHAR(50);
    running_strategy_is_ready BOOLEAN;
    running_quote_provider_contract_id BIGINT;
    running_benchmark_provider_contract_id BIGINT;
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
    source_quote_price DECIMAL(20, 8);
    source_quote_event_time TIMESTAMPTZ;
    source_quote_received_at TIMESTAMPTZ;
    source_quote_ticker VARCHAR(20);
    source_quote_source VARCHAR(100);
    quote_provider_name VARCHAR(100);
    quote_max_delivery_seconds INTEGER;
BEGIN
    SELECT
        experiment.id,
        strategy.version,
        (
            strategy.status = 'ACTIVE'
            AND strategy.config_hash = experiment.strategy_config_hash
        ),
        experiment.quote_provider_contract_id,
        experiment.benchmark_provider_contract_id,
        snapshot.instrument_count,
        experiment.fee_bps,
        experiment.spread_bps,
        experiment.slippage_bps
    INTO
        running_experiment_id,
        running_strategy_version,
        running_strategy_is_ready,
        running_quote_provider_contract_id,
        running_benchmark_provider_contract_id,
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
    ) THEN
        RAISE EXCEPTION
            'running experiment provider evidence is not current';
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
      ON contract.id = running_quote_provider_contract_id
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
              AND session.status = 'OPEN'
              AND NEW.executed_at
                    BETWEEN session.opens_at AND session.closes_at
        )
    ) THEN
        RAISE EXCEPTION
            'trade requires a matching fresh source quote in an open XSTO session';
    END IF;

    IF (
        NEW.quote_price IS NULL
        AND NEW.fee_amount IS NULL
        AND NEW.spread_cost IS NULL
        AND NEW.slippage_cost IS NULL
        AND NEW.net_cash_effect IS NULL
    ) THEN
        IF ROUND(NEW.price, 8) != source_quote_price THEN
            RAISE EXCEPTION
                'trade input price does not match its source quote';
        END IF;
        NEW.quote_price = source_quote_price;
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

    expected_execution_price = ROUND(
        NEW.quote_price * (
            1 + (
                CASE WHEN NEW.action = 'BUY' THEN 1 ELSE -1 END
            ) * (
                running_spread_bps / 2 + running_slippage_bps
            ) / 10000
        ),
        8
    );
    expected_total_value = ROUND(
        expected_execution_price * NEW.shares,
        2
    );
    expected_fee_amount = ROUND(
        expected_total_value * running_fee_bps / 10000,
        2
    );
    expected_spread_cost = ROUND(
        NEW.quote_price * NEW.shares
            * running_spread_bps / 2 / 10000,
        2
    );
    expected_slippage_cost = ROUND(
        NEW.quote_price * NEW.shares
            * running_slippage_bps / 10000,
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
        NEW.quote_price != source_quote_price
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

CREATE TRIGGER trg_tag_paper_benchmark_trade
BEFORE INSERT ON trades
FOR EACH ROW EXECUTE FUNCTION tag_paper_benchmark_trade();

CREATE OR REPLACE FUNCTION tag_paper_benchmark_ai_decision()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    running_experiment_id BIGINT;
    running_strategy_version VARCHAR(50);
    running_strategy_config_hash CHAR(64);
    running_strategy_is_ready BOOLEAN;
    running_quote_provider_contract_id BIGINT;
    running_benchmark_provider_contract_id BIGINT;
    running_reference_instrument_count INTEGER;
    blocking_experiment_status VARCHAR(20);
BEGIN
    SELECT
        experiment.id,
        strategy.version,
        experiment.strategy_config_hash,
        (
            strategy.status = 'ACTIVE'
            AND strategy.config_hash = experiment.strategy_config_hash
        ),
        experiment.quote_provider_contract_id,
        experiment.benchmark_provider_contract_id,
        snapshot.instrument_count
    INTO
        running_experiment_id,
        running_strategy_version,
        running_strategy_config_hash,
        running_strategy_is_ready,
        running_quote_provider_contract_id,
        running_benchmark_provider_contract_id,
        running_reference_instrument_count
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
                'AI decisions are blocked while an experiment is approved or paused';
        END IF;
        IF NEW.benchmark_experiment_id IS NOT NULL THEN
            RAISE EXCEPTION
                'AI decision cannot reference an experiment that is not running';
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
    ) THEN
        RAISE EXCEPTION
            'running experiment provider evidence is not current';
    END IF;

    IF NEW.benchmark_experiment_id IS NULL THEN
        NEW.benchmark_experiment_id = running_experiment_id;
    ELSIF NEW.benchmark_experiment_id != running_experiment_id THEN
        RAISE EXCEPTION
            'AI decision references the wrong running experiment';
    END IF;
    IF NEW.strategy_version != running_strategy_version THEN
        RAISE EXCEPTION
            'AI decision strategy version does not match the running experiment';
    END IF;
    IF NEW.strategy_config_hash != running_strategy_config_hash THEN
        RAISE EXCEPTION
            'AI decision strategy hash does not match the running experiment';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_tag_paper_benchmark_ai_decision
BEFORE INSERT ON ai_decisions
FOR EACH ROW EXECUTE FUNCTION tag_paper_benchmark_ai_decision();
