-- Versioned strategy governance and auditable learning consumption.

ALTER TABLE learnings
    ALTER COLUMN confidence TYPE DECIMAL(5, 2);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_learnings_confidence'
    ) THEN
        ALTER TABLE learnings
            ADD CONSTRAINT ck_learnings_confidence
            CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 100);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS strategy_versions (
    id BIGSERIAL PRIMARY KEY,
    version VARCHAR(50) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL,
    config JSONB NOT NULL,
    config_hash CHAR(64) NOT NULL UNIQUE,
    parent_version_id BIGINT REFERENCES strategy_versions(id)
        ON DELETE RESTRICT,
    proposed_by VARCHAR(100) NOT NULL,
    approved_by VARCHAR(100),
    approved_at TIMESTAMPTZ,
    activated_by VARCHAR(100),
    activated_at TIMESTAMPTZ,
    retired_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_strategy_version_status CHECK (
        status IN ('DRAFT', 'APPROVED', 'ACTIVE', 'RETIRED')
    ),
    CONSTRAINT ck_strategy_version_approval CHECK (
        (status = 'DRAFT' AND approved_by IS NULL AND approved_at IS NULL)
        OR
        (
            status IN ('APPROVED', 'ACTIVE', 'RETIRED')
            AND approved_by IS NOT NULL
            AND approved_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_strategy_version_activation CHECK (
        (status IN ('DRAFT', 'APPROVED')
            AND activated_by IS NULL
            AND activated_at IS NULL)
        OR
        (
            status IN ('ACTIVE', 'RETIRED')
            AND activated_by IS NOT NULL
            AND activated_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_strategy_config_object CHECK (
        jsonb_typeof(config) = 'object'
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_strategy_versions_one_active
    ON strategy_versions ((status))
    WHERE status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS strategy_version_learnings (
    strategy_version_id BIGINT NOT NULL
        REFERENCES strategy_versions(id) ON DELETE CASCADE,
    learning_id INTEGER NOT NULL REFERENCES learnings(id) ON DELETE RESTRICT,
    usage_note TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (strategy_version_id, learning_id),
    CONSTRAINT ck_strategy_learning_usage_note CHECK (
        LENGTH(BTRIM(usage_note)) BETWEEN 1 AND 2000
    )
);

CREATE TABLE IF NOT EXISTS strategy_change_proposals (
    id BIGSERIAL PRIMARY KEY,
    base_version_id BIGINT NOT NULL
        REFERENCES strategy_versions(id) ON DELETE RESTRICT,
    proposed_version_id BIGINT UNIQUE
        REFERENCES strategy_versions(id) ON DELETE RESTRICT,
    config_patch JSONB NOT NULL,
    config_hash CHAR(64) NOT NULL,
    learning_ids INTEGER[] NOT NULL,
    rationale TEXT NOT NULL,
    proposed_by VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    reviewed_by VARCHAR(100),
    reviewed_at TIMESTAMPTZ,
    rejection_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_strategy_proposal_status CHECK (
        status IN ('PENDING', 'APPROVED', 'REJECTED')
    ),
    CONSTRAINT ck_strategy_proposal_patch CHECK (
        jsonb_typeof(config_patch) = 'object'
        AND config_patch <> '{}'::jsonb
    ),
    CONSTRAINT ck_strategy_proposal_learnings CHECK (
        CARDINALITY(learning_ids) > 0
    ),
    CONSTRAINT ck_strategy_proposal_rationale CHECK (
        LENGTH(BTRIM(rationale)) BETWEEN 1 AND 4000
    ),
    CONSTRAINT ck_strategy_proposal_review CHECK (
        (
            status = 'PENDING'
            AND reviewed_by IS NULL
            AND reviewed_at IS NULL
            AND proposed_version_id IS NULL
        )
        OR
        (
            status = 'APPROVED'
            AND reviewed_by IS NOT NULL
            AND reviewed_at IS NOT NULL
            AND proposed_version_id IS NOT NULL
            AND rejection_reason IS NULL
        )
        OR
        (
            status = 'REJECTED'
            AND reviewed_by IS NOT NULL
            AND reviewed_at IS NOT NULL
            AND proposed_version_id IS NULL
            AND LENGTH(BTRIM(rejection_reason)) > 0
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_strategy_proposals_status_created
    ON strategy_change_proposals(status, created_at DESC);

INSERT INTO strategy_versions (
    version,
    status,
    config,
    config_hash,
    proposed_by,
    approved_by,
    approved_at,
    activated_by,
    activated_at
)
VALUES (
    'momentum-report-swing-v1',
    'ACTIVE',
    '{
      "name": "momentum_report_swing",
      "min_confidence": 55,
      "max_positions": 5,
      "max_position_pct": 25,
      "max_sector_positions": 2,
      "omxs30_risk_off_pct": -2.5,
      "min_holding_hours": 24,
      "sell_confidence": 80,
      "require_price_above_sma20": true,
      "stop_loss_pct": -5,
      "take_profit_pct": 10,
      "trailing_activation_pct": 5,
      "trailing_floor_pct": 2,
      "time_stop_days": 10,
      "time_stop_min_gain_pct": 3,
      "cash_warning_pct": 20
    }'::jsonb,
    '7a363941bdffa31f8bb204ad0a0828404b5fe57d83da752a66758fbfd6fd0f50',
    'bootstrap-existing-rules',
    'operator-bootstrap',
    NOW(),
    'operator-bootstrap',
    NOW()
)
ON CONFLICT (version) DO NOTHING;

ALTER TABLE ai_decisions
    ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(50),
    ADD COLUMN IF NOT EXISTS strategy_config_hash CHAR(64);

ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(50)
        NOT NULL DEFAULT 'legacy-unversioned';

CREATE INDEX IF NOT EXISTS idx_trades_strategy_executed
    ON trades(strategy_version, executed_at DESC);

ALTER TABLE strategy_insights
    ALTER COLUMN active SET DEFAULT FALSE;

UPDATE strategy_insights
SET active = FALSE
WHERE strategy_version = 'unversioned';
