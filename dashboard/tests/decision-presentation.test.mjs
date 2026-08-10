import assert from 'node:assert/strict'
import test from 'node:test'

import {
  actionLabel,
  collectDecisionIdentifiers,
  decisionActionPresentation,
  decisionCompanyLabel,
} from '../lib/decision-presentation.mjs'

test('analysis decisions collect unique identifiers for company lookup', () => {
  const rows = [
    {
      decisions_json: JSON.stringify({
        decisions: [
          { ticker: 'SE0000936478', action: 'BUY' },
          { ticker: 'SE0010415281', action: 'BUY' },
        ],
      }),
    },
    {
      decisions_json: {
        decisions: [
          { ticker: 'SE0000936478', action: 'SELL' },
          { ticker: 'Boule Diagnostics AB', action: 'BUY' },
        ],
      },
    },
  ]

  assert.deepEqual(
    collectDecisionIdentifiers(rows),
    ['SE0000936478', 'SE0010415281', 'Boule Diagnostics AB'],
  )
})

test('analysis decisions show company names instead of raw ISIN values', () => {
  const names = {
    SE0000936478: 'Intrum AB',
    SE0010415281: 'Anoto Group AB',
  }

  assert.equal(decisionCompanyLabel('SE0000936478', names), 'Intrum AB')
  assert.equal(decisionCompanyLabel('SE0010415281', names), 'Anoto Group AB')
  assert.equal(
    decisionCompanyLabel('SE0009160872', names),
    'Bolagsnamn saknas',
  )
  assert.equal(
    decisionCompanyLabel('Boule Diagnostics AB', names),
    'Boule Diagnostics AB',
  )
})

test('analysis actions use Swedish visitor labels', () => {
  assert.equal(actionLabel('BUY'), 'KÖP')
  assert.equal(actionLabel('SELL'), 'SÄLJ')
  assert.equal(actionLabel('HOLD'), 'AVVAKTA')
})

test('an unexecuted buy proposal is presented as wait', () => {
  assert.deepEqual(
    decisionActionPresentation({
      action: 'BUY',
      ticker: 'SE0000565228',
      predictionCount: 20,
      executedTrades: [],
    }),
    {
      label: 'AVVAKTA',
      detail: 'Modellförslag: KÖP · inte genomfört',
      status: 'PROPOSED',
      tone: 'wait',
    },
  )
})

test('a proposal without prediction evidence is presented as unverified', () => {
  assert.deepEqual(
    decisionActionPresentation({
      action: 'BUY',
      ticker: 'SE0017486889',
      predictionCount: 0,
      executedTrades: [],
    }),
    {
      label: 'AVVAKTA',
      detail: 'Analysen kunde inte verifieras',
      status: 'UNVERIFIED',
      tone: 'wait',
    },
  )
})

test('only a matching executed trade is presented as bought or sold', () => {
  const executedTrades = [
    { ticker: 'SE0000936478', action: 'BUY' },
    { ticker: 'SE0010415281', action: 'SELL' },
  ]

  assert.equal(
    decisionActionPresentation({
      action: 'BUY',
      ticker: 'SE0000936478',
      predictionCount: 20,
      executedTrades,
    }).label,
    'KÖPT',
  )
  assert.equal(
    decisionActionPresentation({
      action: 'SELL',
      ticker: 'SE0010415281',
      predictionCount: 20,
      executedTrades,
    }).label,
    'SÅLT',
  )
  assert.equal(
    decisionActionPresentation({
      action: 'BUY',
      ticker: 'SE0010415281',
      predictionCount: 20,
      executedTrades,
    }).label,
    'AVVAKTA',
  )
})

test('a hold decision remains wait without implying a trade', () => {
  assert.deepEqual(
    decisionActionPresentation({
      action: 'HOLD',
      ticker: 'SE0000936478',
      predictionCount: 20,
      executedTrades: [],
    }),
    {
      label: 'AVVAKTA',
      detail: null,
      status: 'HOLD',
      tone: 'neutral',
    },
  )
})
