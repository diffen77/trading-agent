import { NextResponse } from 'next/server';

import {
  databaseUnavailable,
  getDatabasePool,
} from '@/lib/server/database';
import {
  AUTHORIZED_MARKET_DATA_CTES,
} from '@/lib/server/authorized-market-data.mjs';

export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const pool = getDatabasePool();
    const result = await pool.query(`
      WITH ${AUTHORIZED_MARKET_DATA_CTES}
      SELECT 
        p.id,
        p.ticker,
        p.name,
        p.thesis,
        p.target_price,
        quote.last_price as current_price,
        p.entry_trigger,
        p.confidence,
        p.status,
        p.priority,
        p.policy_version,
        p.source_book_state_id,
        p.evidence_at,
        p.added_at,
        p.updated_at,
        quote.last_price as latest_price,
        quote.event_time as price_date,
        quote.received_at as price_received_at,
        quote.source as price_source
      FROM prospects p
      LEFT JOIN companies company ON company.ticker = p.ticker
      LEFT JOIN latest_authorized_quotes quote
        ON quote.instrument_id = company.instrument_id
      WHERE p.status = 'watching'
        AND p.is_current = TRUE
      ORDER BY p.priority ASC, p.confidence DESC
    `);
    
    return NextResponse.json(result.rows);
  } catch (error) {
    return databaseUnavailable('fetch prospects', error);
  }
}
