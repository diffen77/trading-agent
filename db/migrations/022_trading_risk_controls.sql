-- Add an operator kill switch and an auditable daily entry-loss guard.

CREATE TABLE IF NOT EXISTS trading_controls (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE,
    status VARCHAR(10) NOT NULL DEFAULT 'ACTIVE',
    max_daily_loss_pct DECIMAL(5, 2) NOT NULL DEFAULT 3.00,
    reason TEXT,
    changed_by VARCHAR(100) NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_trading_controls_singleton CHECK (singleton),
    CONSTRAINT ck_trading_controls_status CHECK (
        status IN ('ACTIVE', 'HALTED')
    ),
    CONSTRAINT ck_trading_controls_daily_loss CHECK (
        max_daily_loss_pct > 0 AND max_daily_loss_pct <= 20
    ),
    CONSTRAINT ck_trading_controls_reason CHECK (
        reason IS NULL OR (
            LENGTH(BTRIM(reason)) BETWEEN 1 AND 500
        )
    ),
    CONSTRAINT ck_trading_controls_halt_reason CHECK (
        status != 'HALTED' OR reason IS NOT NULL
    ),
    CONSTRAINT ck_trading_controls_changed_by CHECK (
        LENGTH(BTRIM(changed_by)) BETWEEN 1 AND 100
    )
);

INSERT INTO trading_controls (
    singleton,
    status,
    max_daily_loss_pct,
    reason,
    changed_by
)
VALUES (
    TRUE,
    'ACTIVE',
    3.00,
    NULL,
    'operator-bootstrap'
)
ON CONFLICT (singleton) DO NOTHING;

CREATE TABLE IF NOT EXISTS trading_control_events (
    id BIGSERIAL PRIMARY KEY,
    event_type VARCHAR(20) NOT NULL,
    previous_status VARCHAR(10),
    new_status VARCHAR(10) NOT NULL,
    previous_max_daily_loss_pct DECIMAL(5, 2),
    new_max_daily_loss_pct DECIMAL(5, 2) NOT NULL,
    reason TEXT NOT NULL,
    changed_by VARCHAR(100) NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_trading_control_events_type CHECK (
        event_type IN (
            'BOOTSTRAP',
            'HALT',
            'RESUME',
            'LIMIT_CHANGE',
            'CONTROL_UPDATE'
        )
    ),
    CONSTRAINT ck_trading_control_events_previous_status CHECK (
        previous_status IS NULL
        OR previous_status IN ('ACTIVE', 'HALTED')
    ),
    CONSTRAINT ck_trading_control_events_new_status CHECK (
        new_status IN ('ACTIVE', 'HALTED')
    ),
    CONSTRAINT ck_trading_control_events_previous_limit CHECK (
        previous_max_daily_loss_pct IS NULL
        OR (
            previous_max_daily_loss_pct > 0
            AND previous_max_daily_loss_pct <= 20
        )
    ),
    CONSTRAINT ck_trading_control_events_new_limit CHECK (
        new_max_daily_loss_pct > 0
        AND new_max_daily_loss_pct <= 20
    ),
    CONSTRAINT ck_trading_control_events_reason CHECK (
        LENGTH(BTRIM(reason)) BETWEEN 1 AND 500
    ),
    CONSTRAINT ck_trading_control_events_changed_by CHECK (
        LENGTH(BTRIM(changed_by)) BETWEEN 1 AND 100
    )
);

CREATE INDEX IF NOT EXISTS idx_trading_control_events_changed_at
    ON trading_control_events(changed_at DESC, id DESC);

INSERT INTO trading_control_events (
    event_type,
    previous_status,
    new_status,
    previous_max_daily_loss_pct,
    new_max_daily_loss_pct,
    reason,
    changed_by
)
SELECT
    'BOOTSTRAP',
    NULL,
    control.status,
    NULL,
    control.max_daily_loss_pct,
    'Initial trading risk control',
    control.changed_by
FROM trading_controls control
WHERE control.singleton = TRUE
  AND NOT EXISTS (
      SELECT 1
      FROM trading_control_events event
      WHERE event.event_type = 'BOOTSTRAP'
  );

CREATE TABLE IF NOT EXISTS trading_daily_risk (
    session_date DATE PRIMARY KEY,
    opening_equity DECIMAL(20, 8) NOT NULL,
    latest_equity DECIMAL(20, 8) NOT NULL,
    daily_return_pct DECIMAL(12, 8) NOT NULL,
    limit_breached BOOLEAN NOT NULL DEFAULT FALSE,
    first_evaluated_at TIMESTAMPTZ NOT NULL,
    last_evaluated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_trading_daily_risk_opening CHECK (
        opening_equity > 0
    ),
    CONSTRAINT ck_trading_daily_risk_latest CHECK (
        latest_equity >= 0
    ),
    CONSTRAINT ck_trading_daily_risk_return CHECK (
        daily_return_pct >= -100
    ),
    CONSTRAINT ck_trading_daily_risk_times CHECK (
        last_evaluated_at >= first_evaluated_at
    )
);

CREATE TABLE IF NOT EXISTS trading_risk_evaluations (
    id BIGSERIAL PRIMARY KEY,
    session_date DATE NOT NULL
        REFERENCES trading_daily_risk(session_date) ON DELETE RESTRICT,
    trading_status VARCHAR(10) NOT NULL,
    opening_equity DECIMAL(20, 8) NOT NULL,
    current_equity DECIMAL(20, 8) NOT NULL,
    daily_return_pct DECIMAL(12, 8) NOT NULL,
    max_daily_loss_pct DECIMAL(5, 2) NOT NULL,
    allowed BOOLEAN NOT NULL,
    reason VARCHAR(30) NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_trading_risk_evaluations_status CHECK (
        trading_status IN ('ACTIVE', 'HALTED')
    ),
    CONSTRAINT ck_trading_risk_evaluations_opening CHECK (
        opening_equity > 0
    ),
    CONSTRAINT ck_trading_risk_evaluations_current CHECK (
        current_equity >= 0
    ),
    CONSTRAINT ck_trading_risk_evaluations_return CHECK (
        daily_return_pct >= -100
    ),
    CONSTRAINT ck_trading_risk_evaluations_limit CHECK (
        max_daily_loss_pct > 0 AND max_daily_loss_pct <= 20
    ),
    CONSTRAINT ck_trading_risk_evaluations_reason CHECK (
        reason IN (
            'ENTRY_ALLOWED',
            'TRADING_HALTED',
            'DAILY_LOSS_LIMIT'
        )
    ),
    CONSTRAINT ck_trading_risk_evaluations_result CHECK (
        (allowed AND reason = 'ENTRY_ALLOWED')
        OR (
            NOT allowed
            AND reason IN ('TRADING_HALTED', 'DAILY_LOSS_LIMIT')
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_trading_risk_evaluations_session
    ON trading_risk_evaluations(
        session_date,
        evaluated_at DESC,
        id DESC
    );

CREATE OR REPLACE FUNCTION prevent_trading_risk_audit_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;

DROP TRIGGER IF EXISTS trading_control_events_append_only
    ON trading_control_events;
CREATE TRIGGER trading_control_events_append_only
BEFORE UPDATE OR DELETE ON trading_control_events
FOR EACH ROW
EXECUTE FUNCTION prevent_trading_risk_audit_mutation();

DROP TRIGGER IF EXISTS trading_risk_evaluations_append_only
    ON trading_risk_evaluations;
CREATE TRIGGER trading_risk_evaluations_append_only
BEFORE UPDATE OR DELETE ON trading_risk_evaluations
FOR EACH ROW
EXECUTE FUNCTION prevent_trading_risk_audit_mutation();
