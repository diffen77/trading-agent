-- Link every AI-originated trade to its persisted parent decision.

ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS decision_id BIGINT
        REFERENCES ai_decisions(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS decision_origin VARCHAR(20)
        NOT NULL DEFAULT 'LEGACY';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_trades_decision_origin'
    ) THEN
        ALTER TABLE trades
            ADD CONSTRAINT ck_trades_decision_origin CHECK (
                decision_origin IN (
                    'AI_DECISION',
                    'AUTOMATED_SCAN',
                    'MECHANICAL_EXIT',
                    'MANUAL',
                    'LEGACY'
                )
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_trades_decision_link'
    ) THEN
        ALTER TABLE trades
            ADD CONSTRAINT ck_trades_decision_link CHECK (
                (
                    decision_origin = 'AI_DECISION'
                    AND decision_id IS NOT NULL
                )
                OR (
                    decision_origin != 'AI_DECISION'
                    AND decision_id IS NULL
                )
            );
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_trades_decision
    ON trades(decision_id, executed_at DESC)
    WHERE decision_id IS NOT NULL;

CREATE OR REPLACE FUNCTION enforce_trade_decision_audit()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    parent_strategy_version VARCHAR(50);
    parent_benchmark_experiment_id BIGINT;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF (
            NEW.decision_id IS DISTINCT FROM OLD.decision_id
            OR NEW.decision_origin IS DISTINCT FROM OLD.decision_origin
        ) THEN
            RAISE EXCEPTION
                'trade decision audit fields are immutable';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.decision_origin != 'AI_DECISION' THEN
        RETURN NEW;
    END IF;

    SELECT
        decision.strategy_version,
        decision.benchmark_experiment_id
    INTO
        parent_strategy_version,
        parent_benchmark_experiment_id
    FROM ai_decisions decision
    WHERE decision.id = NEW.decision_id;

    IF parent_strategy_version IS NULL THEN
        RAISE EXCEPTION
            'AI trade requires an existing versioned parent decision';
    END IF;
    IF parent_strategy_version != NEW.strategy_version THEN
        RAISE EXCEPTION
            'AI trade strategy does not match parent decision';
    END IF;
    IF parent_benchmark_experiment_id IS NOT NULL THEN
        IF NEW.benchmark_experiment_id IS NULL THEN
            NEW.benchmark_experiment_id =
                parent_benchmark_experiment_id;
        ELSIF (
            NEW.benchmark_experiment_id
            != parent_benchmark_experiment_id
        ) THEN
            RAISE EXCEPTION
                'AI trade benchmark does not match parent decision';
        END IF;
    ELSIF NEW.benchmark_experiment_id IS NOT NULL THEN
        RAISE EXCEPTION
            'benchmark trade cannot use a pre-benchmark AI decision';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_enforce_trade_decision_audit ON trades;
CREATE TRIGGER trg_enforce_trade_decision_audit
BEFORE INSERT OR UPDATE ON trades
FOR EACH ROW
EXECUTE FUNCTION enforce_trade_decision_audit();

CREATE OR REPLACE FUNCTION reject_ai_decision_audit_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'AI decision audit is append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_ai_decisions_append_only ON ai_decisions;
CREATE TRIGGER trg_ai_decisions_append_only
BEFORE UPDATE OR DELETE ON ai_decisions
FOR EACH ROW
EXECUTE FUNCTION reject_ai_decision_audit_mutation();
