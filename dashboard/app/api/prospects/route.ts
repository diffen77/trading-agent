import { NextResponse } from 'next/server';
import { Pool } from 'pg';

const pool = new Pool({
  host: process.env.DB_HOST || 'trading-agent-db',
  port: parseInt(process.env.DB_PORT || '5432'),
  database: process.env.DB_NAME || 'trading_agent',
  user: process.env.DB_USER || 'trading',
  password: process.env.DB_PASSWORD || 'trading_dev_123',
});

export async function GET() {
  try {
    const result = await pool.query(`
      SELECT 
        p.id,
        p.ticker,
        p.name,
        p.thesis,
        p.target_price,
        p.current_price,
        p.entry_trigger,
        p.confidence,
        p.status,
        p.priority,
        p.added_at,
        p.updated_at,
        pr.close as latest_price,
        pr.date as price_date
      FROM prospects p
      LEFT JOIN LATERAL (
        SELECT close, date FROM prices 
        WHERE ticker = p.ticker 
        ORDER BY date DESC LIMIT 1
      ) pr ON true
      WHERE p.status = 'watching'
      ORDER BY p.priority ASC, p.confidence DESC
    `);
    
    return NextResponse.json(result.rows);
  } catch (error) {
    console.error('Error fetching prospects:', error);
    return NextResponse.json({ error: 'Failed to fetch prospects' }, { status: 500 });
  }
}
