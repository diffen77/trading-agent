-- Append-only machine-readable evidence from model action to paper fill.

CREATE TABLE decision_funnel_events (
    id BIGSERIAL PRIMARY KEY,
    event_key CHAR(64) NOT NULL UNIQUE,
    ai_decision_id BIGINT NOT NULL
        REFERENCES ai_decisions(id) ON DELETE RESTRICT,
    cycle_key VARCHAR(160),
    stage VARCHAR(30) NOT NULL,
    ticker VARCHAR(20),
    action VARCHAR(10),
    reason_code VARCHAR(64) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_decision_funnel_identity CHECK (
        event_key ~ '^[0-9a-f]{64}$'
        AND (cycle_key IS NULL OR cycle_key ~ '^[a-z0-9][a-z0-9:._-]{2,159}$')
        AND (ticker IS NULL OR ticker ~ '^[A-Z0-9][A-Z0-9.-]{0,19}$')
        AND (action IS NULL OR action IN ('BUY', 'SELL', 'HOLD'))
        AND reason_code ~ '^[A-Z][A-Z0-9_]{1,63}$'
    ),
    CONSTRAINT ck_decision_funnel_stage CHECK (
        stage IN (
            'MODEL_ACTION',
            'VALIDATION_ACCEPTED',
            'VALIDATION_REJECTED',
            'VALIDATION_REVOKED',
            'ORDER_ATTEMPT',
            'ORDER_FILLED',
            'ORDER_REJECTED'
        )
    )
);

CREATE INDEX idx_decision_funnel_decision_stage
    ON decision_funnel_events(ai_decision_id, stage, id);

CREATE INDEX idx_decision_funnel_time
    ON decision_funnel_events(occurred_at DESC, id DESC);

CREATE OR REPLACE FUNCTION reject_decision_funnel_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'decision funnel evidence is append-only';
END;
$$;

CREATE TRIGGER trg_decision_funnel_no_update
BEFORE UPDATE OR DELETE ON decision_funnel_events
FOR EACH ROW
EXECUTE FUNCTION reject_decision_funnel_mutation();
