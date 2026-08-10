import { NextResponse } from 'next/server'

import {
  databaseUnavailable,
  getDatabasePool,
} from '@/lib/server/database'
import {
  AUTHORIZED_MARKET_DATA_CTES,
} from '@/lib/server/authorized-market-data.mjs'

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const pool = getDatabasePool()
    const result = await pool.query(`
      WITH ${AUTHORIZED_MARKET_DATA_CTES}
      SELECT
        company.ticker,
        quote.last_price AS price,
        quote.event_time AS date,
        quote.received_at,
        quote.source,
        company.name,
        company.sector
      FROM companies company
      JOIN instruments instrument
        ON instrument.id = company.instrument_id
       AND instrument.mic = 'XSTO'
       AND instrument.status = 'ACTIVE'
      JOIN latest_authorized_quotes quote
        ON quote.instrument_id = instrument.id
      ORDER BY company.ticker
    `)
    
    return NextResponse.json(result.rows)
  } catch (error) {
    return databaseUnavailable('fetch stocks', error)
  }
}
