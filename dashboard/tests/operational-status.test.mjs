import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildOperationalStatus,
  loadOperationalStatus,
} from '../lib/server/operational-status.mjs'


function readySnapshot() {
  return {
    checked_at: '2026-07-29T10:00:00.000Z',
    schema_version: 44,
    strategy: {
      version: 'momentum-report-swing-v1',
      config_hash: 'a'.repeat(64),
      config: {
        max_positions: 5,
        max_position_pct: 25,
        max_sector_positions: 2,
        stop_loss_pct: -5,
        take_profit_pct: 10,
        min_confidence: 55,
      },
    },
    universe: {
      snapshot_date: '2026-07-25',
      expected_instruments: 416,
      active_instruments: 416,
      provider_aliases: 416,
      company_mappings: 416,
    },
    provider: {
      provider: 'licensed-provider',
      product_name: 'XSTO delayed',
      delivery_mode: 'DELAYED_15M',
      nominal_delay_seconds: 900,
      max_transport_lag_seconds: 30,
      contract_status: 'VALIDATED',
      validation_status: 'PASSED',
      contract_ready: true,
      validation_ready: true,
    },
    reference_entitlement: {
      contract_key: 'nasdaq-nordic-reference-xsto-v1',
      product_name: 'Nordic Equity Reference Data Files',
      status: 'VALIDATED',
      valid_until: '2026-12-31',
      raw_storage_allowed: true,
      derived_storage_allowed: true,
      entitlement_ready: true,
    },
    market: {
      session_date: '2026-07-29',
      phase: 'OPEN',
      timezone_name: 'Europe/Stockholm',
      opens_at: '2026-07-29T07:00:00.000Z',
      closes_at: '2026-07-29T15:30:00.000Z',
    },
    quotes: {
      quoted_instruments: 416,
      fresh_instruments: 416,
      latest_event_time: '2026-07-29T09:45:00.000Z',
      latest_received_at: '2026-07-29T10:00:00.000Z',
    },
    sync: {
      status: 'SUCCEEDED',
      finished_at: '2026-07-29T10:00:00.000Z',
      open_gaps: 0,
    },
    index: {
      symbol: 'OMXSGI',
      provider: 'licensed-index',
      contract_ready: true,
      validation_ready: true,
      signal_fresh: true,
      current_level: 245,
      previous_close: 250,
      change_pct: -2,
      event_time: '2026-07-29T09:45:00.000Z',
      received_at: '2026-07-29T10:00:00.000Z',
    },
    risk: {
      open_positions: 2,
      largest_sector_positions: 1,
      unpriced_positions: 0,
      gross_exposure: 8000,
      cash: 12000,
      total_value: 20000,
      entry_status: 'ACTIVE',
      max_daily_loss_pct: 3,
      daily_loss_breached: false,
      daily_return_pct: -0.5,
    },
    benchmark: {
      experiment_key: 'xsto-forward-2026',
      status: 'RUNNING',
      trading_sessions: 12,
      closed_trades: 3,
      critical_incidents: 0,
      net_return_pct: null,
      benchmark_return_pct: null,
      excess_return_pct: null,
      max_drawdown_pct: null,
      data_coverage_pct: null,
      passed: null,
    },
    operational_alerts: [],
    scheduled_routines: [],
  }
}


test('ready evidence produces an explicit READY status', () => {
  const status = buildOperationalStatus(readySnapshot())

  assert.equal(status.overall_status, 'READY')
  assert.deepEqual(status.blockers, [])
  assert.equal(status.universe.mapping_coverage_pct, 100)
  assert.equal(status.data.fresh_quote_coverage_pct, 100)
  assert.equal(
    status.data.reference_entitlement.entitlement_ready,
    true,
  )
  assert.equal(status.risk.status, 'WITHIN_LIMITS')
  assert.deepEqual(status.monitoring.active_alerts, [])
  assert.deepEqual(status.monitoring.scheduled_routines, [])
})


test('missing legal data evidence fails closed', () => {
  const snapshot = readySnapshot()
  snapshot.universe = {
    snapshot_date: null,
    expected_instruments: 0,
    active_instruments: 0,
    provider_aliases: 0,
    company_mappings: 0,
  }
  snapshot.provider = null
  snapshot.reference_entitlement = null
  snapshot.market.phase = 'CLOSED'
  snapshot.quotes = {
    quoted_instruments: 0,
    fresh_instruments: 0,
    latest_event_time: null,
    latest_received_at: null,
  }
  snapshot.sync = { status: null, finished_at: null, open_gaps: 0 }
  snapshot.benchmark = null

  const status = buildOperationalStatus(snapshot)

  assert.equal(status.overall_status, 'BLOCKED')
  assert.deepEqual(status.blockers, [
    'REFERENCE_SNAPSHOT_MISSING',
    'REFERENCE_ENTITLEMENT_NOT_READY',
    'QUOTE_PROVIDER_NOT_READY',
    'XSTO_SESSION_NOT_OPEN',
  ])
  assert.equal(status.data.freshness_status, 'NOT_REQUIRED')
  assert.equal(status.data.reference_entitlement, null)
})


test('public delayed pre-trade needs executable books but no paid aliases or index', () => {
  const snapshot = readySnapshot()
  snapshot.universe.provider_aliases = 0
  snapshot.provider = {
    ...snapshot.provider,
    provider: 'nasdaq-nordic',
    product_name: 'Nasdaq public delayed pre-trade',
    data_type: 'delayed-pre-trade-equity',
    authorization_basis: 'PUBLIC_NONCOMMERCIAL_TERMS',
  }
  snapshot.reference_entitlement = null
  snapshot.quotes = {
    quoted_instruments: 37,
    fresh_instruments: 37,
    latest_event_time: '2026-07-29T09:45:00.000Z',
    latest_received_at: '2026-07-29T10:00:00.000Z',
  }
  snapshot.index = null
  snapshot.benchmark = null

  const status = buildOperationalStatus(snapshot)

  assert.equal(status.overall_status, 'READY')
  assert.deepEqual(status.blockers, [])
  assert.equal(status.universe.mapping_coverage_pct, 100)
  assert.equal(status.data.market_data_mode, 'PUBLIC_DELAYED_PRETRADE')
  assert.equal(status.data.display_delay_seconds, 900)
  assert.equal(status.data.freshness_status, 'FRESH')
})


test('risk breaches, unpriced positions and incidents remain visible', () => {
  const snapshot = readySnapshot()
  snapshot.risk = {
    ...snapshot.risk,
    open_positions: 6,
    largest_sector_positions: 3,
    unpriced_positions: 1,
  }
  snapshot.benchmark.critical_incidents = 1

  const status = buildOperationalStatus(snapshot)

  assert.equal(status.overall_status, 'BLOCKED')
  assert.deepEqual(status.blockers, [
    'POSITION_LIMIT_BREACH',
    'SECTOR_LIMIT_BREACH',
    'UNPRICED_POSITION',
    'CRITICAL_BENCHMARK_INCIDENT',
  ])
  assert.equal(status.risk.status, 'BREACH')
})


test('manual halt and latched daily loss remain visible', () => {
  const snapshot = readySnapshot()
  snapshot.risk.entry_status = 'HALTED'
  snapshot.risk.daily_loss_breached = true
  snapshot.risk.daily_return_pct = -3.2

  const status = buildOperationalStatus(snapshot)

  assert.equal(status.overall_status, 'BLOCKED')
  assert.deepEqual(status.blockers, [
    'TRADING_HALTED',
    'DAILY_LOSS_LIMIT_BREACHED',
  ])
  assert.equal(status.risk.status, 'BREACH')
  assert.equal(status.risk.daily_return_pct, -3.2)
})


test('missing authorized OMXSGI signal blocks operations', () => {
  const snapshot = readySnapshot()
  snapshot.index.signal_fresh = false

  const status = buildOperationalStatus(snapshot)

  assert.equal(status.overall_status, 'BLOCKED')
  assert.deepEqual(status.blockers, ['INDEX_SIGNAL_NOT_READY'])
})


test('active page alerts block while ticket and resolved alerts remain safe', () => {
  const snapshot = readySnapshot()
  snapshot.operational_alerts = [
    {
      alert_key: 'scheduled:2026-07-29:open',
      code: 'SCHEDULED_ROUTINE_MISSED',
      severity: 'PAGE',
      state: 'OPEN',
      summary: 'Schemakörningen open saknar lyckat utfall.',
      runbook: 'docs/operations.md',
      observed_at: '2026-07-29T10:01:00.000Z',
    },
    {
      alert_key: 'data:delayed',
      code: 'MARKET_DATA_DELAY_WARNING',
      severity: 'TICKET',
      state: 'OPEN',
      summary: 'Dataleveransen närmar sig fördröjningsgränsen.',
      runbook: 'docs/operations.md',
      observed_at: '2026-07-29T10:00:00.000Z',
    },
    {
      alert_key: 'ledger:resolved',
      code: 'LEDGER_INVARIANT_FAILED',
      severity: 'PAGE',
      state: 'RESOLVED',
      summary: 'Ledgerkontrollen är återställd.',
      runbook: 'docs/operations.md',
      observed_at: '2026-07-29T09:59:00.000Z',
    },
  ]
  snapshot.scheduled_routines = [
    {
      routine_key: '2026-07-29:open',
      routine_name: 'open',
      scheduled_at: '2026-07-29T07:00:00.000Z',
      status: 'FAILED',
      failure_code: 'MARKET_DATA_NOT_READY',
      observed_at: '2026-07-29T07:01:00.000Z',
    },
  ]

  const status = buildOperationalStatus(snapshot)

  assert.equal(status.overall_status, 'BLOCKED')
  assert.ok(status.blockers.includes('SCHEDULED_ROUTINE_MISSED'))
  assert.ok(!status.blockers.includes('MARKET_DATA_DELAY_WARNING'))
  assert.ok(!status.blockers.includes('LEDGER_INVARIANT_FAILED'))
  assert.deepEqual(
    status.monitoring.active_alerts.map(alert => alert.code),
    ['SCHEDULED_ROUTINE_MISSED', 'MARKET_DATA_DELAY_WARNING'],
  )
  assert.deepEqual(
    status.monitoring.scheduled_routines,
    snapshot.scheduled_routines,
  )
})


test('database loader derives status from one consistent snapshot', async () => {
  const queries = []
  const pool = {
    async query(query) {
      queries.push(query)
      return { rows: [readySnapshot()] }
    },
  }

  const status = await loadOperationalStatus(pool)

  assert.equal(status.overall_status, 'READY')
  assert.equal(queries.length, 1)
  assert.match(queries[0], /market_data_provider_contracts/)
  assert.match(queries[0], /paper_benchmark_experiments/)
  assert.match(queries[0], /market_quotes/)
  assert.match(queries[0], /market_index_levels/)
  assert.match(queries[0], /trading_controls/)
  assert.match(queries[0], /trading_daily_risk/)
  assert.match(queries[0], /operational_alert_events/)
  assert.match(queries[0], /scheduled_routine_events/)
  assert.match(queries[0], /contract_ready AND validation_ready/)
})
