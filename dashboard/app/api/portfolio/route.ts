import { NextResponse } from 'next/server'

import {
  databaseUnavailable,
  getDatabasePool,
} from '@/lib/server/database'
import { loadPortfolioState } from '@/lib/server/portfolio-valuation.mjs'

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const state = await loadPortfolioState(getDatabasePool())
    const { positions: _positions, ...summary } = state
    return NextResponse.json(summary)
  } catch (error) {
    return databaseUnavailable('fetch portfolio', error)
  }
}
