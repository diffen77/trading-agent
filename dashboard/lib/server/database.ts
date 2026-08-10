import { NextResponse } from 'next/server'
import { Pool } from 'pg'

declare global {
  // eslint-disable-next-line no-var
  var tradingAgentDashboardPool: Pool | undefined
}

export function getDatabasePool() {
  const connectionString = process.env.DATABASE_URL
  if (!connectionString) {
    throw new Error('DATABASE_URL is not configured')
  }

  if (!globalThis.tradingAgentDashboardPool) {
    globalThis.tradingAgentDashboardPool = new Pool({
      connectionString,
      max: 10,
      connectionTimeoutMillis: 5000,
      idleTimeoutMillis: 30000,
      statement_timeout: 5000,
    })
  }
  return globalThis.tradingAgentDashboardPool
}

export function databaseUnavailable(operation: string, error: unknown) {
  const detail = error instanceof Error ? error.message : 'unknown error'
  console.error(`Database operation failed (${operation}): ${detail}`)
  return NextResponse.json(
    { error: 'Service temporarily unavailable' },
    {
      status: 503,
      headers: {
        'Cache-Control': 'no-store',
        'Retry-After': '30',
      },
    },
  )
}
