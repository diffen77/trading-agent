import { NextResponse } from 'next/server'
import { Pool } from 'pg'

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
})

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const result = await pool.query(`
      SELECT id, ticker, action, shares, price, total_value, 
             reasoning, confidence, hypothesis, executed_at, pnl,
             target_price, stop_loss, target_pct, stop_loss_pct,
             ROUND(price * (1 + target_pct/100), 2) as target_level,
             ROUND(price * (1 + stop_loss_pct/100), 2) as stop_loss_level,
             closed_at, outcome
      FROM trades
      ORDER BY executed_at DESC
      LIMIT 50
    `)
    
    return NextResponse.json(result.rows)
  } catch (error) {
    console.error('Error fetching trades:', error)
    return NextResponse.json([])
  }
}
