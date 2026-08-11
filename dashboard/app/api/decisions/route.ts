import { NextResponse } from 'next/server'

import {
  databaseUnavailable,
  getDatabasePool,
} from '@/lib/server/database'
import { collectDecisionIdentifiers } from '@/lib/decision-presentation.mjs'

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const pool = getDatabasePool()
    const result = await pool.query(`
      SELECT
        decision.id,
        decision.decisions_json,
        decision.timestamp,
        decision.model_backend,
        decision.model_name,
        decision.model_provider,
        decision.reasoning_effort,
        decision.response_model,
        (
          SELECT COUNT(*)::INTEGER
          FROM candidate_predictions AS prediction
          WHERE prediction.ai_decision_id = decision.id
        ) AS prediction_count,
        COALESCE(
          (
            SELECT JSON_AGG(
              JSON_BUILD_OBJECT(
                'ticker', trade.ticker,
                'action', trade.action
              )
              ORDER BY trade.executed_at
            )
            FROM trades AS trade
            WHERE trade.decision_id = decision.id
          ),
          '[]'::JSON
        ) AS executed_trades
      FROM ai_decisions AS decision
      ORDER BY decision.timestamp DESC
      LIMIT 20
    `)
    const identifiers = collectDecisionIdentifiers(result.rows)
    const companyResult = identifiers.length === 0
      ? { rows: [] }
      : await pool.query(
          `
            SELECT ticker, name
            FROM companies
            WHERE ticker = ANY($1::text[])
          `,
          [identifiers],
        )
    const instrumentNames = Object.fromEntries(
      companyResult.rows.map(company => [company.ticker, company.name]),
    )

    return NextResponse.json(
      result.rows.map(decision => ({
        ...decision,
        instrument_names: instrumentNames,
      })),
    )
  } catch (error) {
    return databaseUnavailable('fetch decisions', error)
  }
}
