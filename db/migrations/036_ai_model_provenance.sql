-- Persist the exact model contract for every immutable AI decision.

ALTER TABLE ai_decisions
    ADD COLUMN IF NOT EXISTS model_backend VARCHAR(30)
        NOT NULL DEFAULT 'legacy',
    ADD COLUMN IF NOT EXISTS model_name VARCHAR(200)
        NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS model_provider VARCHAR(80),
    ADD COLUMN IF NOT EXISTS reasoning_effort VARCHAR(10),
    ADD COLUMN IF NOT EXISTS response_model VARCHAR(200),
    ADD COLUMN IF NOT EXISTS response_id VARCHAR(200);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_ai_decisions_model_backend'
    ) THEN
        ALTER TABLE ai_decisions
            ADD CONSTRAINT ck_ai_decisions_model_backend CHECK (
                model_backend ~ '^[a-z0-9][a-z0-9._-]{1,29}$'
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_ai_decisions_model_name'
    ) THEN
        ALTER TABLE ai_decisions
            ADD CONSTRAINT ck_ai_decisions_model_name CHECK (
                LENGTH(BTRIM(model_name)) BETWEEN 1 AND 200
                AND model_name = BTRIM(model_name)
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_ai_decisions_reasoning_effort'
    ) THEN
        ALTER TABLE ai_decisions
            ADD CONSTRAINT ck_ai_decisions_reasoning_effort CHECK (
                reasoning_effort IS NULL
                OR reasoning_effort IN (
                    'none',
                    'low',
                    'medium',
                    'high',
                    'xhigh',
                    'max'
                )
            );
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_ai_decisions_model
    ON ai_decisions(model_backend, model_name, timestamp DESC);
