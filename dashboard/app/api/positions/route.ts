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
    return NextResponse.json(state.positions)
  } catch (error) {
    return databaseUnavailable('fetch positions', error)
  }
}
