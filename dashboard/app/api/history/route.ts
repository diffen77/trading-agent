import { NextResponse } from 'next/server';
import { Pool } from 'pg';

const pool = new Pool({
  host: process.env.DB_HOST || 'trading-agent-db',
  port: parseInt(process.env.DB_PORT || '5432'),
  database: process.env.DB_NAME || 'trading_agent',
  user: process.env.DB_USER || 'trading',
  password: process.env.DB_PASSWORD || 'trading_dev_123',
});

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const period = searchParams.get('period') || '1M';
    
    // Calculate date range based on period
    let interval: string;
    switch (period) {
      case '1D': interval = '1 day'; break;
      case '1W': interval = '7 days'; break;
      case '1M': interval = '1 month'; break;
      case 'YTD': interval = '1 year'; break; // Will filter by year start
      case '1Y': interval = '1 year'; break;
      default: interval = '1 month';
    }
    
    let query: string;
    if (period === 'YTD') {
      query = `
        SELECT total_value, cash, positions_value, recorded_at
        FROM portfolio_history
        WHERE recorded_at >= date_trunc('year', CURRENT_DATE)
        ORDER BY recorded_at ASC
      `;
    } else {
      query = `
        SELECT total_value, cash, positions_value, recorded_at
        FROM portfolio_history
        WHERE recorded_at >= NOW() - INTERVAL '${interval}'
        ORDER BY recorded_at ASC
      `;
    }
    
    const result = await pool.query(query);
    
    // If no history, create a baseline
    if (result.rows.length === 0) {
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
      `);
      return NextResponse.json(baseline.rows);
    }
    
    return NextResponse.json(result.rows);
  } catch (error) {
    console.error('Error fetching history:', error);
    return NextResponse.json({ error: 'Failed to fetch history' }, { status: 500 });
  }
}
