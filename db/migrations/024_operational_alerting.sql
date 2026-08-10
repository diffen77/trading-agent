-- Persist scheduled-run outcomes and deduplicated alert state transitions.

CREATE TABLE IF NOT EXISTS scheduled_routine_events (
    id BIGSERIAL PRIMARY KEY,
    routine_key VARCHAR(64) NOT NULL,
    routine_name VARCHAR(20) NOT NULL,
    scheduled_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(10) NOT NULL,
    failure_code VARCHAR(64),
    observed_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_scheduled_routine_key CHECK (
        routine_key ~
        '^[0-9]{4}-[0-9]{2}-[0-9]{2}:(morning|open|midday|close|evening)$'
    ),
    CONSTRAINT ck_scheduled_routine_name CHECK (
        routine_name IN ('morning', 'open', 'midday', 'close', 'evening')
    ),
    CONSTRAINT ck_scheduled_routine_status CHECK (
        status IN ('SUCCEEDED', 'FAILED')
    ),
    CONSTRAINT ck_scheduled_routine_failure CHECK (
        (
            status = 'SUCCEEDED'
            AND failure_code IS NULL
        )
        OR (
            status = 'FAILED'
            AND failure_code ~ '^[A-Z][A-Z0-9_]{2,63}$'
        )
    ),
    CONSTRAINT ck_scheduled_routine_times CHECK (
        observed_at >= scheduled_at
    )
);

CREATE INDEX IF NOT EXISTS idx_scheduled_routine_success
    ON scheduled_routine_events(routine_key, observed_at DESC, id DESC)
    WHERE status = 'SUCCEEDED';

CREATE TABLE IF NOT EXISTS operational_alert_events (
    id BIGSERIAL PRIMARY KEY,
    alert_key VARCHAR(100) NOT NULL,
    code VARCHAR(64) NOT NULL,
    severity VARCHAR(10) NOT NULL,
    state VARCHAR(10) NOT NULL,
    summary VARCHAR(240) NOT NULL,
    runbook VARCHAR(200) NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_operational_alert_key CHECK (
        alert_key ~ '^[a-z0-9][a-z0-9:._-]{1,99}$'
    ),
    CONSTRAINT ck_operational_alert_code CHECK (
        code ~ '^[A-Z][A-Z0-9_]{2,63}$'
    ),
    CONSTRAINT ck_operational_alert_severity CHECK (
        severity IN ('PAGE', 'TICKET')
    ),
    CONSTRAINT ck_operational_alert_state CHECK (
        state IN ('OPEN', 'RESOLVED')
    ),
    CONSTRAINT ck_operational_alert_summary CHECK (
        LENGTH(BTRIM(summary)) BETWEEN 1 AND 240
    ),
    CONSTRAINT ck_operational_alert_runbook CHECK (
        runbook = 'docs/operations.md'
    )
);

CREATE INDEX IF NOT EXISTS idx_operational_alert_latest
    ON operational_alert_events(alert_key, observed_at DESC, id DESC);

CREATE OR REPLACE FUNCTION reject_operational_evidence_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;

DROP TRIGGER IF EXISTS scheduled_routine_events_append_only
    ON scheduled_routine_events;
CREATE TRIGGER scheduled_routine_events_append_only
BEFORE UPDATE OR DELETE ON scheduled_routine_events
FOR EACH ROW
EXECUTE FUNCTION reject_operational_evidence_mutation();

DROP TRIGGER IF EXISTS operational_alert_events_append_only
    ON operational_alert_events;
CREATE TRIGGER operational_alert_events_append_only
BEFORE UPDATE OR DELETE ON operational_alert_events
FOR EACH ROW
EXECUTE FUNCTION reject_operational_evidence_mutation();
