import { NextResponse } from 'next/server'
import { Pool } from 'pg'

const pool = new Pool({ connectionString: process.env.DATABASE_URL })

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const stats = await pool.query(`
      SELECT 
        COUNT(*) as total_trades,
        COUNT(CASE WHEN action = 'BUY' THEN 1 END) as buys,
        COUNT(CASE WHEN action = 'SELL' THEN 1 END) as sells,
        COUNT(CASE WHEN pnl > 0 THEN 1 END) as winners,
        COUNT(CASE WHEN pnl < 0 THEN 1 END) as losers,
        COALESCE(AVG(CASE WHEN pnl > 0 THEN pnl END), 0) as avg_win,
        COALESCE(AVG(CASE WHEN pnl < 0 THEN pnl END), 0) as avg_loss,
        COALESCE(SUM(pnl), 0) as total_pnl,
        COALESCE(MAX(pnl), 0) as best_trade,
        COALESCE(MIN(pnl), 0) as worst_trade,
        COALESCE(AVG(confidence), 0) as avg_confidence
      FROM trades
      WHERE closed_at IS NOT NULL OR pnl IS NOT NULL
    `)

    const row = stats.rows[0]
    const totalClosed = parseInt(row.winners) + parseInt(row.losers)
    const winRate = totalClosed > 0 ? (parseInt(row.winners) / totalClosed) * 100 : 0

    // Get open positions count
    const openPos = await pool.query(`
      SELECT COUNT(DISTINCT ticker) as open_positions FROM portfolio WHERE shares > 0
    `)

    return NextResponse.json({
      total_trades: parseInt(row.total_trades),
      buys: parseInt(row.buys),
      sells: parseInt(row.sells),
      winners: parseInt(row.winners),
      losers: parseInt(row.losers),
      win_rate: winRate,
      avg_win: parseFloat(row.avg_win),
      avg_loss: parseFloat(row.avg_loss),
      total_pnl: parseFloat(row.total_pnl),
      best_trade: parseFloat(row.best_trade),
      worst_trade: parseFloat(row.worst_trade),
      avg_confidence: parseFloat(row.avg_confidence),
      open_positions: parseInt(openPos.rows[0]?.open_positions || 0),
    })
  } catch (error) {
    console.error('Error fetching performance:', error)
    return NextResponse.json({
      total_trades: 0, buys: 0, sells: 0, winners: 0, losers: 0,
      win_rate: 0, avg_win: 0, avg_loss: 0, total_pnl: 0,
      best_trade: 0, worst_trade: 0, avg_confidence: 0, open_positions: 0,
    })
  }
}
