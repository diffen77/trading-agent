import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'

import {
  buildUniverseInsights,
  loadUniverseStatus,
} from '../lib/server/universe-status.mjs'


function emptyOperationalSummary(overrides = {}) {
  return {
    active_instruments: 416,
    company_mappings: 0,
    provider_aliases: 0,
    market_data_files: 0,
    archivable_data_files: 2,
    object_archived_files: 2,
    object_archive_pending_files: 0,
    object_archive_total_bytes: 2048,
    object_archive_status: 'SUCCEEDED',
    object_archive_evaluated_at: '2026-08-05T08:30:00.000Z',
    object_archive_failed_count: 0,
    market_quotes: 0,
    ai_decisions: 0,
    trades: 0,
    active_learnings: 0,
    snapshot_date: '2026-07-25',
    snapshot_received_at: '2026-07-30T10:00:00.000Z',
    snapshot_file_count: 2,
    snapshot_instrument_count: 416,
    snapshot_provider: 'esma-firds',
    contract_provider: 'nasdaq-nordic',
    contract_product_name: 'Nasdaq Nordic Equity Level 1 delayed',
    contract_delivery_mode: 'DELAYED_15M',
    contract_status: 'DRAFT',
    validation_status: null,
    acceptance_session_count: 0,
    ...overrides,
  }
}


test('insights distinguish imported reference data from unavailable trading data', () => {
  const insights = buildUniverseInsights(emptyOperationalSummary())

  assert.deepEqual(
    insights.map(insight => insight.code),
    [
      'REFERENCE_UNIVERSE_ACTIVE',
      'OBJECT_ARCHIVE_CURRENT',
      'TICKER_MAPPING_EMPTY',
      'QUOTE_CURRENCY_UNVERIFIED',
      'MARKET_PRICES_EMPTY',
      'PROVIDER_NOT_VALIDATED',
      'NO_TRADE_LEARNINGS',
    ],
  )
  assert.equal(insights[0].level, 'ready')
  assert.equal(insights[1].level, 'ready')
  assert.equal(insights[2].level, 'blocked')
  assert.equal(insights[3].level, 'blocked')
})


test('insights expose failed or lagging object archival without blocking trading', () => {
  const insights = buildUniverseInsights(emptyOperationalSummary({
    archivable_data_files: 4,
    object_archived_files: 2,
    object_archive_pending_files: 2,
    object_archive_status: 'FAILED',
    object_archive_failed_count: 2,
  }))
  const archive = insights.find(
    insight => insight.code === 'OBJECT_ARCHIVE_DEGRADED',
  )

  assert.equal(archive.level, 'blocked')
  assert.match(archive.detail, /2 av 4/)
  assert.match(archive.detail, /handeln fortsätter/)
})


test('loader returns a bounded, paginated XSTO view using query parameters', async () => {
  const queries = []
  const pool = {
    async query(sql, parameters) {
      queries.push({ sql, parameters })
      if (queries.length === 1) {
        return { rows: [emptyOperationalSummary()] }
      }
      if (queries.length === 2) {
        return {
          rows: [{
            isin: 'SE0000000001',
            symbol: null,
            name: 'Exempelbolag',
            cfi_code: 'ESVUFR',
            currency: 'SEK',
            instrument_type: 'EQUITY',
            first_trade_date: '2020-01-01',
            reference_snapshot_date: '2026-07-25',
            source: 'esma-firds',
            synced_at: '2026-07-30T10:00:00.000Z',
            ticker: null,
            uses_isin_key: false,
          }],
        }
      }
      if (queries.length === 3) {
        return {
          rows: [
            { dimension: 'currency', key: 'SEK', count: 400 },
            { dimension: 'type', key: 'EQUITY', count: 416 },
          ],
        }
      }
      return { rows: [] }
    },
  }

  const status = await loadUniverseStatus(pool, {
    page: 2,
    pageSize: 20,
  })

  assert.deepEqual(queries[1].parameters, [20, 20])
  assert.match(queries[1].sql, /LIMIT \$1 OFFSET \$2/)
  assert.equal(status.pagination.page, 2)
  assert.equal(status.pagination.page_size, 20)
  assert.equal(status.pagination.total_items, 416)
  assert.equal(status.instruments[0].ticker, null)
  assert.deepEqual(status.breakdown.currencies, [
    { key: 'SEK', count: 400 },
  ])
  assert.deepEqual(status.breakdown.instrument_types, [
    { key: 'EQUITY', count: 416 },
  ])
})


test('dashboard route bounds pagination and disables caching', () => {
  const routeUrl = new URL(
    '../app/api/universe/route.ts',
    import.meta.url,
  )
  assert.ok(existsSync(routeUrl), 'universe API route must exist')

  const route = readFileSync(routeUrl, 'utf8')
  assert.match(route, /pageSize > 100/)
  assert.match(route, /status: 400/)
  assert.match(route, /Cache-Control': 'no-store'/)
  assert.match(route, /loadUniverseStatus/)
})


test('dashboard renders real universe data and operational insights', () => {
  const page = readFileSync(
    new URL('../app/page.tsx', import.meta.url),
    'utf8',
  )
  const panel = readFileSync(
    new URL('../app/universe-panel.tsx', import.meta.url),
    'utf8',
  )
  const dashboard = `${page}\n${panel}`

  assert.match(dashboard, /fetch\('\/api\/universe\?page=1&pageSize=12'\)/)
  assert.match(dashboard, /Marknadsdata &amp; lärdomar/)
  assert.match(dashboard, /Rådataarkiv/)
  assert.match(dashboard, /summary\.object_archived_files/)
  assert.match(dashboard, /universe\.instruments/)
  assert.match(dashboard, /universe\.insights/)
  assert.match(dashboard, /Ej tillgänglig i gratisflödet/)
  assert.match(dashboard, /Inga utvärderade handelslärdomar/)
})
