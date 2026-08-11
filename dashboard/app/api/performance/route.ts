import { NextResponse } from 'next/server'

import {
  databaseUnavailable,
  getDatabasePool,
} from '@/lib/server/database'

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const pool = getDatabasePool()
    const stats = await pool.query(`
      SELECT 
        COUNT(*) as total_trades,
        COUNT(*) FILTER (WHERE action = 'BUY') as buys,
        COUNT(*) FILTER (WHERE action = 'SELL') as sells,
        COUNT(*) FILTER (
          WHERE action = 'SELL' AND pnl IS NOT NULL
        ) as closed_sells,
        COUNT(*) FILTER (WHERE action = 'SELL' AND pnl > 0) as winners,
        COUNT(*) FILTER (WHERE action = 'SELL' AND pnl < 0) as losers,
        COALESCE(AVG(pnl) FILTER (
          WHERE action = 'SELL' AND pnl > 0
        ), 0) as avg_win,
        COALESCE(AVG(pnl) FILTER (
          WHERE action = 'SELL' AND pnl < 0
        ), 0) as avg_loss,
        COALESCE(SUM(pnl) FILTER (WHERE action = 'SELL'), 0) as total_pnl,
        COALESCE(MAX(pnl) FILTER (WHERE action = 'SELL'), 0) as best_trade,
        COALESCE(MIN(pnl) FILTER (WHERE action = 'SELL'), 0) as worst_trade,
        COALESCE(AVG(confidence), 0) as avg_confidence
      FROM trades
    `)

    const row = stats.rows[0]
    const totalClosed = parseInt(row.closed_sells)
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
    return databaseUnavailable('fetch performance', error)
  }
}
