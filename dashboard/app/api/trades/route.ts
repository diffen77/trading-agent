import { NextResponse } from 'next/server'

import {
  databaseUnavailable,
  getDatabasePool,
} from '@/lib/server/database'

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const pool = getDatabasePool()
    const result = await pool.query(`
      SELECT
        trade.id,
        trade.ticker,
        company.name AS company_name,
        trade.action,
        trade.shares,
        trade.price,
        trade.total_value,
        trade.reasoning,
        trade.confidence,
        trade.hypothesis,
        trade.executed_at,
        trade.pnl,
        trade.target_price,
        trade.stop_loss,
        trade.target_pct,
        trade.stop_loss_pct,
        ROUND(
          trade.price * (1 + trade.target_pct / 100),
          2
        ) AS target_level,
        ROUND(
          trade.price * (1 + trade.stop_loss_pct / 100),
          2
        ) AS stop_loss_level,
        trade.closed_at,
        trade.outcome,
        trade.strategy_version,
        trade.decision_id,
        trade.decision_origin,
        trade.source_quote_id,
        trade.source_book_state_id,
        decision.timestamp AS decision_timestamp,
        decision.strategy_config_hash,
        COALESCE(
          quote.event_time,
          CASE
            WHEN trade.action = 'BUY' THEN book.ask_event_time
            ELSE book.bid_event_time
          END
        ) AS quote_event_time,
        COALESCE(quote.received_at, book.received_at)
          AS quote_received_at,
        COALESCE(
          source_file.checksum_sha256,
          source_batch.source_checksum_sha256
        ) AS source_file_checksum,
        CASE
          WHEN trade.source_book_state_id IS NOT NULL
            THEN 'PRE_TRADE_BOOK'
          WHEN trade.source_quote_id IS NOT NULL
            THEN 'LAST_TRADE_QUOTE'
          ELSE NULL
        END AS source_evidence_type
      FROM trades trade
      LEFT JOIN companies company
        ON company.ticker = trade.ticker
      LEFT JOIN ai_decisions decision
        ON decision.id = trade.decision_id
      LEFT JOIN market_quotes quote
        ON quote.id = trade.source_quote_id
      LEFT JOIN market_data_files source_file
        ON source_file.id = quote.data_file_id
      LEFT JOIN pre_trade_book_states book
        ON book.id = trade.source_book_state_id
      LEFT JOIN pre_trade_batches source_batch
        ON source_batch.id = book.batch_id
      ORDER BY trade.executed_at DESC, trade.id DESC
      LIMIT 50
    `)
    
    return NextResponse.json(result.rows)
  } catch (error) {
    return databaseUnavailable('fetch trades', error)
  }
}
