import { NextResponse } from 'next/server'

import { getDatabasePool } from '@/lib/server/database'
import {
  REQUIRED_SCHEMA_VERSION,
} from '@/lib/server/operational-status.mjs'

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const result = await getDatabasePool().query(
      'SELECT COALESCE(MAX(version), 0)::int AS version FROM schema_migrations',
    )
    if (result.rows[0]?.version !== REQUIRED_SCHEMA_VERSION) {
      return NextResponse.json(
        { status: 'unhealthy' },
        { status: 503, headers: { 'Cache-Control': 'no-store' } },
      )
    }
    return NextResponse.json(
      { status: 'ok' },
      { headers: { 'Cache-Control': 'no-store' } },
    )
  } catch {
    return NextResponse.json(
      { status: 'unhealthy' },
      { status: 503, headers: { 'Cache-Control': 'no-store' } },
    )
  }
}
