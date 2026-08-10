const INITIAL_CAPITAL = 20000

const PORTFOLIO_STATE_QUERY = `
WITH
latest_reference AS (
  SELECT id, instrument_count, universe_checksum_sha256
  FROM reference_data_snapshots
  WHERE mic = 'XSTO'
  ORDER BY snapshot_date DESC, id DESC
  LIMIT 1
),
authorized_provider AS (
  SELECT contract.id, contract.provider, contract.data_type
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
    AND CURRENT_DATE BETWEEN contract.valid_from AND contract.valid_until
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
latest_balance AS (
  SELECT cash
  FROM balance
  ORDER BY id DESC
  LIMIT 1
)
SELECT
  balance.cash,
  position.ticker,
  position.shares,
  position.avg_price,
  company.name AS company_name,
  company.sector,
  quote.last_price AS current_price,
  quote.source AS price_source,
  quote.event_time AS price_event_time,
  quote.received_at AS price_received_at,
  (provider.provider IS NOT NULL) AS provider_ready,
  signal.rsi,
  signal.sma20,
  trade.confidence,
  trade.reasoning,
  trade.hypothesis,
  trade.executed_at AS opened_at,
  trade.target_price,
  trade.stop_loss,
  CASE
    WHEN trade.executed_at IS NULL THEN NULL
    ELSE EXTRACT(DAY FROM NOW() - trade.executed_at)::int
  END AS days_held
FROM latest_balance balance
LEFT JOIN portfolio position
  ON position.shares > 0
LEFT JOIN companies company
  ON company.ticker = position.ticker
LEFT JOIN instruments instrument
  ON instrument.id = company.instrument_id
  AND instrument.mic = 'XSTO'
  AND instrument.status = 'ACTIVE'
LEFT JOIN authorized_provider provider
  ON TRUE
LEFT JOIN LATERAL (
  SELECT *
  FROM (
    SELECT
      candidate.last_price,
      candidate.source,
      candidate.event_time,
      candidate.received_at
    FROM market_quotes candidate
    JOIN market_data_files source_file
      ON source_file.id = candidate.data_file_id
      AND source_file.provider_contract_id = provider.id
    WHERE provider.data_type != 'delayed-pre-trade-equity'
      AND candidate.instrument_id = instrument.id
      AND candidate.source LIKE provider.provider || '%'
      AND candidate.event_time <= NOW()
      AND candidate.received_at <= NOW()
    ORDER BY candidate.event_time DESC, candidate.id DESC
    LIMIT 1
  ) last_trade
  UNION ALL
  SELECT *
  FROM (
    SELECT
      (state.bid_price + state.ask_price) / 2 AS last_price,
      state.source,
      GREATEST(
        state.bid_event_time,
        state.ask_event_time
      ) AS event_time,
      state.received_at
    FROM pre_trade_batches batch
    JOIN pre_trade_stream_cursors seal
      ON seal.batch_id = batch.id
    JOIN pre_trade_book_states state
      ON state.batch_id = batch.id
      AND state.instrument_id = instrument.id
    WHERE provider.data_type = 'delayed-pre-trade-equity'
      AND batch.provider_contract_id = provider.id
      AND state.bid_price IS NOT NULL
      AND state.ask_price IS NOT NULL
      AND state.trading_system = 'CLOB'
      AND state.trading_phase = 'COTR'
    ORDER BY batch.report_minute DESC, batch.id DESC
    LIMIT 1
  ) pre_trade
  LIMIT 1
) quote ON TRUE
LEFT JOIN LATERAL (
  SELECT candidate.rsi, candidate.sma20
  FROM technical_signals candidate
  WHERE candidate.ticker = position.ticker
  ORDER BY candidate.date DESC, candidate.id DESC
  LIMIT 1
) signal ON TRUE
LEFT JOIN LATERAL (
  SELECT
    candidate.confidence,
    candidate.reasoning,
    candidate.hypothesis,
    candidate.executed_at,
    candidate.target_price,
    candidate.stop_loss
  FROM trades candidate
  WHERE candidate.ticker = position.ticker
    AND candidate.action = 'BUY'
    AND candidate.closed_at IS NULL
  ORDER BY candidate.executed_at DESC, candidate.id DESC
  LIMIT 1
) trade ON TRUE
ORDER BY position.ticker
`


function finiteNumber(value, name) {
  const number = Number(value)
  if (!Number.isFinite(number)) {
    throw new Error(`${name} is not finite`)
  }
  return number
}


function money(value) {
  return Number(value.toFixed(2))
}


export function buildPortfolioState(rows) {
  if (!Array.isArray(rows) || rows.length === 0) {
    throw new Error('Balance state is missing')
  }
  const cash = finiteNumber(rows[0].cash, 'cash')
  if (cash < 0) {
    throw new Error('cash cannot be negative')
  }

  const positions = rows
    .filter(row => row.ticker != null)
    .map(row => {
      if (
        row.provider_ready !== true
        || typeof row.price_source !== 'string'
        || row.price_source.length === 0
        || row.price_event_time == null
        || row.price_received_at == null
      ) {
        throw new Error(
          `Position ${row.ticker} is missing an authorized provider quote`,
        )
      }
      const shares = finiteNumber(row.shares, 'shares')
      const avgPrice = finiteNumber(row.avg_price, 'avg_price')
      const currentPrice = finiteNumber(
        row.current_price,
        'current_price',
      )
      if (shares <= 0 || avgPrice <= 0 || currentPrice <= 0) {
        throw new Error(`Position ${row.ticker} has invalid values`)
      }
      const marketValue = money(shares * currentPrice)
      const pnlKr = money(shares * (currentPrice - avgPrice))
      const pnlPct = Number(
        (((currentPrice - avgPrice) / avgPrice) * 100).toFixed(2),
      )
      return {
        ticker: row.ticker,
        shares,
        avg_price: avgPrice,
        current_price: currentPrice,
        company_name: row.company_name ?? null,
        sector: row.sector ?? null,
        rsi: row.rsi == null ? null : finiteNumber(row.rsi, 'rsi'),
        sma20: row.sma20 == null
          ? null
          : finiteNumber(row.sma20, 'sma20'),
        confidence: row.confidence == null
          ? null
          : finiteNumber(row.confidence, 'confidence'),
        reasoning: row.reasoning ?? null,
        hypothesis: row.hypothesis ?? null,
        opened_at: row.opened_at ?? null,
        target_price: row.target_price == null
          ? null
          : finiteNumber(row.target_price, 'target_price'),
        stop_loss: row.stop_loss == null
          ? null
          : finiteNumber(row.stop_loss, 'stop_loss'),
        days_held: row.days_held == null
          ? null
          : finiteNumber(row.days_held, 'days_held'),
        pnl_pct: pnlPct,
        pnl_kr: pnlKr,
        market_value: marketValue,
        price_source: row.price_source,
        price_event_time: row.price_event_time,
        price_received_at: row.price_received_at,
      }
    })
    .sort((left, right) => right.market_value - left.market_value)

  const positionsValue = money(
    positions.reduce(
      (total, position) => total + position.market_value,
      0,
    ),
  )
  const totalValue = money(cash + positionsValue)
  const pnl = money(totalValue - INITIAL_CAPITAL)
  const pnlPct = Number(
    (((totalValue / INITIAL_CAPITAL) - 1) * 100).toFixed(4),
  )
  return {
    cash,
    positions_value: positionsValue,
    total_value: totalValue,
    pnl,
    pnl_pct: pnlPct,
    positions,
  }
}


export async function loadPortfolioState(pool) {
  const result = await pool.query(PORTFOLIO_STATE_QUERY)
  return buildPortfolioState(result.rows)
}
