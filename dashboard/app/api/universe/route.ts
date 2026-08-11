import { NextResponse } from 'next/server'

import {
  databaseUnavailable,
  getDatabasePool,
} from '@/lib/server/database'
import {
  loadUniverseStatus,
} from '@/lib/server/universe-status.mjs'

export const dynamic = 'force-dynamic'

function positiveInteger(value: string | null, fallback: number) {
  if (value == null) return fallback
  if (!/^[1-9]\d*$/.test(value)) return null

  const parsed = Number(value)
  return Number.isSafeInteger(parsed) ? parsed : null
}

export async function GET(request: Request) {
  const url = new URL(request.url)
  const page = positiveInteger(url.searchParams.get('page'), 1)
  const pageSize = positiveInteger(url.searchParams.get('pageSize'), 20)

  if (page == null || pageSize == null || pageSize > 100) {
    return NextResponse.json(
      { error: 'Invalid pagination' },
      {
        status: 400,
        headers: { 'Cache-Control': 'no-store' },
      },
    )
  }

  try {
    const universe = await loadUniverseStatus(
      getDatabasePool(),
      { page, pageSize },
    )
    return NextResponse.json(universe, {
      headers: { 'Cache-Control': 'no-store' },
    })
  } catch (error) {
    return databaseUnavailable('fetch universe status', error)
  }
}
