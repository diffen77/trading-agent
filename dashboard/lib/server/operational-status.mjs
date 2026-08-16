import { buildActivationActions } from './activation-actions.mjs'


export const REQUIRED_SCHEMA_VERSION = 52


const OPERATIONAL_STATUS_QUERY = `
WITH
schema_state AS (
  SELECT COALESCE(MAX(version), 0)::int AS schema_version
  FROM schema_migrations
),
latest_reference AS (
  SELECT id, snapshot_date, instrument_count, universe_checksum_sha256
  FROM reference_data_snapshots
  WHERE mic = 'XSTO'
    AND provider = 'esma-firds'
  ORDER BY snapshot_date DESC, id DESC
  LIMIT 1
),
quote_contract AS (
  SELECT
    contract.id,
    contract.provider,
    contract.product_name,
    contract.data_type,
    contract.delivery_mode,
    contract.transport,
    contract.authorization_basis,
    contract.nominal_delay_seconds,
    contract.max_transport_lag_seconds,
    contract.status AS contract_status,
    validation.status AS validation_status,
    validation.acceptance_session_count,
    validation.expected_instruments,
    validation.product_covered_instruments,
    validation.symbol_mapped_instruments,
    (
      contract.status = 'VALIDATED'
      AND contract.governance_evidence_status = 'VERIFIED'
      AND contract.terms_checksum_sha256 IS NOT NULL
      AND contract.derived_storage_allowed
      AND contract.retention_policy IS NOT NULL
      AND contract.proposed_by IS NOT NULL
      AND contract.reviewed_by IS NOT NULL
      AND contract.usage_scope = 'INTERNAL_ANALYSIS_AND_PAPER'
      AND contract.external_distribution = FALSE
      AND (NOW() AT TIME ZONE 'Europe/Stockholm')::date
        BETWEEN contract.valid_from AND contract.valid_until
      AND contract.transport_verified_at IS NOT NULL
      AND (
        (
          contract.authorization_basis = 'NEGOTIATED_CONTRACT'
          AND contract.raw_storage_allowed
          AND contract.legal_reviewed_at IS NOT NULL
        )
        OR (
          contract.authorization_basis = 'PUBLIC_NONCOMMERCIAL_TERMS'
          AND contract.provider = 'nasdaq-nordic'
          AND contract.data_type = 'delayed-pre-trade-equity'
          AND contract.transport = 'PUBLIC_CSV'
          AND contract.raw_storage_allowed = FALSE
          AND contract.policy_verified_at IS NOT NULL
          AND contract.policy_verified_by IS NOT NULL
        )
      )
    ) AS contract_ready,
    (
      validation.status = 'PASSED'
      AND validation.governance_evidence_status = 'VERIFIED'
      AND validation.valid_until >= NOW()
      AND validation.valid_until::date <= contract.valid_until
      AND validation.reference_snapshot_id = (
        SELECT id FROM latest_reference
      )
      AND validation.reference_checksum_sha256 = (
        SELECT universe_checksum_sha256 FROM latest_reference
      )
      AND validation.acceptance_session_count >= CASE
        WHEN contract.authorization_basis = 'PUBLIC_NONCOMMERCIAL_TERMS'
        THEN 1
        ELSE 5
      END
      AND validation.validation_basis = CASE
        WHEN contract.authorization_basis = 'PUBLIC_NONCOMMERCIAL_TERMS'
        THEN 'CONTINUOUS_FILE_VALIDATION'
        ELSE 'FIVE_SESSION_ACCEPTANCE'
      END
      AND validation.first_session_date IS NOT NULL
      AND validation.last_session_date
        <= (NOW() AT TIME ZONE 'Europe/Stockholm')::date
      AND validation.acceptance_checksum_sha256 IS NOT NULL
      AND validation.expected_instruments = COALESCE(
        (SELECT instrument_count FROM latest_reference),
        0
      )
      AND validation.product_covered_instruments = validation.expected_instruments
      AND validation.symbol_mapped_instruments = validation.expected_instruments
      AND validation.sample_file_count > 0
      AND validation.sample_quote_count > 0
      AND validation.max_observed_delivery_seconds <= (
        contract.nominal_delay_seconds
        + contract.max_transport_lag_seconds
      )
      AND validation.max_observed_delivery_seconds
        >= contract.nominal_delay_seconds
    ) AS validation_ready
  FROM market_data_provider_contracts contract
  LEFT JOIN LATERAL (
    SELECT candidate.*
    FROM market_data_provider_validations candidate
    WHERE candidate.contract_id = contract.id
    ORDER BY candidate.validated_at DESC, candidate.id DESC
    LIMIT 1
  ) validation ON TRUE
  WHERE contract.mic = 'XSTO'
    AND contract.data_type IN (
      'delayed-post-trade-equity',
      'delayed-pre-trade-equity',
      'realtime-equity-level-1'
    )
  ORDER BY
    CASE WHEN contract.status = 'VALIDATED' THEN 0 ELSE 1 END,
    contract.updated_at DESC,
    contract.id DESC
  LIMIT 1
),
current_alias_candidate AS (
  SELECT
    snapshot.id,
    snapshot.reference_snapshot_id,
    entitlement.contract_key,
    entitlement.product_name,
    entitlement.status,
    entitlement.valid_from,
    entitlement.valid_until,
    entitlement.raw_storage_allowed,
    entitlement.derived_storage_allowed,
    (
      entitlement.status = 'VALIDATED'
      AND entitlement.provider = snapshot.provider
      AND entitlement.mic = snapshot.mic
      AND entitlement.transport = 'SFTP'
      AND entitlement.usage_scope = 'INTERNAL_ANALYSIS_AND_PAPER'
      AND entitlement.external_distribution = FALSE
      AND entitlement.raw_storage_allowed = TRUE
      AND entitlement.derived_storage_allowed = TRUE
      AND (NOW() AT TIME ZONE 'Europe/Stockholm')::date
        BETWEEN entitlement.valid_from AND entitlement.valid_until
      AND entitlement.legal_reviewed_at IS NOT NULL
      AND entitlement.legal_reviewed_at <= NOW()
      AND entitlement.storage_reviewed_at IS NOT NULL
      AND entitlement.storage_reviewed_at <= NOW()
      AND entitlement.entitlement_verified_at IS NOT NULL
      AND entitlement.entitlement_verified_at <= NOW()
      AND entitlement.host_key_algorithm IS NOT NULL
      AND entitlement.host_key_fingerprint_sha256 IS NOT NULL
    ) AS entitlement_ready
  FROM instrument_alias_snapshot_heads head
  JOIN instrument_alias_snapshots snapshot
    ON snapshot.id = head.snapshot_id
  LEFT JOIN reference_data_entitlements entitlement
    ON entitlement.id = snapshot.entitlement_id
  WHERE head.mic = 'XSTO'
    AND head.provider = (SELECT provider FROM quote_contract)
    AND snapshot.provider = head.provider
    AND snapshot.mic = head.mic
  LIMIT 1
),
reference_entitlement_state AS (
  SELECT
    contract_key,
    product_name,
    status,
    valid_from,
    valid_until,
    raw_storage_allowed,
    derived_storage_allowed,
    entitlement_ready
  FROM current_alias_candidate
),
current_alias_snapshot AS (
  SELECT id, reference_snapshot_id
  FROM current_alias_candidate
  WHERE entitlement_ready
),
index_contract AS (
  SELECT
    contract.id,
    contract.provider,
    contract.product_name,
    contract.data_type,
    contract.delivery_mode,
    contract.nominal_delay_seconds,
    contract.max_transport_lag_seconds,
    contract.status AS contract_status,
    validation.status AS validation_status,
    (
      contract.status = 'VALIDATED'
      AND contract.governance_evidence_status = 'VERIFIED'
      AND contract.terms_checksum_sha256 IS NOT NULL
      AND contract.raw_storage_allowed
      AND contract.derived_storage_allowed
      AND contract.retention_policy IS NOT NULL
      AND contract.proposed_by IS NOT NULL
      AND contract.reviewed_by IS NOT NULL
      AND contract.usage_scope = 'INTERNAL_ANALYSIS_AND_PAPER'
      AND contract.external_distribution = FALSE
      AND (NOW() AT TIME ZONE 'Europe/Stockholm')::date
        BETWEEN contract.valid_from AND contract.valid_until
      AND contract.legal_reviewed_at IS NOT NULL
      AND contract.transport_verified_at IS NOT NULL
    ) AS contract_ready,
    (
      validation.status = 'PASSED'
      AND validation.governance_evidence_status = 'VERIFIED'
      AND validation.valid_until >= NOW()
      AND validation.valid_until::date <= contract.valid_until
      AND validation.reference_snapshot_id IS NULL
      AND validation.reference_checksum_sha256 IS NULL
      AND validation.acceptance_session_count >= 5
      AND validation.first_session_date IS NOT NULL
      AND validation.last_session_date
        <= (NOW() AT TIME ZONE 'Europe/Stockholm')::date
      AND validation.acceptance_checksum_sha256 IS NOT NULL
      AND validation.expected_instruments = 1
      AND validation.product_covered_instruments = 1
      AND validation.symbol_mapped_instruments = 1
      AND validation.sample_file_count > 0
      AND validation.sample_quote_count > 0
      AND validation.max_observed_delivery_seconds <= (
        contract.nominal_delay_seconds
        + contract.max_transport_lag_seconds
      )
      AND validation.max_observed_delivery_seconds
        >= contract.nominal_delay_seconds
    ) AS validation_ready
  FROM market_data_provider_contracts contract
  LEFT JOIN LATERAL (
    SELECT candidate.*
    FROM market_data_provider_validations candidate
    WHERE candidate.contract_id = contract.id
    ORDER BY candidate.validated_at DESC, candidate.id DESC
    LIMIT 1
  ) validation ON TRUE
  WHERE contract.mic = 'XSTO'
    AND contract.data_type IN (
      'delayed-index-level',
      'realtime-index-level'
    )
  ORDER BY
    CASE WHEN contract.status = 'VALIDATED' THEN 0 ELSE 1 END,
    contract.updated_at DESC,
    contract.id DESC
  LIMIT 1
),
universe_state AS (
  SELECT
    (SELECT snapshot_date FROM latest_reference) AS snapshot_date,
    COALESCE(
      (SELECT instrument_count FROM latest_reference),
      0
    )::int AS expected_instruments,
    COUNT(*) FILTER (
      WHERE instrument.mic = 'XSTO'
        AND instrument.status = 'ACTIVE'
    )::int AS active_instruments,
    COUNT(DISTINCT alias.instrument_id) FILTER (
      WHERE alias.snapshot_id = (
        SELECT id FROM current_alias_snapshot
      )
        AND (
          SELECT reference_snapshot_id
          FROM current_alias_snapshot
        ) = (SELECT id FROM latest_reference)
    )::int AS provider_aliases,
    COUNT(DISTINCT company.instrument_id) FILTER (
      WHERE company.instrument_id IS NOT NULL
        AND instrument.status = 'ACTIVE'
    )::int AS company_mappings
  FROM instruments instrument
  LEFT JOIN instrument_alias_snapshot_members alias
    ON alias.instrument_id = instrument.id
  LEFT JOIN companies company
    ON company.instrument_id = instrument.id
  WHERE instrument.mic = 'XSTO'
),
today_session AS (
  SELECT
    session_date,
    opens_at,
    closes_at,
    timezone_name,
    status,
    CASE
      WHEN status = 'CLOSED' THEN 'CLOSED'
      WHEN NOW() < opens_at THEN 'PREOPEN'
      WHEN NOW() > closes_at THEN 'CLOSED'
      ELSE 'OPEN'
    END AS phase
  FROM market_sessions
  WHERE mic = 'XSTO'
    AND session_date = (NOW() AT TIME ZONE 'Europe/Stockholm')::date
  LIMIT 1
),
previous_session AS (
  SELECT session.*
  FROM market_sessions session
  JOIN today_session current ON TRUE
  WHERE session.mic = 'XSTO'
    AND session.status IN ('OPEN', 'HALF_DAY')
    AND session.closes_at < current.opens_at
  ORDER BY session.closes_at DESC
  LIMIT 1
),
current_index_level AS (
  SELECT level.*
  FROM market_index_levels level
  JOIN index_contract contract
    ON contract.id = level.provider_contract_id
  JOIN today_session session ON TRUE
  WHERE level.symbol = 'OMXSGI'
    AND level.mic = 'XSTO'
    AND level.source LIKE contract.provider || '%'
    AND level.event_time BETWEEN session.opens_at AND NOW()
    AND level.received_at <= NOW()
  ORDER BY level.event_time DESC, level.id DESC
  LIMIT 1
),
previous_index_level AS (
  SELECT level.*
  FROM market_index_levels level
  JOIN index_contract contract
    ON contract.id = level.provider_contract_id
  JOIN previous_session session ON TRUE
  WHERE level.symbol = 'OMXSGI'
    AND level.mic = 'XSTO'
    AND level.source LIKE contract.provider || '%'
    AND level.event_time BETWEEN session.opens_at AND session.closes_at
    AND level.received_at <= NOW()
  ORDER BY level.event_time DESC, level.id DESC
  LIMIT 1
),
index_state AS (
  SELECT jsonb_build_object(
    'symbol', 'OMXSGI',
    'provider', contract.provider,
    'product_name', contract.product_name,
    'delivery_mode', contract.delivery_mode,
    'contract_status', contract.contract_status,
    'validation_status', contract.validation_status,
    'contract_ready', contract.contract_ready,
    'validation_ready', contract.validation_ready,
    'signal_fresh', (
      current_level.id IS NOT NULL
      AND previous_level.id IS NOT NULL
      AND current_level.event_time <= NOW()
      AND current_level.received_at <= NOW()
      AND EXTRACT(EPOCH FROM (NOW() - current_level.event_time)) <= (
        contract.nominal_delay_seconds
        + contract.max_transport_lag_seconds
      )
    ),
    'current_level', current_level.level,
    'previous_close', previous_level.level,
    'change_pct', (
      (current_level.level / NULLIF(previous_level.level, 0) - 1) * 100
    ),
    'event_time', current_level.event_time,
    'received_at', current_level.received_at
  ) AS index_signal
  FROM index_contract contract
  LEFT JOIN current_index_level current_level ON TRUE
  LEFT JOIN previous_index_level previous_level ON TRUE
),
latest_quotes AS (
  SELECT DISTINCT ON (quote.instrument_id)
    quote.instrument_id,
    quote.event_time,
    quote.received_at,
    quote.last_price
  FROM market_quotes quote
  JOIN instruments instrument
    ON instrument.id = quote.instrument_id
  JOIN market_data_files source_file
    ON source_file.id = quote.data_file_id
    AND source_file.provider_contract_id = (
      SELECT id FROM quote_contract
    )
  WHERE instrument.mic = 'XSTO'
    AND instrument.status = 'ACTIVE'
    AND COALESCE(
      (SELECT data_type FROM quote_contract),
      ''
    ) != 'delayed-pre-trade-equity'
    AND COALESCE(
      (SELECT contract_ready AND validation_ready FROM quote_contract),
      FALSE
    )
    AND quote.source LIKE COALESCE(
      (SELECT provider FROM quote_contract),
      '__missing_provider__'
    ) || '%'
  ORDER BY quote.instrument_id, quote.event_time DESC, quote.id DESC
),
latest_pretrade_batch AS (
  SELECT batch.id
  FROM pre_trade_batches batch
  JOIN pre_trade_stream_cursors seal
    ON seal.batch_id = batch.id
  WHERE batch.provider_contract_id = (
    SELECT id FROM quote_contract
  )
    AND COALESCE(
      (SELECT data_type FROM quote_contract),
      ''
    ) = 'delayed-pre-trade-equity'
    AND COALESCE(
      (SELECT contract_ready AND validation_ready FROM quote_contract),
      FALSE
    )
  ORDER BY batch.report_minute DESC, batch.id DESC
  LIMIT 1
),
latest_pretrade_books AS (
  SELECT
    state.instrument_id,
    GREATEST(state.bid_event_time, state.ask_event_time) AS event_time,
    state.received_at,
    (state.bid_price + state.ask_price) / 2 AS last_price
  FROM pre_trade_book_states state
  JOIN latest_pretrade_batch batch
    ON batch.id = state.batch_id
  WHERE state.bid_price IS NOT NULL
    AND state.ask_price IS NOT NULL
    AND state.bid_quantity > 0
    AND state.ask_quantity > 0
    AND state.trading_system = 'CLOB'
    AND state.trading_phase = 'COTR'
),
latest_authorized_marks AS (
  SELECT * FROM latest_quotes
  UNION ALL
  SELECT * FROM latest_pretrade_books
),
quote_state AS (
  SELECT
    COUNT(*)::int AS quoted_instruments,
    COUNT(*) FILTER (
      WHERE quote.event_time <= NOW()
        AND quote.received_at <= NOW()
        AND quote.event_time >= (SELECT opens_at FROM today_session)
        AND EXTRACT(EPOCH FROM (NOW() - quote.event_time)) <= COALESCE(
          (
            SELECT
              nominal_delay_seconds + max_transport_lag_seconds
            FROM quote_contract
          ),
          -1
        )
    )::int AS fresh_instruments,
    MAX(quote.event_time) AS latest_event_time,
    MAX(quote.received_at) AS latest_received_at
  FROM latest_authorized_marks quote
),
latest_sync AS (
  SELECT run.status, run.finished_at
  FROM data_sync_runs run
  WHERE run.provider = (SELECT provider FROM quote_contract)
    AND run.data_type = (SELECT data_type FROM quote_contract)
  ORDER BY run.started_at DESC, run.id DESC
  LIMIT 1
),
gap_state AS (
  SELECT COUNT(*)::int AS open_gaps
  FROM market_data_gaps gap
  WHERE gap.provider = (SELECT provider FROM quote_contract)
    AND gap.data_type = (SELECT data_type FROM quote_contract)
    AND gap.status = 'OPEN'
),
strategy_state AS (
  SELECT version, config_hash, config
  FROM strategy_versions
  WHERE status = 'ACTIVE'
  LIMIT 1
),
position_values AS (
  SELECT
    position.ticker,
    COALESCE(company.sector, 'Okänd') AS sector,
    position.shares,
    quote.last_price
  FROM portfolio position
  LEFT JOIN companies company
    ON company.ticker = position.ticker
  LEFT JOIN latest_authorized_marks quote
    ON quote.instrument_id = company.instrument_id
  WHERE position.shares > 0
),
risk_state AS (
  SELECT
    (SELECT COUNT(*) FROM position_values)::int AS open_positions,
    COALESCE((
      SELECT MAX(sector_positions)
      FROM (
        SELECT COUNT(*)::int AS sector_positions
        FROM position_values
        GROUP BY sector
      ) grouped_sectors
    ), 0)::int AS largest_sector_positions,
    (SELECT COUNT(*) FROM position_values WHERE last_price IS NULL)::int
      AS unpriced_positions,
    COALESCE((
      SELECT SUM(shares * last_price)
      FROM position_values
      WHERE last_price IS NOT NULL
    ), 0) AS gross_exposure,
    COALESCE((
      SELECT cash
      FROM balance
      ORDER BY id DESC
      LIMIT 1
    ), 0) AS cash,
    (
      SELECT status
      FROM trading_controls
      WHERE singleton = TRUE
    ) AS entry_status,
    (
      SELECT max_daily_loss_pct
      FROM trading_controls
      WHERE singleton = TRUE
    ) AS max_daily_loss_pct,
    COALESCE((
      SELECT limit_breached
      FROM trading_daily_risk
      WHERE session_date = (
        NOW() AT TIME ZONE 'Europe/Stockholm'
      )::date
    ), FALSE) AS daily_loss_breached,
    (
      SELECT daily_return_pct
      FROM trading_daily_risk
      WHERE session_date = (
        NOW() AT TIME ZONE 'Europe/Stockholm'
      )::date
    ) AS daily_return_pct
),
benchmark_base AS (
  SELECT experiment.*
  FROM paper_benchmark_experiments experiment
  ORDER BY
    CASE experiment.status
      WHEN 'RUNNING' THEN 0
      WHEN 'PAUSED' THEN 1
      WHEN 'APPROVED' THEN 2
      WHEN 'DRAFT' THEN 3
      WHEN 'COMPLETED' THEN 4
      ELSE 5
    END,
    experiment.id DESC
  LIMIT 1
),
benchmark_state AS (
  SELECT jsonb_build_object(
    'experiment_key', experiment.experiment_key,
    'status', experiment.status,
    'benchmark_symbol', experiment.benchmark_symbol,
    'model_backend', experiment.model_backend,
    'model_name', experiment.model_name,
    'min_trading_sessions', experiment.min_trading_sessions,
    'min_closed_trades', experiment.min_closed_trades,
    'trading_sessions', (
      SELECT COUNT(*)::int
      FROM paper_benchmark_daily_observations observation
      WHERE observation.experiment_id = experiment.id
    ),
    'closed_trades', (
      SELECT COUNT(*)::int
      FROM trades trade
      WHERE trade.benchmark_experiment_id = experiment.id
        AND trade.action = 'SELL'
        AND trade.closes_position
    ),
    'critical_incidents', (
      SELECT COUNT(*)::int
      FROM paper_benchmark_incidents incident
      WHERE incident.experiment_id = experiment.id
        AND incident.severity = 'CRITICAL'
    ),
    'net_return_pct', evaluation.net_return_pct,
    'benchmark_return_pct', evaluation.benchmark_return_pct,
    'excess_return_pct', evaluation.excess_return_pct,
    'max_drawdown_pct', evaluation.max_drawdown_pct,
    'data_coverage_pct', evaluation.data_coverage_pct,
    'passed', evaluation.passed
  ) AS benchmark
  FROM benchmark_base experiment
  LEFT JOIN paper_benchmark_evaluations evaluation
    ON evaluation.experiment_id = experiment.id
),
latest_operational_alerts AS (
  SELECT
    alert_key,
    code,
    severity,
    state,
    summary,
    runbook,
    observed_at
  FROM (
    SELECT DISTINCT ON (event.alert_key)
      event.id,
      event.alert_key,
      event.code,
      event.severity,
      event.state,
      event.summary,
      event.runbook,
      event.observed_at
    FROM operational_alert_events event
    ORDER BY event.alert_key, event.observed_at DESC, event.id DESC
  ) latest
  WHERE state = 'OPEN'
  ORDER BY
    CASE severity WHEN 'PAGE' THEN 0 ELSE 1 END,
    observed_at DESC,
    id DESC
  LIMIT 50
),
recent_scheduled_routines AS (
  SELECT
    routine_key,
    routine_name,
    scheduled_at,
    status,
    failure_code,
    observed_at
  FROM (
    SELECT DISTINCT ON (event.routine_key)
      event.id,
      event.routine_key,
      event.routine_name,
      event.scheduled_at,
      event.status,
      event.failure_code,
      event.observed_at
    FROM scheduled_routine_events event
    ORDER BY event.routine_key, event.observed_at DESC, event.id DESC
  ) latest
  ORDER BY scheduled_at DESC, observed_at DESC, id DESC
  LIMIT 10
),
activity_state AS (
  SELECT
    (NOW() AT TIME ZONE 'Europe/Stockholm')::date AS local_date,
    (
      SELECT COUNT(*)::int
      FROM ai_decisions decision
      WHERE (decision.timestamp AT TIME ZONE 'Europe/Stockholm')::date
        = (NOW() AT TIME ZONE 'Europe/Stockholm')::date
    ) AS decisions_today,
    (
      SELECT COUNT(*)::int
      FROM trades trade
      WHERE (trade.executed_at AT TIME ZONE 'Europe/Stockholm')::date
        = (NOW() AT TIME ZONE 'Europe/Stockholm')::date
    ) AS trades_today,
    (SELECT MAX(timestamp) FROM ai_decisions) AS latest_decision_at,
    (SELECT MAX(executed_at) FROM trades) AS latest_trade_at
),
pipeline_state AS (
  SELECT
    (SELECT status FROM continuous_learning_runs
      ORDER BY evaluated_at DESC, id DESC LIMIT 1) AS learning_status,
    (SELECT evaluated_at FROM continuous_learning_runs
      ORDER BY evaluated_at DESC, id DESC LIMIT 1) AS learning_evaluated_at,
    (SELECT error_code FROM continuous_learning_runs
      ORDER BY evaluated_at DESC, id DESC LIMIT 1) AS learning_error_code,
    (SELECT status FROM knowledge_graph_sync_runs
      ORDER BY synced_at DESC, id DESC LIMIT 1) AS knowledge_status,
    (SELECT synced_at FROM knowledge_graph_sync_runs
      ORDER BY synced_at DESC, id DESC LIMIT 1) AS knowledge_synced_at,
    (SELECT error_code FROM knowledge_graph_sync_runs
      ORDER BY synced_at DESC, id DESC LIMIT 1) AS knowledge_error_code,
    (SELECT backlog_counts FROM knowledge_graph_sync_runs
      ORDER BY synced_at DESC, id DESC LIMIT 1) AS knowledge_backlog_counts
),
decision_funnel_state AS (
  SELECT
    decision.id AS ai_decision_id,
    decision.timestamp AS decided_at,
    (SELECT COUNT(*)::int FROM candidate_predictions prediction
      WHERE prediction.ai_decision_id = decision.id) AS candidates_read,
    (SELECT COUNT(*)::int FROM candidate_predictions prediction
      WHERE prediction.ai_decision_id = decision.id
        AND NOT prediction.eligible
        AND NOT prediction.exploration) AS candidates_filtered,
    (SELECT COUNT(*)::int FROM candidate_predictions prediction
      WHERE prediction.ai_decision_id = decision.id) AS candidates_ranked,
    (SELECT COUNT(*)::int FROM candidate_predictions prediction
      WHERE prediction.ai_decision_id = decision.id
        AND (prediction.eligible OR prediction.exploration)) AS candidates_eligible,
    (SELECT COUNT(*)::int FROM candidate_predictions prediction
      WHERE prediction.ai_decision_id = decision.id
        AND prediction.exploration) AS candidates_exploration,
    (SELECT COUNT(*)::int FROM candidate_predictions prediction
      WHERE prediction.ai_decision_id = decision.id
        AND prediction.model_action != 'ABSTAIN') AS model_actions,
    (SELECT COUNT(*)::int FROM decision_funnel_events event
      WHERE event.ai_decision_id = decision.id
        AND event.stage = 'VALIDATION_ACCEPTED') AS validations_accepted,
    (SELECT COUNT(*)::int FROM decision_funnel_events event
      WHERE event.ai_decision_id = decision.id
        AND event.stage = 'VALIDATION_REJECTED') AS validations_rejected,
    (SELECT COUNT(*)::int FROM decision_funnel_events event
      WHERE event.ai_decision_id = decision.id
        AND event.stage = 'ORDER_ATTEMPT') AS order_attempts,
    (SELECT COUNT(*)::int FROM decision_funnel_events event
      WHERE event.ai_decision_id = decision.id
        AND event.stage = 'ORDER_REJECTED') AS order_rejections,
    (SELECT COUNT(*)::int FROM decision_funnel_events event
      WHERE event.ai_decision_id = decision.id
        AND event.stage = 'ORDER_FILLED') AS fills,
    COALESCE((
      SELECT jsonb_object_agg(reason_code, reason_count)
      FROM (
        SELECT event.reason_code, COUNT(*)::int AS reason_count
        FROM decision_funnel_events event
        WHERE event.ai_decision_id = decision.id
          AND event.stage IN ('VALIDATION_REJECTED', 'ORDER_REJECTED')
        GROUP BY event.reason_code
      ) reasons
    ), '{}'::jsonb) AS stopping_reasons
  FROM ai_decisions decision
  ORDER BY decision.timestamp DESC, decision.id DESC
  LIMIT 1
),
evidence_report_state AS (
  SELECT
    (SELECT report FROM agent_evidence_reports
      WHERE period = 'DAILY'
      ORDER BY generated_at DESC, id DESC LIMIT 1) AS daily_report,
    (SELECT generated_at FROM agent_evidence_reports
      WHERE period = 'DAILY'
      ORDER BY generated_at DESC, id DESC LIMIT 1) AS daily_generated_at,
    (SELECT report FROM agent_evidence_reports
      WHERE period = 'WEEKLY'
      ORDER BY generated_at DESC, id DESC LIMIT 1) AS weekly_report,
    (SELECT generated_at FROM agent_evidence_reports
      WHERE period = 'WEEKLY'
      ORDER BY generated_at DESC, id DESC LIMIT 1) AS weekly_generated_at
)
SELECT
  NOW() AS checked_at,
  (SELECT schema_version FROM schema_state) AS schema_version,
  (
    SELECT jsonb_build_object(
      'version', version,
      'config_hash', config_hash,
      'config', config
    )
    FROM strategy_state
  ) AS strategy,
  (
    SELECT jsonb_build_object(
      'snapshot_date', snapshot_date,
      'expected_instruments', expected_instruments,
      'active_instruments', active_instruments,
      'provider_aliases', provider_aliases,
      'company_mappings', company_mappings
    )
    FROM universe_state
  ) AS universe,
  (
    SELECT jsonb_build_object(
      'provider', provider,
      'product_name', product_name,
      'data_type', data_type,
      'delivery_mode', delivery_mode,
      'transport', transport,
      'authorization_basis', authorization_basis,
      'nominal_delay_seconds', nominal_delay_seconds,
      'max_transport_lag_seconds', max_transport_lag_seconds,
      'contract_status', contract_status,
      'validation_status', validation_status,
      'acceptance_session_count', acceptance_session_count,
      'contract_ready', contract_ready,
      'validation_ready', validation_ready
    )
    FROM quote_contract
  ) AS provider,
  (
    SELECT jsonb_build_object(
      'contract_key', contract_key,
      'product_name', product_name,
      'status', status,
      'valid_from', valid_from,
      'valid_until', valid_until,
      'raw_storage_allowed', raw_storage_allowed,
      'derived_storage_allowed', derived_storage_allowed,
      'entitlement_ready', entitlement_ready
    )
    FROM reference_entitlement_state
  ) AS reference_entitlement,
  COALESCE(
    (
      SELECT jsonb_build_object(
        'session_date', session_date,
        'phase', phase,
        'timezone_name', timezone_name,
        'opens_at', opens_at,
        'closes_at', closes_at
      )
      FROM today_session
    ),
    jsonb_build_object(
      'session_date', NULL,
      'phase', 'MISSING',
      'timezone_name', 'Europe/Stockholm',
      'opens_at', NULL,
      'closes_at', NULL
    )
  ) AS market,
  (
    SELECT jsonb_build_object(
      'quoted_instruments', quoted_instruments,
      'fresh_instruments', fresh_instruments,
      'latest_event_time', latest_event_time,
      'latest_received_at', latest_received_at
    )
    FROM quote_state
  ) AS quotes,
  jsonb_build_object(
    'status', (SELECT status FROM latest_sync),
    'finished_at', (SELECT finished_at FROM latest_sync),
    'open_gaps', (SELECT open_gaps FROM gap_state)
  ) AS sync,
  (SELECT index_signal FROM index_state) AS index,
  (
    SELECT jsonb_build_object(
      'open_positions', open_positions,
      'largest_sector_positions', largest_sector_positions,
      'unpriced_positions', unpriced_positions,
      'gross_exposure', gross_exposure,
      'cash', cash,
      'total_value', cash + gross_exposure,
      'entry_status', entry_status,
      'max_daily_loss_pct', max_daily_loss_pct,
      'daily_loss_breached', daily_loss_breached,
      'daily_return_pct', daily_return_pct
    )
    FROM risk_state
  ) AS risk,
  (SELECT benchmark FROM benchmark_state) AS benchmark,
  COALESCE(
    (
      SELECT jsonb_agg(
        jsonb_build_object(
          'alert_key', alert_key,
          'code', code,
          'severity', severity,
          'state', state,
          'summary', summary,
          'runbook', runbook,
          'observed_at', observed_at
        )
        ORDER BY
          CASE severity WHEN 'PAGE' THEN 0 ELSE 1 END,
          observed_at DESC
      )
      FROM latest_operational_alerts
    ),
    '[]'::jsonb
  ) AS operational_alerts,
  COALESCE(
    (
      SELECT jsonb_agg(
        jsonb_build_object(
          'routine_key', routine_key,
          'routine_name', routine_name,
          'scheduled_at', scheduled_at,
          'status', status,
          'failure_code', failure_code,
          'observed_at', observed_at
        )
        ORDER BY scheduled_at DESC, observed_at DESC
      )
      FROM recent_scheduled_routines
    ),
    '[]'::jsonb
  ) AS scheduled_routines,
  (
    SELECT jsonb_build_object(
      'local_date', local_date,
      'decisions_today', decisions_today,
      'trades_today', trades_today,
      'latest_decision_at', latest_decision_at,
      'latest_trade_at', latest_trade_at
    )
    FROM activity_state
  ) AS activity,
  (
    SELECT jsonb_build_object(
      'learning_status', learning_status,
      'learning_evaluated_at', learning_evaluated_at,
      'learning_error_code', learning_error_code,
      'knowledge_status', knowledge_status,
      'knowledge_synced_at', knowledge_synced_at,
      'knowledge_error_code', knowledge_error_code,
      'knowledge_backlog_counts', knowledge_backlog_counts
    )
    FROM pipeline_state
  ) AS pipelines,
  (
    SELECT jsonb_build_object(
      'ai_decision_id', ai_decision_id,
      'decided_at', decided_at,
      'candidates_read', candidates_read,
      'candidates_filtered', candidates_filtered,
      'candidates_ranked', candidates_ranked,
      'candidates_eligible', candidates_eligible,
      'candidates_exploration', candidates_exploration,
      'model_actions', model_actions,
      'validations_accepted', validations_accepted,
      'validations_rejected', validations_rejected,
      'order_attempts', order_attempts,
      'order_rejections', order_rejections,
      'fills', fills,
      'stopping_reasons', stopping_reasons
    )
    FROM decision_funnel_state
  ) AS funnel,
  (
    SELECT jsonb_build_object(
      'daily', daily_report,
      'daily_generated_at', daily_generated_at,
      'weekly', weekly_report,
      'weekly_generated_at', weekly_generated_at
    )
    FROM evidence_report_state
  ) AS evidence_reports
`

function numberOrZero(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number : 0
}

function releaseSha(value) {
  return typeof value === 'string' && /^[0-9a-f]{40}([0-9a-f]{24})?$/.test(value)
    ? value
    : null
}


function percentage(numerator, denominator) {
  if (denominator <= 0) return 0
  return Number(((numerator / denominator) * 100).toFixed(2))
}


export function buildOperationalStatus(snapshot) {
  const blockers = []
  const schemaVersion = numberOrZero(snapshot?.schema_version)
  const strategy = snapshot?.strategy ?? null
  const strategyConfig = strategy?.config ?? {}
  const universe = snapshot?.universe ?? {}
  const expectedInstruments = numberOrZero(
    universe.expected_instruments,
  )
  const activeInstruments = numberOrZero(universe.active_instruments)
  const providerAliases = numberOrZero(universe.provider_aliases)
  const companyMappings = numberOrZero(universe.company_mappings)
  const provider = snapshot?.provider ?? null
  const referenceEntitlement = snapshot?.reference_entitlement ?? null
  const market = snapshot?.market ?? { phase: 'MISSING' }
  const quotes = snapshot?.quotes ?? {}
  const sync = snapshot?.sync ?? {}
  const index = snapshot?.index ?? null
  const risk = snapshot?.risk ?? {}
  const benchmark = snapshot?.benchmark ?? null
  const operationalAlerts = (
    Array.isArray(snapshot?.operational_alerts)
      ? snapshot.operational_alerts
      : []
  ).filter(alert => (
    alert
    && typeof alert === 'object'
    && alert.state === 'OPEN'
  )).slice(0, 50)
  const scheduledRoutines = (
    Array.isArray(snapshot?.scheduled_routines)
      ? snapshot.scheduled_routines
      : []
  ).filter(routine => (
    routine
    && typeof routine === 'object'
  )).slice(0, 10)
  const activity = snapshot?.activity ?? {}
  const pipelines = snapshot?.pipelines ?? {}
  const funnel = snapshot?.funnel ?? {}
  const evidenceReports = snapshot?.evidence_reports ?? {}
  const runtimeReleaseSha = releaseSha(snapshot?.runtime_release_sha)
  const marketIsOpen = market.phase === 'OPEN'
  const publicPretrade = (
    provider?.data_type === 'delayed-pre-trade-equity'
    && provider?.authorization_basis === 'PUBLIC_NONCOMMERCIAL_TERMS'
  )

  if (schemaVersion !== REQUIRED_SCHEMA_VERSION) {
    blockers.push('SCHEMA_VERSION_MISMATCH')
  }
  if (!runtimeReleaseSha) {
    blockers.push('RELEASE_IDENTITY_UNAVAILABLE')
  }
  if (!strategy) {
    blockers.push('ACTIVE_STRATEGY_MISSING')
  }
  if (expectedInstruments <= 0) {
    blockers.push('REFERENCE_SNAPSHOT_MISSING')
  } else {
    if (activeInstruments !== expectedInstruments) {
      blockers.push('UNIVERSE_INCOMPLETE')
    }
    if (
      companyMappings !== expectedInstruments
      || (!publicPretrade && providerAliases !== expectedInstruments)
    ) {
      blockers.push('TICKER_MAPPING_INCOMPLETE')
    }
  }
  if (
    !publicPretrade
    && referenceEntitlement?.entitlement_ready !== true
  ) {
    blockers.push('REFERENCE_ENTITLEMENT_NOT_READY')
  }
  if (
    !provider
    || provider.contract_ready !== true
    || provider.validation_ready !== true
  ) {
    blockers.push('QUOTE_PROVIDER_NOT_READY')
  }
  if (!marketIsOpen) {
    blockers.push('XSTO_SESSION_NOT_OPEN')
  }

  const freshInstruments = numberOrZero(quotes.fresh_instruments)
  if (
    marketIsOpen
    && (
      expectedInstruments <= 0
      || (
        publicPretrade
          ? freshInstruments <= 0
          : freshInstruments !== expectedInstruments
      )
    )
  ) {
    blockers.push('FRESH_QUOTE_COVERAGE_INCOMPLETE')
  }
  if (
    marketIsOpen
    && provider?.contract_ready === true
    && sync.status !== 'SUCCEEDED'
  ) {
    blockers.push('MARKET_SYNC_NOT_READY')
  }
  if (numberOrZero(sync.open_gaps) > 0) {
    blockers.push('MARKET_DATA_GAPS_OPEN')
  }
  if (!publicPretrade) {
    if (
      !index
      || index.contract_ready !== true
      || index.validation_ready !== true
    ) {
      blockers.push('INDEX_PROVIDER_NOT_READY')
    } else if (marketIsOpen && index.signal_fresh !== true) {
      blockers.push('INDEX_SIGNAL_NOT_READY')
    }
  }

  const maxPositions = numberOrZero(strategyConfig.max_positions)
  const maxSectorPositions = numberOrZero(
    strategyConfig.max_sector_positions,
  )
  const openPositions = numberOrZero(risk.open_positions)
  const largestSectorPositions = numberOrZero(
    risk.largest_sector_positions,
  )
  const unpricedPositions = numberOrZero(risk.unpriced_positions)
  const entryStatus = risk.entry_status ?? 'MISSING'
  const dailyLossBreached = risk.daily_loss_breached === true
  if (maxPositions > 0 && openPositions > maxPositions) {
    blockers.push('POSITION_LIMIT_BREACH')
  }
  if (unpricedPositions > 0) {
    blockers.push('UNPRICED_POSITION')
  }
  if (entryStatus !== 'ACTIVE') {
    blockers.push('TRADING_HALTED')
  }
  if (dailyLossBreached) {
    blockers.push('DAILY_LOSS_LIMIT_BREACHED')
  }

  if (benchmark) {
    if (numberOrZero(benchmark.critical_incidents) > 0) {
      blockers.push('CRITICAL_BENCHMARK_INCIDENT')
    }
  }

  for (const alert of operationalAlerts) {
    const code = alert.code
    if (
      alert.severity === 'PAGE'
      && typeof code === 'string'
      && /^[A-Z][A-Z0-9_]{2,63}$/.test(code)
      && code !== 'SECTOR_LIMIT_BREACH'
      && !blockers.includes(code)
    ) {
      blockers.push(code)
    }
  }

  const mappingCoverage = publicPretrade
    ? percentage(companyMappings, expectedInstruments)
    : Math.min(
      percentage(providerAliases, expectedInstruments),
      percentage(companyMappings, expectedInstruments),
    )
  const freshQuoteCoverage = percentage(
    freshInstruments,
    expectedInstruments,
  )
  const riskHasBreach = blockers.some(code => (
    code === 'POSITION_LIMIT_BREACH'
    || code === 'UNPRICED_POSITION'
    || code === 'TRADING_HALTED'
    || code === 'DAILY_LOSS_LIMIT_BREACHED'
  ))

  const status = {
    checked_at: snapshot?.checked_at ?? null,
    source: {
      system_of_record: 'postgresql',
      checked_at: snapshot?.checked_at ?? null,
      read_status: snapshot?.checked_at ? 'CURRENT' : 'STALE',
      max_age_seconds: 120,
    },
    overall_status: blockers.length === 0 ? 'READY' : 'BLOCKED',
    blockers,
    schema_version: schemaVersion,
    release: {
      sha: runtimeReleaseSha,
      status: runtimeReleaseSha ? 'VERIFIED' : 'UNAVAILABLE',
    },
    strategy,
    universe: {
      ...universe,
      expected_instruments: expectedInstruments,
      active_instruments: activeInstruments,
      provider_aliases: providerAliases,
      company_mappings: companyMappings,
      mapping_coverage_pct: mappingCoverage,
    },
    market,
    data: {
      provider,
      reference_entitlement: referenceEntitlement,
      market_data_mode: publicPretrade
        ? 'PUBLIC_DELAYED_PRETRADE'
        : 'LICENSED_LAST_TRADE',
      display_delay_seconds: numberOrZero(
        provider?.nominal_delay_seconds,
      ),
      quoted_instruments: numberOrZero(quotes.quoted_instruments),
      fresh_instruments: freshInstruments,
      fresh_quote_coverage_pct: freshQuoteCoverage,
      latest_event_time: quotes.latest_event_time ?? null,
      latest_received_at: quotes.latest_received_at ?? null,
      freshness_status: marketIsOpen
        ? (
          (
            publicPretrade
              ? freshInstruments > 0
              : freshQuoteCoverage === 100
          )
            ? 'FRESH'
            : 'INCOMPLETE'
        )
        : 'NOT_REQUIRED',
      last_sync_status: sync.status ?? null,
      last_sync_finished_at: sync.finished_at ?? null,
      open_gaps: numberOrZero(sync.open_gaps),
    },
    index,
    risk: {
      ...risk,
      open_positions: openPositions,
      largest_sector_positions: largestSectorPositions,
      unpriced_positions: unpricedPositions,
      gross_exposure: numberOrZero(risk.gross_exposure),
      cash: numberOrZero(risk.cash),
      total_value: numberOrZero(risk.total_value),
      entry_status: entryStatus,
      max_daily_loss_pct: numberOrZero(risk.max_daily_loss_pct),
      daily_loss_breached: dailyLossBreached,
      daily_return_pct: (
        risk.daily_return_pct == null
          ? null
          : Number(risk.daily_return_pct)
      ),
      max_positions: maxPositions,
      max_sector_positions: maxSectorPositions,
      max_position_pct: numberOrZero(
        strategyConfig.max_position_pct,
      ),
      status: riskHasBreach ? 'BREACH' : 'WITHIN_LIMITS',
    },
    benchmark,
    monitoring: {
      active_alerts: operationalAlerts,
      scheduled_routines: scheduledRoutines,
    },
    activity: {
      local_date: activity.local_date ?? null,
      decisions_today: numberOrZero(activity.decisions_today),
      trades_today: numberOrZero(activity.trades_today),
      latest_decision_at: activity.latest_decision_at ?? null,
      latest_trade_at: activity.latest_trade_at ?? null,
    },
    pipelines: {
      learning: {
        status: pipelines.learning_status ?? null,
        evaluated_at: pipelines.learning_evaluated_at ?? null,
        error_code: pipelines.learning_error_code ?? null,
      },
      knowledge: {
        status: pipelines.knowledge_status ?? null,
        synced_at: pipelines.knowledge_synced_at ?? null,
        error_code: pipelines.knowledge_error_code ?? null,
        backlog: pipelines.knowledge_backlog_counts ?? {},
      },
    },
    funnel: {
      ai_decision_id: funnel.ai_decision_id ?? null,
      decided_at: funnel.decided_at ?? null,
      candidates_read: numberOrZero(funnel.candidates_read),
      candidates_filtered: numberOrZero(funnel.candidates_filtered),
      candidates_ranked: numberOrZero(funnel.candidates_ranked),
      candidates_eligible: numberOrZero(funnel.candidates_eligible),
      candidates_exploration: numberOrZero(
        funnel.candidates_exploration,
      ),
      model_actions: numberOrZero(funnel.model_actions),
      validations_accepted: numberOrZero(
        funnel.validations_accepted,
      ),
      validations_rejected: numberOrZero(
        funnel.validations_rejected,
      ),
      order_attempts: numberOrZero(funnel.order_attempts),
      order_rejections: numberOrZero(funnel.order_rejections),
      fills: numberOrZero(funnel.fills),
      stopping_reasons: funnel.stopping_reasons ?? {},
    },
    evidence_reports: {
      daily: evidenceReports.daily ?? null,
      daily_generated_at: evidenceReports.daily_generated_at ?? null,
      weekly: evidenceReports.weekly ?? null,
      weekly_generated_at: evidenceReports.weekly_generated_at ?? null,
    },
  }

  return {
    ...status,
    activation_actions: buildActivationActions(status),
  }
}


export async function loadOperationalStatus(pool) {
  const result = await pool.query(OPERATIONAL_STATUS_QUERY)
  if (result.rows.length !== 1) {
    throw new Error('Operational status snapshot is missing')
  }
  return buildOperationalStatus({
    ...result.rows[0],
    runtime_release_sha: (
      process.env.RELEASE_SHA ?? result.rows[0].runtime_release_sha
    ),
  })
}
