-- Isolated control-versus-graph-memory model comparisons.

CREATE TABLE knowledge_shadow_runs (
    id BIGSERIAL PRIMARY KEY,
    run_key VARCHAR(100) NOT NULL UNIQUE,
    source_decision_id BIGINT NOT NULL UNIQUE
        REFERENCES ai_decisions(id) ON DELETE RESTRICT,
    evaluated_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) NOT NULL,
    reason_code VARCHAR(50) NOT NULL,
    call_order VARCHAR(20) NOT NULL,
    context_checksum_sha256 CHAR(64) NOT NULL,
    evidence_provenance VARCHAR(100) NOT NULL,
    evidence_as_of TIMESTAMPTZ NOT NULL,
    evidence_checksum_sha256 CHAR(64) NOT NULL,
    evidence_fact_count INTEGER NOT NULL,
    candidate_count INTEGER NOT NULL,
    comparison_count INTEGER NOT NULL,
    changed_count INTEGER NOT NULL,
    control_prompt_tokens INTEGER NOT NULL,
    control_response_tokens INTEGER NOT NULL,
    memory_prompt_tokens INTEGER NOT NULL,
    memory_response_tokens INTEGER NOT NULL,
    model_backend VARCHAR(30) NOT NULL,
    model_name VARCHAR(200) NOT NULL,
    model_provider VARCHAR(80),
    reasoning_effort VARCHAR(20),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_knowledge_shadow_identity CHECK (
        run_key ~ '^knowledge-shadow:[1-9][0-9]*$'
        AND context_checksum_sha256 ~ '^[0-9a-f]{64}$'
        AND evidence_checksum_sha256 ~ '^[0-9a-f]{64}$'
        AND evidence_provenance
            = 'neo4j:candidate-outcome-aggregate-v1'
        AND model_backend ~ '^[a-z0-9][a-z0-9._-]{1,29}$'
        AND LENGTH(BTRIM(model_name)) BETWEEN 1 AND 200
        AND (
            model_provider IS NULL
            OR LENGTH(BTRIM(model_provider)) BETWEEN 1 AND 80
        )
        AND (
            reasoning_effort IS NULL
            OR reasoning_effort IN (
                'none', 'low', 'medium', 'high', 'xhigh', 'max'
            )
        )
    ),
    CONSTRAINT ck_knowledge_shadow_counts CHECK (
        evidence_as_of <= evaluated_at
        AND evidence_fact_count BETWEEN 0 AND 60
        AND candidate_count BETWEEN 0 AND 50
        AND comparison_count BETWEEN 0 AND 50
        AND changed_count BETWEEN 0 AND comparison_count
        AND control_prompt_tokens BETWEEN 0 AND 10000000
        AND control_response_tokens BETWEEN 0 AND 10000000
        AND memory_prompt_tokens BETWEEN 0 AND 10000000
        AND memory_response_tokens BETWEEN 0 AND 10000000
    ),
    CONSTRAINT ck_knowledge_shadow_state CHECK (
        reason_code ~ '^[A-Z][A-Z0-9_]{1,49}$'
        AND (
            (
                status = 'SUCCEEDED'
                AND reason_code = 'COMPARED'
                AND call_order IN ('CONTROL_FIRST', 'MEMORY_FIRST')
                AND evidence_fact_count > 0
                AND comparison_count > 0
            )
            OR (
                status = 'SKIPPED'
                AND reason_code IN (
                    'MODEL_CONFIG_MISMATCH',
                    'NO_CANDIDATES',
                    'NO_ELIGIBLE_EVIDENCE',
                    'STRATEGY_UNAVAILABLE'
                )
                AND call_order = 'NOT_RUN'
                AND comparison_count = 0
                AND changed_count = 0
                AND control_prompt_tokens = 0
                AND control_response_tokens = 0
                AND memory_prompt_tokens = 0
                AND memory_response_tokens = 0
            )
            OR (
                status = 'FAILED'
                AND reason_code IN (
                    'GRAPH_UNAVAILABLE',
                    'INVALID_MODEL_RESPONSE',
                    'MODEL_UNAVAILABLE'
                )
                AND call_order IN (
                    'NOT_RUN',
                    'CONTROL_FIRST',
                    'MEMORY_FIRST'
                )
                AND comparison_count = 0
                AND changed_count = 0
            )
        )
    )
);

CREATE INDEX idx_knowledge_shadow_runs_latest
    ON knowledge_shadow_runs(evaluated_at DESC, id DESC);

CREATE TABLE knowledge_shadow_decisions (
    id BIGSERIAL PRIMARY KEY,
    shadow_run_id BIGINT NOT NULL
        REFERENCES knowledge_shadow_runs(id) ON DELETE RESTRICT,
    ticker VARCHAR(20) NOT NULL,
    control_action VARCHAR(10) NOT NULL,
    memory_action VARCHAR(10) NOT NULL,
    control_confidence NUMERIC(5, 2),
    memory_confidence NUMERIC(5, 2),
    changed BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_knowledge_shadow_decision UNIQUE (
        shadow_run_id,
        ticker
    ),
    CONSTRAINT ck_knowledge_shadow_decision_ticker CHECK (
        ticker ~ '^[A-Z0-9][A-Z0-9.-]{0,19}$'
    ),
    CONSTRAINT ck_knowledge_shadow_decision_values CHECK (
        control_action IN ('BUY', 'SELL', 'HOLD', 'ABSTAIN')
        AND memory_action IN ('BUY', 'SELL', 'HOLD', 'ABSTAIN')
        AND (
            control_confidence IS NULL
            OR control_confidence BETWEEN 0 AND 100
        )
        AND (
            memory_confidence IS NULL
            OR memory_confidence BETWEEN 0 AND 100
        )
        AND (
            control_action != 'ABSTAIN'
            OR control_confidence IS NULL
        )
        AND (
            memory_action != 'ABSTAIN'
            OR memory_confidence IS NULL
        )
        AND changed = (control_action != memory_action)
    )
);

CREATE INDEX idx_knowledge_shadow_decisions_changed
    ON knowledge_shadow_decisions(changed, shadow_run_id DESC);

CREATE OR REPLACE FUNCTION reject_knowledge_shadow_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'knowledge shadow evidence is append-only';
END;
$$;

CREATE TRIGGER trg_knowledge_shadow_runs_no_update
BEFORE UPDATE OR DELETE ON knowledge_shadow_runs
FOR EACH ROW
EXECUTE FUNCTION reject_knowledge_shadow_mutation();

CREATE TRIGGER trg_knowledge_shadow_decisions_no_update
BEFORE UPDATE OR DELETE ON knowledge_shadow_decisions
FOR EACH ROW
EXECUTE FUNCTION reject_knowledge_shadow_mutation();
