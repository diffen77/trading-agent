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
    return NextResponse.json(status, {
      headers: { 'Cache-Control': 'no-store' },
    })
  } catch (error) {
    return databaseUnavailable('fetch operational status', error)
  }
}
