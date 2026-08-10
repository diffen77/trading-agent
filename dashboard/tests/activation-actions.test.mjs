import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildActivationActions,
} from '../lib/server/activation-actions.mjs'


function blockedStatus() {
  return {
    overall_status: 'BLOCKED',
    blockers: [
      'REFERENCE_ENTITLEMENT_NOT_READY',
      'TICKER_MAPPING_INCOMPLETE',
      'QUOTE_PROVIDER_NOT_READY',
      'INDEX_PROVIDER_NOT_READY',
      'FORWARD_BENCHMARK_NOT_REGISTERED',
    ],
    universe: {
      expected_instruments: 416,
      provider_aliases: 0,
      company_mappings: 0,
    },
    data: {
      provider: null,
      reference_entitlement: null,
      fresh_instruments: 0,
    },
    index: null,
    benchmark: null,
  }
}


test('activation actions follow blocker dependency order', () => {
  const actions = buildActivationActions(blockedStatus())

  assert.deepEqual(
    actions.map(action => action.code),
    [
      'REFERENCE_ACCESS',
      'QUOTE_GOVERNANCE',
      'INSTRUMENT_MAPPING',
      'PROVIDER_ACCEPTANCE',
      'INDEX_ACCESS',
      'FORWARD_PAPER',
    ],
  )
  assert.equal(actions[0].status, 'ACTION_REQUIRED')
  assert.equal(actions[0].owner, 'Du + Nasdaq')
  assert.equal(actions[1].status, 'ACTION_REQUIRED')
  assert.equal(actions[2].status, 'WAITING')
  assert.equal(actions[3].status, 'WAITING')
  assert.equal(actions[4].status, 'ACTION_REQUIRED')
  assert.equal(actions[5].status, 'WAITING')
})


test('activation actions expose truthful automatic progress', () => {
  const status = blockedStatus()
  status.universe.provider_aliases = 416
  status.universe.company_mappings = 416
  status.data.reference_entitlement = { entitlement_ready: true }
  status.data.provider = {
    contract_ready: true,
    validation_ready: false,
    acceptance_session_count: 2,
  }
  status.index = {
    contract_ready: true,
    validation_ready: false,
  }

  const actions = buildActivationActions(status)
  const mapping = actions.find(
    action => action.code === 'INSTRUMENT_MAPPING',
  )
  const acceptance = actions.find(
    action => action.code === 'PROVIDER_ACCEPTANCE',
  )
  const index = actions.find(action => action.code === 'INDEX_ACCESS')

  assert.equal(mapping.status, 'READY')
  assert.deepEqual(mapping.progress, { current: 416, required: 416 })
  assert.equal(acceptance.status, 'RUNNING')
  assert.deepEqual(acceptance.progress, { current: 2, required: 5 })
  assert.equal(index.status, 'ACTION_REQUIRED')
})


test('paper action only completes after the frozen benchmark passes', () => {
  const status = blockedStatus()
  status.universe.provider_aliases = 416
  status.universe.company_mappings = 416
  status.data.reference_entitlement = { entitlement_ready: true }
  status.data.provider = {
    contract_ready: true,
    validation_ready: true,
    acceptance_session_count: 5,
  }
  status.index = {
    contract_ready: true,
    validation_ready: true,
  }
  status.benchmark = {
    status: 'RUNNING',
    passed: null,
    trading_sessions: 12,
    min_trading_sessions: 252,
  }

  let paper = buildActivationActions(status).find(
    action => action.code === 'FORWARD_PAPER',
  )
  assert.equal(paper.status, 'RUNNING')
  assert.deepEqual(paper.progress, { current: 12, required: 252 })

  status.benchmark.status = 'PASSED'
  status.benchmark.passed = true
  status.benchmark.trading_sessions = 252
  paper = buildActivationActions(status).find(
    action => action.code === 'FORWARD_PAPER',
  )
  assert.equal(paper.status, 'READY')
})


test('validation evidence cannot bypass its provider contract', () => {
  const status = blockedStatus()
  status.data.provider = {
    contract_ready: false,
    validation_ready: true,
    acceptance_session_count: 5,
  }
  status.index = {
    contract_ready: false,
    validation_ready: true,
  }

  const actions = buildActivationActions(status)

  assert.equal(
    actions.find(action => action.code === 'PROVIDER_ACCEPTANCE').status,
    'WAITING',
  )
  assert.equal(
    actions.find(action => action.code === 'INDEX_ACCESS').status,
    'ACTION_REQUIRED',
  )
})


test('prerequisites alone do not claim that automation is running', () => {
  const status = blockedStatus()
  status.data.reference_entitlement = { entitlement_ready: true }
  status.data.provider = {
    contract_ready: true,
    validation_ready: false,
    acceptance_session_count: 0,
  }

  let actions = buildActivationActions(status)
  assert.equal(
    actions.find(action => action.code === 'INSTRUMENT_MAPPING').status,
    'ACTION_REQUIRED',
  )

  status.universe.provider_aliases = 416
  status.universe.company_mappings = 416
  actions = buildActivationActions(status)
  assert.equal(
    actions.find(action => action.code === 'PROVIDER_ACCEPTANCE').status,
    'ACTION_REQUIRED',
  )
})
