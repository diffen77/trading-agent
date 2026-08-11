const UNIVERSE_SUMMARY_QUERY = `
WITH latest_reference AS (
  SELECT
    provider,
    snapshot_date,
    received_at,
    file_count,
    instrument_count
  FROM reference_data_snapshots
  WHERE mic = 'XSTO'
  ORDER BY snapshot_date DESC, id DESC
  LIMIT 1
),
latest_contract AS (
  SELECT
    contract.provider,
    contract.product_name,
    contract.data_type,
    contract.authorization_basis,
    contract.delivery_mode,
    contract.status AS contract_status,
    validation.status AS validation_status,
    COALESCE(validation.acceptance_session_count, 0)::int
      AS acceptance_session_count
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
latest_object_archive AS (
  SELECT
    status,
    evaluated_at,
    failed_count
  FROM object_archive_runs
  ORDER BY evaluated_at DESC, id DESC
  LIMIT 1
)
SELECT
  (
    SELECT COUNT(*)::int
    FROM instruments
    WHERE mic = 'XSTO' AND status = 'ACTIVE'
  ) AS active_instruments,
  (
    SELECT COUNT(*)::int
    FROM companies company
    JOIN instruments instrument ON instrument.id = company.instrument_id
    WHERE instrument.mic = 'XSTO' AND instrument.status = 'ACTIVE'
  ) AS company_mappings,
  (
    SELECT COUNT(DISTINCT alias.instrument_id)::int
    FROM instrument_aliases alias
    JOIN instruments instrument ON instrument.id = alias.instrument_id
    WHERE instrument.mic = 'XSTO'
      AND instrument.status = 'ACTIVE'
      AND alias.active = TRUE
  ) AS provider_aliases,
  (
    (SELECT COUNT(*) FROM market_data_files)
    + (SELECT COUNT(*) FROM pre_trade_batches)
  )::int AS market_data_files,
  (
    (SELECT COUNT(*) FROM market_data_files)
    + (SELECT COUNT(*) FROM reference_data_files)
  )::int
    AS archivable_data_files,
  (
    (SELECT COUNT(*) FROM market_data_object_archives)
    + (SELECT COUNT(*) FROM reference_data_object_archives)
  )::int
    AS object_archived_files,
  (
    (
      SELECT COUNT(*)
      FROM market_data_files source
      LEFT JOIN market_data_object_archives archived
        ON archived.market_data_file_id = source.id
      WHERE archived.id IS NULL
    )
    + (
      SELECT COUNT(*)
      FROM reference_data_files source
      LEFT JOIN reference_data_object_archives archived
        ON archived.reference_data_file_id = source.id
      WHERE archived.id IS NULL
    )
  )::int AS object_archive_pending_files,
  (
    COALESCE(
      (SELECT SUM(compressed_bytes) FROM market_data_object_archives),
      0
    )
    + COALESCE(
      (SELECT SUM(compressed_bytes) FROM reference_data_object_archives),
      0
    )
  )::bigint AS object_archive_total_bytes,
  (
    SELECT COALESCE(SUM(records_inserted), 0)::bigint
    FROM data_sync_runs
    WHERE data_type IN (
      'delayed-post-trade-equity',
      'delayed-pre-trade-equity',
      'realtime-equity-level-1'
    )
      AND status IN ('SUCCEEDED', 'PARTIAL')
  ) AS market_quotes,
  (SELECT COUNT(*)::int FROM ai_decisions) AS ai_decisions,
  (SELECT COUNT(*)::int FROM trades) AS trades,
  (
    SELECT COUNT(*)::int
    FROM learnings
    WHERE active = TRUE
  ) AS active_learnings,
  reference.snapshot_date,
  reference.received_at AS snapshot_received_at,
  reference.file_count AS snapshot_file_count,
  reference.instrument_count AS snapshot_instrument_count,
  reference.provider AS snapshot_provider,
  contract.provider AS contract_provider,
  contract.product_name AS contract_product_name,
  contract.data_type AS contract_data_type,
  contract.authorization_basis,
  contract.delivery_mode AS contract_delivery_mode,
  contract.contract_status,
  contract.validation_status,
  COALESCE(contract.acceptance_session_count, 0)::int
    AS acceptance_session_count,
  object_archive.status AS object_archive_status,
  object_archive.evaluated_at AS object_archive_evaluated_at,
  COALESCE(object_archive.failed_count, 0)::int
    AS object_archive_failed_count
FROM (SELECT 1) singleton
LEFT JOIN latest_reference reference ON TRUE
LEFT JOIN latest_contract contract ON TRUE
LEFT JOIN latest_object_archive object_archive ON TRUE
`


const UNIVERSE_INSTRUMENTS_QUERY = `
SELECT
  instrument.isin,
  instrument.symbol,
  instrument.name,
  instrument.cfi_code,
  instrument.currency,
  instrument.instrument_type,
  instrument.first_trade_date,
  instrument.reference_snapshot_date,
  instrument.source,
  instrument.synced_at,
  CASE
    WHEN company.ticker = BTRIM(instrument.isin) THEN NULL
    ELSE company.ticker
  END AS ticker,
  COALESCE(company.ticker = BTRIM(instrument.isin), FALSE)
    AS uses_isin_key
FROM instruments instrument
LEFT JOIN companies company ON company.instrument_id = instrument.id
WHERE instrument.mic = 'XSTO'
  AND instrument.status = 'ACTIVE'
ORDER BY instrument.name, instrument.isin
LIMIT $1 OFFSET $2
`


const UNIVERSE_BREAKDOWN_QUERY = `
SELECT
  'currency' AS dimension,
  instrument.currency AS key,
  COUNT(*)::int AS count
FROM instruments instrument
WHERE instrument.mic = 'XSTO'
  AND instrument.status = 'ACTIVE'
GROUP BY instrument.currency
UNION ALL
SELECT
  'type' AS dimension,
  instrument.instrument_type AS key,
  COUNT(*)::int AS count
FROM instruments instrument
WHERE instrument.mic = 'XSTO'
  AND instrument.status = 'ACTIVE'
GROUP BY instrument.instrument_type
ORDER BY dimension, count DESC, key
`


const ACTIVE_LEARNINGS_QUERY = `
SELECT
  category,
  content,
  confidence,
  times_validated,
  updated_at
FROM learnings
WHERE active = TRUE
ORDER BY times_validated DESC, updated_at DESC, id DESC
LIMIT 12
`


function count(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}


function percentage(value, total) {
  if (total <= 0) return 0
  return Math.round((value / total) * 1000) / 10
}


export function buildUniverseInsights(summary) {
  const activeInstruments = count(summary.active_instruments)
  const mappedInstruments = count(summary.company_mappings)
  const providerAliases = count(summary.provider_aliases)
  const marketQuotes = count(summary.market_quotes)
  const trades = count(summary.trades)
  const activeLearnings = count(summary.active_learnings)
  const snapshotCount = count(summary.snapshot_instrument_count)
  const publicPretrade = (
    summary.contract_data_type === 'delayed-pre-trade-equity'
    && summary.authorization_basis === 'PUBLIC_NONCOMMERCIAL_TERMS'
  )
  const insights = []

  if (activeInstruments > 0 && snapshotCount === activeInstruments) {
    insights.push({
      code: 'REFERENCE_UNIVERSE_ACTIVE',
      level: 'ready',
      title: `${activeInstruments} XSTO-instrument är importerade`,
      detail: `Universumet kommer från ${summary.snapshot_provider || 'referenskällan'} och matchar den senaste sparade ögonblicksbilden.`,
    })
  } else {
    insights.push({
      code: 'REFERENCE_UNIVERSE_INCOMPLETE',
      level: 'blocked',
      title: 'Instrumentuniversumet är ofullständigt',
      detail: `${activeInstruments} aktiva instrument finns, medan senaste ögonblicksbilden anger ${snapshotCount}.`,
    })
  }

  const archivableFiles = count(summary.archivable_data_files)
  const archivedFiles = count(summary.object_archived_files)
  const pendingArchiveFiles = count(summary.object_archive_pending_files)
  if (archivableFiles > 0) {
    if (
      archivedFiles === archivableFiles
      && ['SUCCEEDED', 'SKIPPED'].includes(summary.object_archive_status)
    ) {
      insights.push({
        code: 'OBJECT_ARCHIVE_CURRENT',
        level: 'ready',
        title: 'Verifierad rådata är säkerhetskopierad',
        detail: `${archivedFiles} av ${archivableFiles} arkiverbara filer finns i det isolerade objektarkivet.`,
      })
    } else if (
      ['FAILED', 'PARTIAL'].includes(summary.object_archive_status)
    ) {
      insights.push({
        code: 'OBJECT_ARCHIVE_DEGRADED',
        level: 'blocked',
        title: 'Rådataarkivet behöver återhämtas',
        detail: `${archivedFiles} av ${archivableFiles} filer är arkiverade och ${pendingArchiveFiles} väntar. Paperhandeln fortsätter medan arkiv-workern försöker igen.`,
      })
    } else {
      insights.push({
        code: 'OBJECT_ARCHIVE_PENDING',
        level: 'waiting',
        title: 'Rådata väntar på objektarkivering',
        detail: `${archivedFiles} av ${archivableFiles} filer är arkiverade och ${pendingArchiveFiles} väntar.`,
      })
    }
  }

  if (
    mappedInstruments === 0
    || (!publicPretrade && providerAliases === 0)
  ) {
    insights.push({
      code: 'TICKER_MAPPING_EMPTY',
      level: 'blocked',
      title: 'Ticker- och bolagsmappning saknas',
      detail: 'ISIN och instrumentnamn finns, men de kan ännu inte kopplas säkert till Nasdaq-symboler och bolag.',
    })
  } else if (
    mappedInstruments < activeInstruments
    || (!publicPretrade && providerAliases < activeInstruments)
  ) {
    insights.push({
      code: 'TICKER_MAPPING_INCOMPLETE',
      level: 'blocked',
      title: 'Ticker- och bolagsmappningen är ofullständig',
      detail: `${mappedInstruments} bolag och ${providerAliases} provideralias är mappade av ${activeInstruments} instrument.`,
    })
  }

  insights.push({
    code: publicPretrade && marketQuotes > 0
      ? 'QUOTE_CURRENCY_VERIFIED'
      : 'QUOTE_CURRENCY_UNVERIFIED',
    level: publicPretrade && marketQuotes > 0 ? 'ready' : 'blocked',
    title: publicPretrade && marketQuotes > 0
      ? 'Prisvaluta verifieras från varje Nasdaq-fil'
      : 'Prisvaluta är ännu inte verifierad',
    detail: publicPretrade && marketQuotes > 0
      ? 'FIRDS används bara för instrumentidentitet; pris- och kvantitetsvaluta hämtas och kontrolleras i den fördröjda orderboken.'
      : 'FIRDS-fältet är nominell referensvaluta och får inte användas som prisvaluta när Nasdaq-priser senare läses in.',
  })

  if (marketQuotes === 0) {
    insights.push({
      code: 'MARKET_PRICES_EMPTY',
      level: 'blocked',
      title: 'Inga verifierade marknadspriser är sparade',
      detail: 'Referensdata räcker för att lista instrument, men inte för värdering, signaler eller papertrading.',
    })
  }

  if (
    summary.contract_status !== 'VALIDATED'
    || summary.validation_status !== 'PASSED'
  ) {
    insights.push({
      code: 'PROVIDER_NOT_VALIDATED',
      level: 'blocked',
      title: 'Nasdaq-feeden är inte godkänd för lagring och handel',
      detail: `Avtal ${summary.contract_status || 'saknas'}, validering ${summary.validation_status || 'saknas'} och ${count(summary.acceptance_session_count)}/${publicPretrade ? 1 : 5} acceptanssessioner.`,
    })
  }

  if (trades === 0 || activeLearnings === 0) {
    insights.push({
      code: 'NO_TRADE_LEARNINGS',
      level: 'waiting',
      title: 'Agenten har ännu inga utvärderade handelslärdomar',
      detail: `${trades} affärer och ${activeLearnings} aktiva lärdomar finns. Lärdomar ska skapas först från spårbara papertrades och utfall.`,
    })
  }

  return insights
}


function normalizeSummary(row) {
  const activeInstruments = count(row.active_instruments)
  const companyMappings = count(row.company_mappings)
  const providerAliases = count(row.provider_aliases)
  const publicPretrade = (
    row.contract_data_type === 'delayed-pre-trade-equity'
    && row.authorization_basis === 'PUBLIC_NONCOMMERCIAL_TERMS'
  )

  return {
    ...row,
    active_instruments: activeInstruments,
    company_mappings: companyMappings,
    provider_aliases: providerAliases,
    market_data_files: count(row.market_data_files),
    archivable_data_files: count(row.archivable_data_files),
    object_archived_files: count(row.object_archived_files),
    object_archive_pending_files: count(row.object_archive_pending_files),
    object_archive_total_bytes: count(row.object_archive_total_bytes),
    object_archive_failed_count: count(row.object_archive_failed_count),
    market_quotes: count(row.market_quotes),
    ai_decisions: count(row.ai_decisions),
    trades: count(row.trades),
    active_learnings: count(row.active_learnings),
    snapshot_file_count: count(row.snapshot_file_count),
    snapshot_instrument_count: count(row.snapshot_instrument_count),
    acceptance_session_count: count(row.acceptance_session_count),
    mapping_coverage_pct: percentage(
      publicPretrade
        ? companyMappings
        : Math.min(companyMappings, providerAliases),
      activeInstruments,
    ),
  }
}


export async function loadUniverseStatus(pool, { page, pageSize }) {
  if (
    !Number.isInteger(page)
    || page < 1
    || !Number.isInteger(pageSize)
    || pageSize < 1
    || pageSize > 100
  ) {
    throw new RangeError('Invalid universe pagination')
  }

  const offset = (page - 1) * pageSize
  const summaryResult = await pool.query(UNIVERSE_SUMMARY_QUERY)
  if (summaryResult.rows.length !== 1) {
    throw new Error('Universe summary is missing')
  }

  const instrumentResult = await pool.query(
    UNIVERSE_INSTRUMENTS_QUERY,
    [pageSize, offset],
  )
  const breakdownResult = await pool.query(UNIVERSE_BREAKDOWN_QUERY)
  const learningResult = await pool.query(ACTIVE_LEARNINGS_QUERY)
  const summary = normalizeSummary(summaryResult.rows[0])
  const totalItems = summary.active_instruments

  return {
    summary,
    breakdown: {
      currencies: breakdownResult.rows
        .filter(row => row.dimension === 'currency')
        .map(row => ({ key: row.key, count: count(row.count) })),
      instrument_types: breakdownResult.rows
        .filter(row => row.dimension === 'type')
        .map(row => ({ key: row.key, count: count(row.count) })),
    },
    instruments: instrumentResult.rows,
    agent_learnings: learningResult.rows,
    insights: buildUniverseInsights(summary),
    pagination: {
      page,
      page_size: pageSize,
      total_items: totalItems,
      total_pages: totalItems === 0
        ? 0
        : Math.ceil(totalItems / pageSize),
    },
  }
}
