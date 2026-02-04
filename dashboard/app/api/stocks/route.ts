import { NextResponse } from 'next/server'
import { Pool } from 'pg'

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
})

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const result = await pool.query(`
      SELECT DISTINCT ON (p.ticker) 
        p.ticker, 
        p.close as price,
        p.date,
        c.name,
        c.sector
      FROM prices p
      LEFT JOIN companies c ON p.ticker = c.ticker
      ORDER BY p.ticker, p.date DESC
    `)
    
    return NextResponse.json(result.rows)
  } catch (error) {
    console.error('Error fetching stocks:', error)
    return NextResponse.json([])
  }
}
