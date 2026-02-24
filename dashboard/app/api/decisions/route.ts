import { NextResponse } from 'next/server'
import { Pool } from 'pg'

const pool = new Pool({ connectionString: process.env.DATABASE_URL })

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const result = await pool.query(`
      SELECT id, decisions_json, timestamp
      FROM ai_decisions
      ORDER BY timestamp DESC
      LIMIT 20
    `)
    return NextResponse.json(result.rows)
  } catch (error) {
    console.error('Error fetching decisions:', error)
    return NextResponse.json([])
  }
}
