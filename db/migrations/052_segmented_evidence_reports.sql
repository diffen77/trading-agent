-- Regime-segmented evidence, deterministic baselines and automated reports.

CREATE VIEW candidate_outcome_segments AS
WITH enriched AS (
    SELECT
        prediction.*,
        AVG((prediction.feature_json->>'momentum_60m_pct')::NUMERIC)
            OVER (PARTITION BY prediction.ai_decision_id) AS universe_breadth_60m,
        company.sector
    FROM candidate_predictions prediction
    LEFT JOIN companies company ON company.ticker = prediction.ticker
)
SELECT
    enriched.policy_version,
    enriched.exploration,
    enriched.model_action,
    outcome.horizon_minutes,
    CASE
        WHEN enriched.universe_breadth_60m >= 0.5 THEN 'RISK_ON'
        WHEN enriched.universe_breadth_60m <= -0.5 THEN 'RISK_OFF'
        ELSE 'NEUTRAL'
    END AS market_regime,
    CASE
        WHEN (enriched.feature_json->>'spread_bps')::NUMERIC <= 10 THEN 'TIGHT'
        WHEN (enriched.feature_json->>'spread_bps')::NUMERIC <= 50 THEN 'NORMAL'
        ELSE 'WIDE'
    END AS spread_bucket,
    CASE
        WHEN LEAST(
            (enriched.feature_json->>'bid_quantity')::NUMERIC,
            (enriched.feature_json->>'ask_quantity')::NUMERIC
        ) >= 1000 THEN 'HIGH'
        WHEN LEAST(
            (enriched.feature_json->>'bid_quantity')::NUMERIC,
            (enriched.feature_json->>'ask_quantity')::NUMERIC
        ) >= 100 THEN 'MEDIUM'
        ELSE 'LOW'
    END AS liquidity_bucket,
    COALESCE(enriched.sector, 'Unclassified') AS sector,
    CASE
        WHEN EXTRACT(HOUR FROM enriched.observed_at AT TIME ZONE 'Europe/Stockholm') < 11 THEN 'OPEN'
        WHEN EXTRACT(HOUR FROM enriched.observed_at AT TIME ZONE 'Europe/Stockholm') < 15 THEN 'MIDDAY'
        ELSE 'CLOSE'
    END AS session_phase,
    COUNT(*)::INTEGER AS outcomes,
    AVG(outcome.return_bps) AS mean_return_bps,
    100.0 * AVG(CASE WHEN outcome.return_bps > 0 THEN 1.0 ELSE 0.0 END)
        AS positive_rate_pct
FROM enriched
JOIN candidate_prediction_outcomes outcome
  ON outcome.prediction_id = enriched.id
GROUP BY
    enriched.policy_version,
    enriched.exploration,
    enriched.model_action,
    outcome.horizon_minutes,
    market_regime,
    spread_bucket,
    liquidity_bucket,
    COALESCE(enriched.sector, 'Unclassified'),
    session_phase;

CREATE VIEW deterministic_baseline_evidence AS
WITH ranked AS (
    SELECT
        prediction.*,
        outcome.horizon_minutes,
        outcome.return_bps,
        ROW_NUMBER() OVER (
            PARTITION BY prediction.ai_decision_id, outcome.horizon_minutes
            ORDER BY prediction.signal_score DESC, prediction.ticker
        ) AS score_rank
    FROM candidate_predictions prediction
    JOIN candidate_prediction_outcomes outcome
      ON outcome.prediction_id = prediction.id
), evidence AS (
    SELECT policy_version, horizon_minutes, 'CASH'::TEXT AS baseline_name,
           0::NUMERIC AS return_bps
    FROM ranked
    UNION ALL
    SELECT policy_version, horizon_minutes, 'TOP_SIGNAL', return_bps
    FROM ranked WHERE score_rank = 1
    UNION ALL
    SELECT policy_version, horizon_minutes, 'MODEL_BUY', return_bps
    FROM ranked WHERE model_action = 'BUY'
)
SELECT
    policy_version,
    horizon_minutes,
    baseline_name,
    COUNT(*)::INTEGER AS observations,
    AVG(return_bps) AS mean_return_bps,
    100.0 * AVG(CASE WHEN return_bps > 0 THEN 1.0 ELSE 0.0 END)
        AS positive_rate_pct
FROM evidence
GROUP BY policy_version, horizon_minutes, baseline_name;

CREATE VIEW rotation_quality_evidence AS
SELECT
    sell_trade.decision_id,
    sell_trade.id AS sell_trade_id,
    buy_trade.id AS buy_trade_id,
    sell_trade.ticker AS sold_ticker,
    buy_trade.ticker AS bought_ticker,
    sell_trade.executed_at AS sold_at,
    buy_trade.executed_at AS bought_at,
    sell_trade.pnl AS realized_sell_pnl_sek,
    outcome.return_bps AS replacement_return_30m_bps,
    CASE
        WHEN outcome.id IS NULL THEN 'PENDING'
        WHEN outcome.return_bps > 0 THEN 'POSITIVE'
        ELSE 'NEGATIVE'
    END AS rotation_quality
FROM trades sell_trade
JOIN trades buy_trade
  ON buy_trade.decision_id = sell_trade.decision_id
 AND buy_trade.action = 'BUY'
 AND sell_trade.action = 'SELL'
 AND buy_trade.id != sell_trade.id
LEFT JOIN candidate_predictions prediction
  ON prediction.ai_decision_id = buy_trade.decision_id
 AND prediction.ticker = buy_trade.ticker
LEFT JOIN candidate_prediction_outcomes outcome
  ON outcome.prediction_id = prediction.id
 AND outcome.horizon_minutes = 30;

CREATE TABLE position_opportunity_observations (
    id BIGSERIAL PRIMARY KEY,
    observation_key CHAR(64) NOT NULL UNIQUE,
    observed_at TIMESTAMPTZ NOT NULL,
    ticker VARCHAR(20) NOT NULL REFERENCES companies(ticker) ON DELETE RESTRICT,
    position_return_bps NUMERIC(18, 8) NOT NULL,
    best_available_ticker VARCHAR(20) REFERENCES companies(ticker) ON DELETE RESTRICT,
    best_available_return_30m_bps NUMERIC(18, 8),
    opportunity_cost_bps NUMERIC(18, 8),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_position_opportunity_key CHECK (observation_key ~ '^[0-9a-f]{64}$')
);

CREATE TABLE agent_evidence_reports (
    id BIGSERIAL PRIMARY KEY,
    report_key VARCHAR(100) NOT NULL UNIQUE,
    period VARCHAR(10) NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) NOT NULL,
    report JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_agent_report_identity CHECK (
        report_key ~ '^[a-z0-9][a-z0-9:._-]{2,99}$'
        AND period IN ('DAILY', 'WEEKLY')
        AND status IN ('COMPLETE', 'PARTIAL')
        AND period_start <= period_end
        AND JSONB_TYPEOF(report) = 'object'
    )
);

CREATE INDEX idx_agent_evidence_reports_latest
    ON agent_evidence_reports(generated_at DESC, id DESC);

CREATE OR REPLACE FUNCTION record_agent_evidence_report(
    requested_period VARCHAR,
    report_at TIMESTAMPTZ
) RETURNS BIGINT
LANGUAGE plpgsql
AS $$
DECLARE
    start_date DATE;
    end_date DATE;
    result_id BIGINT;
    report_payload JSONB;
BEGIN
    IF requested_period NOT IN ('DAILY', 'WEEKLY') THEN
        RAISE EXCEPTION 'unsupported evidence report period';
    END IF;
    end_date := (report_at AT TIME ZONE 'Europe/Stockholm')::DATE;
    start_date := CASE
        WHEN requested_period = 'DAILY' THEN end_date
        ELSE end_date - 6
    END;

    INSERT INTO position_opportunity_observations (
        observation_key,
        observed_at,
        ticker,
        position_return_bps,
        best_available_ticker,
        best_available_return_30m_bps,
        opportunity_cost_bps
    )
    SELECT
        ENCODE(SHA256(CONVERT_TO(
            report_at::TEXT || ':' || position.ticker,
            'UTF8'
        )), 'hex'),
        report_at,
        position.ticker,
        held.return_bps,
        alternative.ticker,
        alternative.return_bps,
        alternative.return_bps - held.return_bps
    FROM portfolio position
    JOIN LATERAL (
        SELECT outcome.return_bps
        FROM candidate_predictions prediction
        JOIN candidate_prediction_outcomes outcome
          ON outcome.prediction_id = prediction.id
         AND outcome.horizon_minutes = 30
        WHERE prediction.ticker = position.ticker
          AND outcome.evaluated_at <= report_at
        ORDER BY outcome.target_event_time DESC, outcome.id DESC
        LIMIT 1
    ) held ON TRUE
    LEFT JOIN LATERAL (
        SELECT prediction.ticker, outcome.return_bps
        FROM candidate_predictions prediction
        JOIN candidate_prediction_outcomes outcome
          ON outcome.prediction_id = prediction.id
         AND outcome.horizon_minutes = 30
        WHERE prediction.ticker != position.ticker
          AND prediction.eligible
          AND outcome.evaluated_at <= report_at
        ORDER BY outcome.return_bps DESC, prediction.ticker
        LIMIT 1
    ) alternative ON TRUE
    WHERE position.shares > 0
    ON CONFLICT (observation_key) DO NOTHING;

    WITH windowed_decisions AS (
        SELECT id FROM ai_decisions
        WHERE (timestamp AT TIME ZONE 'Europe/Stockholm')::DATE
              BETWEEN start_date AND end_date
    ), funnel AS (
        SELECT stage, reason_code, COUNT(*)::INTEGER AS count
        FROM decision_funnel_events
        WHERE ai_decision_id IN (SELECT id FROM windowed_decisions)
        GROUP BY stage, reason_code
    ), trade_summary AS (
        SELECT
            COUNT(*)::INTEGER AS trades,
            COUNT(*) FILTER (WHERE action = 'BUY')::INTEGER AS buys,
            COUNT(*) FILTER (WHERE action = 'SELL')::INTEGER AS sells,
            COALESCE(SUM(pnl), 0) AS realized_pnl_sek
        FROM trades
        WHERE (executed_at AT TIME ZONE 'Europe/Stockholm')::DATE
              BETWEEN start_date AND end_date
    ), outcome_summary AS (
        SELECT COUNT(*)::INTEGER AS new_outcomes
        FROM candidate_prediction_outcomes
        WHERE (evaluated_at AT TIME ZONE 'Europe/Stockholm')::DATE
              BETWEEN start_date AND end_date
    )
    SELECT JSONB_BUILD_OBJECT(
        'period', requested_period,
        'period_start', start_date,
        'period_end', end_date,
        'decisions', (SELECT COUNT(*) FROM windowed_decisions),
        'funnel', COALESCE((SELECT JSONB_AGG(JSONB_BUILD_OBJECT(
            'stage', stage, 'reason_code', reason_code, 'count', count
        ) ORDER BY stage, reason_code) FROM funnel), '[]'::JSONB),
        'trades', (SELECT ROW_TO_JSON(trade_summary) FROM trade_summary),
        'new_outcomes', (SELECT new_outcomes FROM outcome_summary),
        'segments', COALESCE((SELECT JSONB_AGG(ROW_TO_JSON(segment))
            FROM (
                SELECT * FROM candidate_outcome_segments
                ORDER BY outcomes DESC, policy_version, horizon_minutes
                LIMIT 50
            ) segment), '[]'::JSONB),
        'baselines', COALESCE((SELECT JSONB_AGG(ROW_TO_JSON(baseline))
            FROM deterministic_baseline_evidence baseline), '[]'::JSONB),
        'rotations', COALESCE((SELECT JSONB_AGG(ROW_TO_JSON(rotation))
            FROM rotation_quality_evidence rotation
            WHERE (rotation.sold_at AT TIME ZONE 'Europe/Stockholm')::DATE
                  BETWEEN start_date AND end_date), '[]'::JSONB),
        'opportunity_cost', COALESCE((SELECT JSONB_AGG(ROW_TO_JSON(opportunity))
            FROM position_opportunity_observations opportunity
            WHERE (opportunity.observed_at AT TIME ZONE 'Europe/Stockholm')::DATE
                  BETWEEN start_date AND end_date), '[]'::JSONB)
    ) INTO report_payload;

    INSERT INTO agent_evidence_reports (
        report_key, period, period_start, period_end, generated_at, status, report
    ) VALUES (
        LOWER(requested_period) || ':' || end_date::TEXT,
        requested_period,
        start_date,
        end_date,
        report_at,
        CASE WHEN (report_payload->>'decisions')::INTEGER > 0
             THEN 'COMPLETE' ELSE 'PARTIAL' END,
        report_payload
    )
    ON CONFLICT (report_key) DO UPDATE SET
        generated_at = EXCLUDED.generated_at,
        status = EXCLUDED.status,
        report = EXCLUDED.report
    RETURNING id INTO result_id;
    RETURN result_id;
END;
$$;
