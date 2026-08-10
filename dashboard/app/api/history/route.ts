import { NextResponse } from 'next/server'

import {
  databaseUnavailable,
  getDatabasePool,
} from '@/lib/server/database'
import {
  loadPortfolioState,
} from '@/lib/server/portfolio-valuation.mjs'

export const dynamic = 'force-dynamic'

export async function GET(request: Request) {
  try {
    const pool = getDatabasePool()
    const { searchParams } = new URL(request.url)
    const period = searchParams.get('period') || '1M'

    let interval: string
    switch (period) {
      case '1D': interval = '1 day'; break
      case '1W': interval = '7 days'; break
      case '1M': interval = '1 month'; break
      case 'YTD': interval = '1 year'; break
      case '1Y': interval = '1 year'; break
      default: interval = '1 month'
    }

    const dateFilter = period === 'YTD'
      ? "recorded_at >= date_trunc('year', CURRENT_DATE)"
      : `recorded_at >= NOW() - INTERVAL '${interval}'`

    // Deduplicate by day - take last snapshot per day
    const result = await pool.query(`
      SELECT DISTINCT ON (recorded_at::date)
        total_value, cash, positions_value, recorded_at
      FROM portfolio_history
      WHERE ${dateFilter}
      ORDER BY recorded_at::date, recorded_at DESC
    `)

    if (result.rows.length === 0) {
      const baseline = await loadPortfolioState(pool)
      return NextResponse.json([{
        total_value: baseline.total_value,
        cash: baseline.cash,
        positions_value: baseline.positions_value,
        recorded_at: new Date().toISOString(),
      }])
    }

    return NextResponse.json(result.rows)
  } catch (error) {
    return databaseUnavailable('fetch portfolio history', error)
  }
}
