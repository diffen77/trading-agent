-- Separate, bounded and measurable paper-exploration policy.

CREATE TABLE exploration_policy_versions (
    id BIGSERIAL PRIMARY KEY,
    version VARCHAR(50) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL,
    config JSONB NOT NULL,
    config_hash CHAR(64) NOT NULL,
    approved_by VARCHAR(100) NOT NULL,
    activated_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_exploration_policy_identity CHECK (
        version ~ '^[a-z0-9][a-z0-9._-]{2,49}$'
        AND config_hash ~ '^[0-9a-f]{64}$'
        AND LENGTH(BTRIM(approved_by)) BETWEEN 1 AND 100
    ),
    CONSTRAINT ck_exploration_policy_status CHECK (status IN ('ACTIVE', 'RETIRED')),
    CONSTRAINT ck_exploration_policy_config CHECK (
        JSONB_TYPEOF(config) = 'object'
        AND config ?& ARRAY['score_margin', 'max_positions_per_cycle', 'max_position_pct']
        AND config - 'score_margin' - 'max_positions_per_cycle' - 'max_position_pct' = '{}'::JSONB
        AND (config->>'score_margin')::NUMERIC > 0
        AND (config->>'score_margin')::NUMERIC <= 20
        AND (config->>'max_positions_per_cycle')::INTEGER BETWEEN 1 AND 3
        AND (config->>'max_position_pct')::NUMERIC > 0
        AND (config->>'max_position_pct')::NUMERIC <= 5
    )
);

CREATE UNIQUE INDEX uq_one_active_exploration_policy
    ON exploration_policy_versions(status) WHERE status = 'ACTIVE';

CREATE OR REPLACE FUNCTION enforce_exploration_policy_immutability()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'exploration policy versions cannot be deleted';
    END IF;
    IF NEW.version != OLD.version
       OR NEW.config != OLD.config
       OR NEW.config_hash != OLD.config_hash
       OR NEW.approved_by != OLD.approved_by
       OR NEW.activated_at != OLD.activated_at
       OR NOT (OLD.status = 'ACTIVE' AND NEW.status = 'RETIRED') THEN
        RAISE EXCEPTION 'exploration policy version is immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_exploration_policy_immutable
BEFORE UPDATE OR DELETE ON exploration_policy_versions
FOR EACH ROW EXECUTE FUNCTION enforce_exploration_policy_immutability();

INSERT INTO exploration_policy_versions (
    version, status, config, config_hash, approved_by, activated_at
) VALUES (
    'xsto-exploration-v1',
    'ACTIVE',
    '{"max_position_pct":5,"max_positions_per_cycle":1,"score_margin":5}'::JSONB,
    '85d634d931558688b479ed4f9f1d15359ddee22afd3fd5e697f048cd5ed66516',
    'operator-roadmap-2026-08-16',
    NOW()
);

ALTER TABLE candidate_predictions
    ADD COLUMN exploration BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN exploration_policy_version VARCHAR(50)
        REFERENCES exploration_policy_versions(version) ON DELETE RESTRICT,
    ADD COLUMN exploration_max_position_pct NUMERIC(7, 2),
    ADD CONSTRAINT ck_candidate_prediction_exploration CHECK (
        (NOT exploration AND exploration_policy_version IS NULL AND exploration_max_position_pct IS NULL)
        OR
        (exploration AND exploration_policy_version IS NOT NULL
         AND exploration_max_position_pct > 0 AND exploration_max_position_pct <= 5)
    );

CREATE INDEX idx_candidate_predictions_exploration
    ON candidate_predictions(exploration, observed_at DESC, id DESC);
