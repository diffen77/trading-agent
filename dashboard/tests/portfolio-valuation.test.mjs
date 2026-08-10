import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildPortfolioState,
  loadPortfolioState,
} from '../lib/server/portfolio-valuation.mjs'


test('cash-only portfolio is truthful without a quote provider', () => {
  const state = buildPortfolioState([
    {
      cash: '20000',
      ticker: null,
      provider_ready: false,
    },
  ])

  assert.deepEqual(state, {
    cash: 20000,
    positions_value: 0,
    total_value: 20000,
    pnl: 0,
    pnl_pct: 0,
    positions: [],
  })
})


test('position valuation keeps exact provider quote provenance', () => {
  const state = buildPortfolioState([
    {
      cash: '15000',
      ticker: 'VOLV-B',
      shares: '10',
      avg_price: '300',
      current_price: '321.5',
      price_source: 'licensed-provider-delayed',
      price_event_time: '2026-07-29T09:45:00.000Z',
      price_received_at: '2026-07-29T10:00:00.000Z',
      provider_ready: true,
      company_name: 'Volvo B',
      sector: 'Industrials',
      rsi: '55',
      sma20: '310',
      confidence: '75',
      reasoning: 'Momentum',
      hypothesis: 'Fortsatt uppgång',
      opened_at: '2026-07-20T08:00:00.000Z',
      target_price: '330',
      stop_loss: '290',
      days_held: 9,
    },
  ])

  assert.equal(state.positions_value, 3215)
  assert.equal(state.total_value, 18215)
  assert.equal(state.positions[0].pnl_kr, 215)
  assert.equal(state.positions[0].price_source, 'licensed-provider-delayed')
  assert.equal(
    state.positions[0].price_event_time,
    '2026-07-29T09:45:00.000Z',
  )
})


test('open position without authorized quote fails closed', () => {
  assert.throws(
    () => buildPortfolioState([
      {
        cash: '19000',
        ticker: 'VOLV-B',
        shares: '1',
        avg_price: '300',
        current_price: null,
        price_source: null,
        provider_ready: false,
      },
    ]),
    /authorized provider quote/,
  )
})


test('database loader never queries legacy daily prices', async () => {
  const queries = []
  const pool = {
    async query(query) {
      queries.push(query)
      return {
        rows: [{
          cash: '20000',
          ticker: null,
          provider_ready: false,
        }],
      }
    },
  }

  await loadPortfolioState(pool)

  assert.equal(queries.length, 1)
  assert.match(queries[0], /market_quotes/)
  assert.doesNotMatch(queries[0], /\bFROM prices\b/)
})
