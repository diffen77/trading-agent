import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const dockerfile = readFileSync(
  new URL('../Dockerfile', import.meta.url),
  'utf8',
)
const healthRoute = readFileSync(
  new URL('../app/api/health/route.ts', import.meta.url),
  'utf8',
)
const operationalStatus = readFileSync(
  new URL('../lib/server/operational-status.mjs', import.meta.url),
  'utf8',
)
const tradesRoute = readFileSync(
  new URL('../app/api/trades/route.ts', import.meta.url),
  'utf8',
)
const decisionsRoute = readFileSync(
  new URL('../app/api/decisions/route.ts', import.meta.url),
  'utf8',
)
const dashboardPage = readFileSync(
  new URL('../app/page.tsx', import.meta.url),
  'utf8',
)

test('standalone runtime binds to the container interface used by healthcheck', () => {
  assert.match(dockerfile, /ENV PORT=3000/)
  assert.match(dockerfile, /ENV HOSTNAME=0\.0\.0\.0/)
  assert.match(dockerfile, /127\.0\.0\.1:3000\/api\/health/)
})

test('healthcheck and operations share one required schema version', () => {
  assert.match(
    operationalStatus,
    /export const REQUIRED_SCHEMA_VERSION = 44/,
  )
  assert.match(healthRoute, /REQUIRED_SCHEMA_VERSION/)
  assert.doesNotMatch(healthRoute, /version !== \d+/)
})

test('trade journal exposes the decision and market-data audit chain', () => {
  assert.match(tradesRoute, /company\.name AS company_name/)
  assert.match(tradesRoute, /LEFT JOIN companies company/)
  assert.match(tradesRoute, /trade\.decision_id/)
  assert.match(tradesRoute, /LEFT JOIN ai_decisions/)
  assert.match(tradesRoute, /LEFT JOIN market_quotes/)
  assert.match(tradesRoute, /LEFT JOIN market_data_files/)
  assert.match(tradesRoute, /LEFT JOIN pre_trade_book_states/)
  assert.match(tradesRoute, /LEFT JOIN pre_trade_batches/)
  assert.match(dashboardPage, /fetch\('\/api\/trades'\)/)
  assert.match(dashboardPage, /Affärsjournal/)
  assert.match(dashboardPage, /source_file_checksum/)
  assert.match(dashboardPage, /Realiserad P&amp;L/)
})

test('analysis API resolves decision identifiers to company names', () => {
  assert.match(decisionsRoute, /collectDecisionIdentifiers/)
  assert.match(decisionsRoute, /FROM companies/)
  assert.match(decisionsRoute, /instrument_names/)
  assert.match(decisionsRoute, /model_backend/)
  assert.match(decisionsRoute, /reasoning_effort/)
  assert.match(dashboardPage, /Aktiv analysmodell/)
  assert.match(dashboardPage, /GPT-5\.6 Sol/)
})
