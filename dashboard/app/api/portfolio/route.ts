import { NextResponse } from 'next/server'
import { Pool } from 'pg'

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
})

export async function GET() {
  try {
    // Get balance
    const balanceResult = await pool.query(
      'SELECT cash, total_value FROM balance ORDER BY id DESC LIMIT 1'
    )
    
    const balance = balanceResult.rows[0] || { cash: 20000, total_value: 20000 }
    
    // Get portfolio positions value
    const portfolioResult = await pool.query(`
      SELECT SUM(p.shares * pr.close) as positions_value
      FROM portfolio p
      JOIN (
        SELECT DISTINCT ON (ticker) ticker, close
        FROM prices
        ORDER BY ticker, date DESC
      ) pr ON p.ticker = pr.ticker
    `)
    
    const positionsValue = parseFloat(portfolioResult.rows[0]?.positions_value || 0)
    const totalValue = parseFloat(balance.cash) + positionsValue
    
    return NextResponse.json({
      cash: parseFloat(balance.cash),
      positions_value: positionsValue,
      total_value: totalValue,
      pnl: totalValue - 20000,
      pnl_pct: ((totalValue / 20000) - 1) * 100,
    })
  } catch (error) {
    console.error('Error fetching portfolio:', error)
    return NextResponse.json({
      cash: 20000,
      positions_value: 0,
      total_value: 20000,
      pnl: 0,
      pnl_pct: 0,
    })
  }
}
