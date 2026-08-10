import { NextResponse } from 'next/server'

import {
  databaseUnavailable,
  getDatabasePool,
} from '@/lib/server/database'
import {
  loadOperationalStatus,
} from '@/lib/server/operational-status.mjs'

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const status = await loadOperationalStatus(getDatabasePool())
    const signal = status.index
    return NextResponse.json(
      signal?.symbol === 'OMXSGI'
        ? [{
            symbol: signal.symbol,
            type: 'index',
            value: signal.current_level,
            change_pct: signal.change_pct,
            date: signal.event_time,
          }]
        : [],
      { headers: { 'Cache-Control': 'no-store' } },
    )
  } catch (error) {
    return databaseUnavailable('fetch authorized market index', error)
  }
}
