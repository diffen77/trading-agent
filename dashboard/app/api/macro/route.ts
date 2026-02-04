import { NextResponse } from 'next/server'
import { Pool } from 'pg'

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
})

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const result = await pool.query(`
      SELECT symbol, type, value, change_pct, date
      FROM macro
      ORDER BY type, symbol
    `)
    
    return NextResponse.json(result.rows)
  } catch (error) {
    console.error('Error fetching macro:', error)
    return NextResponse.json([])
  }
}
