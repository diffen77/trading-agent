import assert from 'node:assert/strict'
import test from 'node:test'

import pg from 'pg'

import {
  loadOperationalStatus,
} from '../lib/server/operational-status.mjs'
import {
  loadPortfolioState,
} from '../lib/server/portfolio-valuation.mjs'
import {
  AUTHORIZED_MARKET_DATA_CTES,
} from '../lib/server/authorized-market-data.mjs'


const TEST_DATABASE_URL = process.env.TEST_DATABASE_URL


test(
  'operational status query fails closed against schema 44',
  { skip: !TEST_DATABASE_URL },
  async () => {
    const pool = new pg.Pool({
      connectionString: TEST_DATABASE_URL,
      max: 1,
    })
    try {
      const status = await loadOperationalStatus(pool)

      assert.equal(status.schema_version, 44)
      assert.equal(status.overall_status, 'BLOCKED')
      assert.ok(status.blockers.includes('REFERENCE_SNAPSHOT_MISSING'))
      assert.ok(status.blockers.includes('QUOTE_PROVIDER_NOT_READY'))
      assert.ok(status.blockers.includes('XSTO_SESSION_NOT_OPEN'))
      assert.equal(
        status.activation_actions[0].code,
        'REFERENCE_ACCESS',
      )
      assert.ok(Array.isArray(status.monitoring.active_alerts))
      assert.ok(Array.isArray(status.monitoring.scheduled_routines))
    } finally {
      await pool.end()
    }
  },
)


test(
  'authorized dashboard quote view executes against schema 44',
  { skip: !TEST_DATABASE_URL },
  async () => {
    const pool = new pg.Pool({
      connectionString: TEST_DATABASE_URL,
      max: 1,
    })
    try {
      const result = await pool.query(`
        WITH ${AUTHORIZED_MARKET_DATA_CTES}
        SELECT COUNT(*)::int AS quote_count
        FROM latest_authorized_quotes
      `)

      assert.equal(typeof result.rows[0].quote_count, 'number')
    } finally {
      await pool.end()
    }
  },
)


test(
  'operational status exposes only current alert state and latest routine outcome',
  { skip: !TEST_DATABASE_URL },
  async () => {
    const pool = new pg.Pool({
      connectionString: TEST_DATABASE_URL,
      max: 1,
    })
    const client = await pool.connect()
    try {
      await client.query('BEGIN')
      await client.query(`
        INSERT INTO operational_alert_events (
          alert_key,
          code,
          severity,
          state,
          summary,
          runbook,
          observed_at
        )
        VALUES
          (
            'test:resolved',
            'LEDGER_INVARIANT_FAILED',
            'PAGE',
            'OPEN',
            'Tillfälligt ledgerfel.',
            'docs/operations.md',
            NOW() - INTERVAL '3 minutes'
          ),
          (
            'test:resolved',
            'LEDGER_INVARIANT_FAILED',
            'PAGE',
            'RESOLVED',
            'Ledgerkontrollen är återställd.',
            'docs/operations.md',
            NOW() - INTERVAL '2 minutes'
          ),
          (
            'test:open',
            'SCHEDULED_ROUTINE_MISSED',
            'PAGE',
            'OPEN',
            'Schemakörningen saknar lyckat utfall.',
            'docs/operations.md',
            NOW() - INTERVAL '1 minute'
          )
      `)
      await client.query(`
        INSERT INTO scheduled_routine_events (
          routine_key,
          routine_name,
          scheduled_at,
          status,
          failure_code,
          observed_at
        )
        VALUES
          (
            '2026-07-29:open',
            'open',
            '2026-07-29T07:00:00Z',
            'FAILED',
            'MARKET_DATA_NOT_READY',
            '2026-07-29T07:01:00Z'
          ),
          (
            '2026-07-29:open',
            'open',
            '2026-07-29T07:00:00Z',
            'SUCCEEDED',
            NULL,
            '2026-07-29T07:02:00Z'
          )
      `)

      const status = await loadOperationalStatus(client)

      assert.deepEqual(
        status.monitoring.active_alerts
          .filter(alert => alert.alert_key.startsWith('test:'))
          .map(alert => alert.alert_key),
        ['test:open'],
      )
      assert.ok(status.blockers.includes('SCHEDULED_ROUTINE_MISSED'))
      const routine = status.monitoring.scheduled_routines.find(
        candidate => candidate.routine_key === '2026-07-29:open',
      )
      assert.ok(routine)
      assert.equal(
        routine.status,
        'SUCCEEDED',
      )
    } finally {
      await client.query('ROLLBACK')
      client.release()
      await pool.end()
    }
  },
)


test(
  'cash-only portfolio query works without legacy prices',
  { skip: !TEST_DATABASE_URL },
  async () => {
    const pool = new pg.Pool({
      connectionString: TEST_DATABASE_URL,
      max: 1,
    })
    try {
      const state = await loadPortfolioState(pool)

      assert.equal(state.cash, 20000)
      assert.equal(state.total_value, 20000)
      assert.deepEqual(state.positions, [])
    } finally {
      await pool.end()
    }
  },
)


test(
  'legacy daily price cannot value an open position',
  { skip: !TEST_DATABASE_URL },
  async () => {
    const pool = new pg.Pool({
      connectionString: TEST_DATABASE_URL,
      max: 1,
    })
    const client = await pool.connect()
    try {
      await client.query('BEGIN')
      await client.query(`
        INSERT INTO companies (ticker, name)
        VALUES ('VOLV-B', 'Volvo B')
        ON CONFLICT (ticker) DO NOTHING
      `)
      await client.query(`
        INSERT INTO prices (ticker, date, close)
        VALUES ('VOLV-B', CURRENT_DATE, 321.5)
        ON CONFLICT (ticker, date) DO NOTHING
      `)
      await client.query(`
        INSERT INTO portfolio (ticker, shares, avg_price)
        VALUES ('VOLV-B', 1, 300)
      `)

      await assert.rejects(
        loadPortfolioState(client),
        /authorized provider quote/,
      )
    } finally {
      await client.query('ROLLBACK')
      client.release()
      await pool.end()
    }
  },
)
