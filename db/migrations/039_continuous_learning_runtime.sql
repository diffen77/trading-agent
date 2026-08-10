-- Continuous-learning runtime foundations and full XSTO shadow coverage.

ALTER TABLE candidate_predictions
    DROP CONSTRAINT ck_candidate_prediction_values;

ALTER TABLE candidate_predictions
    ADD CONSTRAINT ck_candidate_prediction_values CHECK (
        latest_price > 0
        AND signal_rank BETWEEN 1 AND 1000
        AND signal_score BETWEEN 0 AND 100
        AND feature_window_start
            = feature_window_end - INTERVAL '60 minutes'
        AND feature_window_end <= observed_at
        AND JSONB_TYPEOF(feature_json) = 'object'
    );

CREATE TABLE candidate_policy_calibration_runs (
    id BIGSERIAL PRIMARY KEY,
    calibration_key VARCHAR(100) NOT NULL UNIQUE,
    policy_version VARCHAR(50) NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL,
    session_cutoff_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL,
    reason_code VARCHAR(50) NOT NULL,
    train_sessions INTEGER NOT NULL,
    validation_sessions INTEGER NOT NULL,
    train_outcomes INTEGER NOT NULL,
    validation_outcomes INTEGER NOT NULL,
    coverage_pct NUMERIC(7, 2) NOT NULL,
    active_min_signal_score NUMERIC(7, 2) NOT NULL,
    challenger_min_signal_score NUMERIC(7, 2),
    baseline_validation_mean_bps NUMERIC(18, 8),
    challenger_validation_mean_bps NUMERIC(18, 8),
    validation_improvement_bps NUMERIC(18, 8),
    metrics JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_candidate_calibration_identity CHECK (
        calibration_key ~ '^[a-z0-9][a-z0-9:._-]{2,99}$'
        AND policy_version ~ '^[a-z0-9][a-z0-9._-]{2,49}$'
        AND reason_code ~ '^[A-Z][A-Z0-9_]{1,49}$'
    ),
    CONSTRAINT ck_candidate_calibration_status CHECK (
        status IN ('COLLECTING', 'REJECTED', 'CHALLENGER')
    ),
    CONSTRAINT ck_candidate_calibration_counts CHECK (
        train_sessions BETWEEN 0 AND 1000
        AND validation_sessions BETWEEN 0 AND 1000
        AND train_outcomes >= 0
        AND validation_outcomes >= 0
        AND coverage_pct BETWEEN 0 AND 100
        AND active_min_signal_score BETWEEN 0 AND 100
        AND (
            challenger_min_signal_score IS NULL
            OR challenger_min_signal_score BETWEEN 0 AND 100
        )
    ),
    CONSTRAINT ck_candidate_calibration_metrics CHECK (
        JSONB_TYPEOF(metrics) = 'object'
    )
);

CREATE INDEX idx_candidate_calibration_latest
    ON candidate_policy_calibration_runs(
        evaluated_at DESC,
        id DESC
    );

CREATE TABLE candidate_policy_versions (
    id BIGSERIAL PRIMARY KEY,
    version VARCHAR(50) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL,
    config JSONB NOT NULL,
    config_hash CHAR(64) NOT NULL,
    parent_version_id BIGINT
        REFERENCES candidate_policy_versions(id) ON DELETE RESTRICT,
    calibration_run_id BIGINT
        REFERENCES candidate_policy_calibration_runs(id) ON DELETE RESTRICT,
    proposed_by VARCHAR(100) NOT NULL,
    reviewed_by VARCHAR(100),
    reviewed_at TIMESTAMPTZ,
    activated_by VARCHAR(100),
    activated_at TIMESTAMPTZ,
    retired_at TIMESTAMPTZ,
    rejection_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_candidate_policy_identity CHECK (
        version ~ '^[a-z0-9][a-z0-9._-]{2,49}$'
        AND config_hash ~ '^[0-9a-f]{64}$'
        AND LENGTH(BTRIM(proposed_by)) BETWEEN 1 AND 100
    ),
    CONSTRAINT ck_candidate_policy_status CHECK (
        status IN ('DRAFT', 'APPROVED', 'ACTIVE', 'RETIRED', 'REJECTED')
    ),
    CONSTRAINT ck_candidate_policy_config CHECK (
        JSONB_TYPEOF(config) = 'object'
        AND config ? 'min_signal_score'
        AND config ? 'max_spread_bps'
        AND config - 'min_signal_score' - 'max_spread_bps'
            = '{}'::JSONB
        AND (config->>'min_signal_score')::NUMERIC BETWEEN 0 AND 100
        AND (config->>'max_spread_bps')::NUMERIC > 0
        AND (config->>'max_spread_bps')::NUMERIC <= 1000
    ),
    CONSTRAINT ck_candidate_policy_review CHECK (
        (
            status = 'DRAFT'
            AND reviewed_by IS NULL
            AND reviewed_at IS NULL
            AND rejection_reason IS NULL
        )
        OR (
            status IN ('APPROVED', 'ACTIVE', 'RETIRED')
            AND reviewed_by IS NOT NULL
            AND reviewed_at IS NOT NULL
            AND rejection_reason IS NULL
        )
        OR (
            status = 'REJECTED'
            AND reviewed_by IS NOT NULL
            AND reviewed_at IS NOT NULL
            AND LENGTH(BTRIM(rejection_reason)) BETWEEN 1 AND 2000
        )
    ),
    CONSTRAINT ck_candidate_policy_activation CHECK (
        (
            status IN ('DRAFT', 'APPROVED', 'REJECTED')
            AND activated_by IS NULL
            AND activated_at IS NULL
            AND retired_at IS NULL
        )
        OR (
            status = 'ACTIVE'
            AND activated_by IS NOT NULL
            AND activated_at IS NOT NULL
            AND retired_at IS NULL
        )
        OR (
            status = 'RETIRED'
            AND activated_by IS NOT NULL
            AND activated_at IS NOT NULL
            AND retired_at IS NOT NULL
        )
    )
);

CREATE UNIQUE INDEX uq_candidate_policy_one_active
    ON candidate_policy_versions ((status))
    WHERE status = 'ACTIVE';

CREATE INDEX idx_candidate_policy_status_created
    ON candidate_policy_versions(status, created_at DESC, id DESC);

INSERT INTO candidate_policy_versions (
    version,
    status,
    config,
    config_hash,
    proposed_by,
    reviewed_by,
    reviewed_at,
    activated_by,
    activated_at
)
VALUES (
    'xsto-momentum-v1',
    'ACTIVE',
    '{"max_spread_bps":250,"min_signal_score":0}'::JSONB,
    'c094e434d32c0976f395e9c4e5d7a7e9c3a3e9f70991e1ab3e8f3678d4b8a148',
    'bootstrap-existing-policy',
    'operator-bootstrap',
    NOW(),
    'operator-bootstrap',
    NOW()
);

CREATE TABLE continuous_learning_runs (
    id BIGSERIAL PRIMARY KEY,
    run_key VARCHAR(100) NOT NULL UNIQUE,
    evaluated_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) NOT NULL,
    outcomes_labelled INTEGER NOT NULL,
    calibration_run_id BIGINT
        REFERENCES candidate_policy_calibration_runs(id) ON DELETE RESTRICT,
    error_code VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_continuous_learning_run_key CHECK (
        run_key ~ '^[a-z0-9][a-z0-9:._-]{2,99}$'
    ),
    CONSTRAINT ck_continuous_learning_run_status CHECK (
        status IN ('SUCCEEDED', 'FAILED')
        AND outcomes_labelled >= 0
        AND (
            (status = 'SUCCEEDED' AND error_code IS NULL)
            OR (
                status = 'FAILED'
                AND error_code ~ '^[A-Z][A-Z0-9_]{1,49}$'
            )
        )
    )
);

CREATE INDEX idx_continuous_learning_runs_latest
    ON continuous_learning_runs(evaluated_at DESC, id DESC);

CREATE OR REPLACE FUNCTION reject_continuous_learning_evidence_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'continuous learning evidence is append-only';
END;
$$;

CREATE TRIGGER candidate_policy_calibration_runs_append_only
BEFORE UPDATE OR DELETE ON candidate_policy_calibration_runs
FOR EACH ROW
EXECUTE FUNCTION reject_continuous_learning_evidence_mutation();

CREATE TRIGGER continuous_learning_runs_append_only
BEFORE UPDATE OR DELETE ON continuous_learning_runs
FOR EACH ROW
EXECUTE FUNCTION reject_continuous_learning_evidence_mutation();
