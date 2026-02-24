import { NextResponse } from 'next/server'
import { Pool } from 'pg'

const pool = new Pool({ connectionString: process.env.DATABASE_URL })

export const dynamic = 'force-dynamic'

export async function GET(request: Request) {
  try {
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
      // Return baseline
      const baseline = await pool.query(`
        SELECT 
          b.cash + COALESCE(SUM(p.shares * pr.close), 0) as total_value,
          b.cash,
          COALESCE(SUM(p.shares * pr.close), 0) as positions_value,
          NOW() as recorded_at
        FROM balance b
        LEFT JOIN portfolio p ON true
        LEFT JOIN LATERAL (
          SELECT close FROM prices WHERE ticker = p.ticker ORDER BY date DESC LIMIT 1
        ) pr ON true
        GROUP BY b.cash
      `)
      return NextResponse.json(baseline.rows)
    }

    return NextResponse.json(result.rows)
  } catch (error) {
    console.error('Error fetching history:', error)
    return NextResponse.json([])
  }
}
