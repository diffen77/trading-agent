import { NextResponse } from 'next/server'
import { Pool } from 'pg'

const pool = new Pool({ connectionString: process.env.DATABASE_URL })

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const result = await pool.query(`
      SELECT 
        p.ticker,
        p.shares,
        p.avg_price,
        pr.close as current_price,
        pr.date as price_date,
        c.name as company_name,
        c.sector,
        ts.rsi,
        ts.sma20,
        t.confidence,
        t.reasoning,
        t.hypothesis,
        t.executed_at as opened_at,
        t.target_price,
        t.stop_loss,
        ROUND(((pr.close - p.avg_price) / p.avg_price * 100)::numeric, 2) as pnl_pct,
        ROUND((p.shares * (pr.close - p.avg_price))::numeric, 2) as pnl_kr,
        ROUND((p.shares * pr.close)::numeric, 2) as market_value,
        EXTRACT(DAY FROM NOW() - t.executed_at)::int as days_held
      FROM portfolio p
      LEFT JOIN LATERAL (
        SELECT close, date FROM prices WHERE ticker = p.ticker ORDER BY date DESC LIMIT 1
      ) pr ON true
      LEFT JOIN companies c ON p.ticker = c.ticker
      LEFT JOIN LATERAL (
        SELECT rsi, sma20 FROM technical_signals WHERE ticker = p.ticker ORDER BY date DESC LIMIT 1
      ) ts ON true
      LEFT JOIN LATERAL (
        SELECT confidence, reasoning, hypothesis, executed_at, target_price, stop_loss
        FROM trades WHERE ticker = p.ticker AND action = 'BUY' ORDER BY executed_at DESC LIMIT 1
      ) t ON true
      WHERE p.shares > 0
      ORDER BY (p.shares * pr.close) DESC
    `)

    return NextResponse.json(result.rows)
  } catch (error) {
    console.error('Error fetching positions:', error)
    return NextResponse.json([])
  }
}
