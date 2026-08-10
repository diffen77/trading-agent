-- Point-in-time datasets and auditable walk-forward backtest results.

ALTER TABLE market_bars
    ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ;

UPDATE market_bars
SET available_at = received_at
WHERE available_at IS NULL;

ALTER TABLE market_bars
    ALTER COLUMN available_at SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_market_bar_availability'
    ) THEN
        ALTER TABLE market_bars
            ADD CONSTRAINT ck_market_bar_availability
            CHECK (
                available_at >= event_time
                AND available_at <= received_at + INTERVAL '5 seconds'
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_market_bars_point_in_time
    ON market_bars(
        source,
        interval_seconds,
        available_at,
        instrument_id,
        event_time
    );

CREATE TABLE IF NOT EXISTS backtest_datasets (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    provider VARCHAR(100) NOT NULL,
    bar_source VARCHAR(100) NOT NULL,
    bar_interval_seconds INTEGER NOT NULL DEFAULT 86400,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    data_cutoff TIMESTAMPTZ NOT NULL,
    benchmark_instrument_id BIGINT NOT NULL REFERENCES instruments(id),
    risk_instrument_id BIGINT NOT NULL REFERENCES instruments(id),
    benchmark_is_total_return BOOLEAN NOT NULL,
    raw_unadjusted_prices BOOLEAN NOT NULL,
    corporate_actions_complete BOOLEAN NOT NULL,
    universe_history_complete BOOLEAN NOT NULL,
    includes_inactive_and_delisted BOOLEAN NOT NULL,
    content_checksum_sha256 CHAR(64) NOT NULL,
    validated_by VARCHAR(100),
    validated_at TIMESTAMPTZ,
    revoked_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_backtest_dataset_status CHECK (
        status IN ('DRAFT', 'VALIDATED', 'REVOKED')
    ),
    CONSTRAINT ck_backtest_dataset_period CHECK (
        period_end >= period_start
    ),
    CONSTRAINT ck_backtest_dataset_interval CHECK (
        bar_interval_seconds = 86400
    ),
    CONSTRAINT ck_backtest_dataset_checksum CHECK (
        content_checksum_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_backtest_dataset_validation CHECK (
        (
            status = 'DRAFT'
            AND validated_by IS NULL
            AND validated_at IS NULL
            AND revoked_reason IS NULL
        )
        OR
        (
            status = 'VALIDATED'
            AND validated_by IS NOT NULL
            AND validated_at IS NOT NULL
            AND revoked_reason IS NULL
        )
        OR
        (
            status = 'REVOKED'
            AND validated_by IS NOT NULL
            AND validated_at IS NOT NULL
            AND LENGTH(BTRIM(revoked_reason)) > 0
        )
    )
);

CREATE TABLE IF NOT EXISTS backtest_universe_memberships (
    dataset_id BIGINT NOT NULL
        REFERENCES backtest_datasets(id) ON DELETE CASCADE,
    instrument_id BIGINT NOT NULL REFERENCES instruments(id),
    valid_from DATE NOT NULL,
    valid_to DATE,
    sector VARCHAR(200) NOT NULL,
    source VARCHAR(100) NOT NULL,
    source_available_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (dataset_id, instrument_id, valid_from),
    CONSTRAINT ck_backtest_membership_dates CHECK (
        valid_to IS NULL OR valid_to >= valid_from
    ),
    CONSTRAINT ck_backtest_membership_sector CHECK (
        LENGTH(BTRIM(sector)) > 0
    )
);

CREATE INDEX IF NOT EXISTS idx_backtest_membership_date
    ON backtest_universe_memberships(dataset_id, valid_from, valid_to);

CREATE TABLE IF NOT EXISTS backtest_corporate_actions (
    id BIGSERIAL PRIMARY KEY,
    dataset_id BIGINT NOT NULL
        REFERENCES backtest_datasets(id) ON DELETE CASCADE,
    instrument_id BIGINT NOT NULL REFERENCES instruments(id),
    action_type VARCHAR(30) NOT NULL,
    ex_date DATE NOT NULL,
    split_ratio DECIMAL(20, 10),
    cash_amount DECIMAL(20, 8),
    currency CHAR(3),
    source VARCHAR(100) NOT NULL,
    source_available_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_backtest_corporate_action UNIQUE (
        dataset_id,
        instrument_id,
        action_type,
        ex_date
    ),
    CONSTRAINT ck_backtest_corporate_action_type CHECK (
        action_type IN ('SPLIT', 'CASH_DIVIDEND', 'DELISTING')
    ),
    CONSTRAINT ck_backtest_corporate_action_values CHECK (
        (
            action_type = 'SPLIT'
            AND split_ratio > 0
            AND cash_amount IS NULL
        )
        OR
        (
            action_type = 'CASH_DIVIDEND'
            AND cash_amount >= 0
            AND split_ratio IS NULL
            AND currency ~ '^[A-Z]{3}$'
        )
        OR
        (
            action_type = 'DELISTING'
            AND split_ratio IS NULL
            AND cash_amount IS NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_backtest_actions_date
    ON backtest_corporate_actions(dataset_id, ex_date, instrument_id);

CREATE TABLE IF NOT EXISTS walk_forward_runs (
    id BIGSERIAL PRIMARY KEY,
    run_key VARCHAR(200) NOT NULL UNIQUE,
    dataset_id BIGINT NOT NULL
        REFERENCES backtest_datasets(id) ON DELETE RESTRICT,
    strategy_version_id BIGINT NOT NULL
        REFERENCES strategy_versions(id) ON DELETE RESTRICT,
    engine_version VARCHAR(50) NOT NULL,
    scope VARCHAR(50) NOT NULL,
    config JSONB NOT NULL,
    input_checksum_sha256 CHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'RUNNING',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    total_return_pct DECIMAL(16, 8),
    benchmark_return_pct DECIMAL(16, 8),
    excess_return_pct DECIMAL(16, 8),
    max_drawdown_pct DECIMAL(16, 8),
    sharpe_ratio DECIMAL(16, 8),
    turnover_ratio DECIMAL(16, 8),
    trades_count INTEGER,
    win_rate DECIMAL(16, 8),
    error_message TEXT,
    CONSTRAINT ck_walk_forward_status CHECK (
        status IN ('RUNNING', 'SUCCEEDED', 'FAILED')
    ),
    CONSTRAINT ck_walk_forward_scope CHECK (
        scope IN ('DETERMINISTIC_POLICY')
    ),
    CONSTRAINT ck_walk_forward_input_checksum CHECK (
        input_checksum_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_walk_forward_finished CHECK (
        finished_at IS NULL OR finished_at >= started_at
    )
);

CREATE TABLE IF NOT EXISTS walk_forward_folds (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES walk_forward_runs(id) ON DELETE CASCADE,
    fold_number INTEGER NOT NULL,
    train_start DATE NOT NULL,
    train_end DATE NOT NULL,
    test_start DATE NOT NULL,
    test_end DATE NOT NULL,
    metrics JSONB NOT NULL,
    UNIQUE (run_id, fold_number),
    CONSTRAINT ck_walk_forward_fold_order CHECK (
        train_end < test_start
        AND train_start <= train_end
        AND test_start <= test_end
    )
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES walk_forward_runs(id) ON DELETE CASCADE,
    fold_number INTEGER NOT NULL,
    instrument_id BIGINT NOT NULL REFERENCES instruments(id),
    entry_date DATE NOT NULL,
    exit_date DATE NOT NULL,
    shares DECIMAL(20, 8) NOT NULL,
    entry_price DECIMAL(20, 8) NOT NULL,
    exit_price DECIMAL(20, 8) NOT NULL,
    gross_pnl DECIMAL(20, 8) NOT NULL,
    fees DECIMAL(20, 8) NOT NULL,
    net_pnl DECIMAL(20, 8) NOT NULL,
    exit_reason VARCHAR(30) NOT NULL,
    CONSTRAINT ck_backtest_trade_values CHECK (
        exit_date >= entry_date
        AND shares > 0
        AND entry_price > 0
        AND exit_price > 0
        AND fees >= 0
    ),
    CONSTRAINT ck_backtest_exit_reason CHECK (
        exit_reason IN (
            'STOP_LOSS',
            'TAKE_PROFIT',
            'TRAILING_STOP',
            'TIME_STOP',
            'UNIVERSE_EXIT',
            'DELISTING',
            'PERIOD_END'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_backtest_trades_run_fold
    ON backtest_trades(run_id, fold_number, entry_date);

CREATE TABLE IF NOT EXISTS backtest_equity_curve (
    run_id BIGINT NOT NULL REFERENCES walk_forward_runs(id) ON DELETE CASCADE,
    fold_number INTEGER NOT NULL,
    session_date DATE NOT NULL,
    equity DECIMAL(20, 8) NOT NULL,
    cash DECIMAL(20, 8) NOT NULL,
    benchmark_value DECIMAL(20, 8) NOT NULL,
    PRIMARY KEY (run_id, fold_number, session_date),
    CONSTRAINT ck_backtest_equity_values CHECK (
        equity >= 0 AND cash >= 0 AND benchmark_value > 0
    )
);

ALTER TABLE backtest_results
    ADD COLUMN IF NOT EXISTS engine_version VARCHAR(50)
        NOT NULL DEFAULT 'legacy-unsafe-v0',
    ADD COLUMN IF NOT EXISTS point_in_time BOOLEAN
        NOT NULL DEFAULT FALSE;
