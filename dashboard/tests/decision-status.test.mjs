import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const decisionsRoute = readFileSync(
  new URL('../app/api/decisions/route.ts', import.meta.url),
  'utf8',
)
const dashboardPage = readFileSync(
  new URL('../app/page.tsx', import.meta.url),
  'utf8',
)

test('decision API links analyses to evidence and executed trades', () => {
  assert.match(decisionsRoute, /candidate_predictions/i)
  assert.match(decisionsRoute, /prediction\.ai_decision_id\s*=\s*decision\.id/i)
  assert.match(decisionsRoute, /trades\s+AS\s+trade/i)
  assert.match(decisionsRoute, /trade\.decision_id\s*=\s*decision\.id/i)
})

test('decision cards present validation and execution status', () => {
  assert.match(dashboardPage, /decisionActionPresentation/)
  assert.match(dashboardPage, /prediction_count/)
  assert.match(dashboardPage, /executed_trades/)
})
