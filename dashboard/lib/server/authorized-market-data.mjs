export const AUTHORIZED_MARKET_DATA_CTES = `
latest_reference AS (
  SELECT id, instrument_count, universe_checksum_sha256
  FROM reference_data_snapshots
  WHERE mic = 'XSTO'
  ORDER BY snapshot_date DESC, id DESC
  LIMIT 1
),
authorized_provider AS (
  SELECT
    contract.id,
    contract.provider,
    contract.data_type,
    contract.authorization_basis
  FROM market_data_provider_contracts contract
  JOIN LATERAL (
    SELECT validation.*
    FROM market_data_provider_validations validation
    WHERE validation.contract_id = contract.id
    ORDER BY validation.validated_at DESC, validation.id DESC
    LIMIT 1
  ) validation ON TRUE
  WHERE contract.mic = 'XSTO'
    AND contract.data_type IN (
      'delayed-post-trade-equity',
      'delayed-pre-trade-equity',
      'realtime-equity-level-1'
    )
    AND contract.status = 'VALIDATED'
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
    AND validation.status = 'PASSED'
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
    AND validation.product_covered_instruments =
      validation.expected_instruments
    AND validation.symbol_mapped_instruments =
      validation.expected_instruments
    AND validation.sample_file_count > 0
    AND validation.sample_quote_count > 0
    AND validation.max_observed_delivery_seconds <= (
      contract.nominal_delay_seconds
      + contract.max_transport_lag_seconds
    )
    AND validation.max_observed_delivery_seconds
      >= contract.nominal_delay_seconds
  ORDER BY contract.updated_at DESC, contract.id DESC
  LIMIT 1
),
latest_authorized_quotes AS (
  SELECT *
  FROM (
    SELECT DISTINCT ON (quote.instrument_id)
      quote.instrument_id,
      quote.last_price,
      quote.event_time,
      quote.received_at,
      quote.source
    FROM market_quotes quote
    JOIN authorized_provider provider
      ON provider.data_type != 'delayed-pre-trade-equity'
    JOIN market_data_files source_file
      ON source_file.id = quote.data_file_id
      AND source_file.provider_contract_id = provider.id
    WHERE quote.source LIKE provider.provider || '%'
      AND quote.event_time <= NOW()
      AND quote.received_at <= NOW()
    ORDER BY quote.instrument_id, quote.event_time DESC, quote.id DESC
  ) last_trade
  UNION ALL
  SELECT
    state.instrument_id,
    (state.bid_price + state.ask_price) / 2 AS last_price,
    GREATEST(state.bid_event_time, state.ask_event_time) AS event_time,
    state.received_at,
    state.source
  FROM authorized_provider provider
  JOIN LATERAL (
    SELECT batch.id
    FROM pre_trade_batches batch
    JOIN pre_trade_stream_cursors seal
      ON seal.batch_id = batch.id
    WHERE provider.data_type = 'delayed-pre-trade-equity'
      AND batch.provider_contract_id = provider.id
    ORDER BY batch.report_minute DESC, batch.id DESC
    LIMIT 1
  ) batch ON TRUE
  JOIN pre_trade_book_states state
    ON state.batch_id = batch.id
  WHERE state.bid_price IS NOT NULL
    AND state.ask_price IS NOT NULL
    AND state.trading_system = 'CLOB'
    AND state.trading_phase = 'COTR'
)
`
