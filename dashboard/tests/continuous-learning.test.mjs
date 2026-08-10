import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'


const page = readFileSync(
  new URL('../app/page.tsx', import.meta.url),
  'utf8',
)
const panel = readFileSync(
  new URL('../app/continuous-learning-panel.tsx', import.meta.url),
  'utf8',
)
const route = readFileSync(
  new URL('../app/api/learning/route.ts', import.meta.url),
  'utf8',
)


test('dashboard fetches continuous learning evidence', () => {
  assert.match(page, /fetch\('\/api\/learning'\)/)
  assert.match(page, /<ContinuousLearningPanel status=\{learning\}/)
  assert.match(page, /!learning/)
})


test('learning panel distinguishes measured outcomes from conclusions', () => {
  assert.match(panel, /Kontinuerlig förbättring/)
  assert.match(panel, /Analyserade kandidater/)
  assert.match(panel, /Mätta utfall/)
  assert.match(panel, /Mogna utfall täckta/)
  assert.match(panel, /Samlar underlag/)
  assert.match(panel, /av mogna utfall mätta/)
  assert.doesNotMatch(panel, /Utfall saknas/)
  assert.match(panel, /Inga slutsatser dras/)
  assert.match(panel, /Avstått/)
  assert.match(panel, /horizon_minutes/)
  assert.match(panel, /Senaste skuggbevakning/)
  assert.match(panel, /Lärprocess/)
  assert.match(panel, /Framåttestad challenger/)
  assert.match(panel, /operatörsgranskning/)
  assert.match(panel, /Tradingminne/)
  assert.match(panel, /Kunskapsgraf/)
  assert.match(panel, /total_nodes/)
  assert.match(panel, /Grafminne i skuggläge/)
  assert.match(panel, /påverkar inte affärer/)
  assert.match(panel, /changed_count/)
  assert.match(panel, /Samlar ännu tillräckligt med verifierade utfall/)
  assert.doesNotMatch(panel, /\{status\.knowledge_shadow\.reason_code\}/)
  assert.match(panel, /genomförd grafjämförelse/)
  assert.match(panel, /genomförda grafjämförelser/)
  assert.match(panel, /ändrad handling/)
  assert.match(panel, /ändrade handlingar/)
  assert.match(panel, /successful_runs/)
  assert.match(panel, /total_comparison_count/)
  assert.match(panel, /total_changed_count/)
})


test('learning API uses append-only prediction and outcome journals', () => {
  assert.match(route, /FROM candidate_predictions/)
  assert.match(route, /FROM candidate_prediction_outcomes/)
  assert.match(route, /expected_horizons_minutes/)
  assert.match(route, /overdue_outcomes/)
  assert.match(route, /horizon_minutes/)
  assert.match(route, /FROM continuous_learning_runs/)
  assert.match(route, /FROM candidate_policy_calibration_runs/)
  assert.match(route, /FROM candidate_policy_versions/)
  assert.match(route, /latest_cycle_candidates/)
  assert.match(route, /FROM knowledge_graph_sync_runs/)
  assert.match(route, /knowledge_graph_sync/)
  assert.match(route, /FROM knowledge_shadow_runs/)
  assert.match(route, /knowledge_shadow_stats/)
  assert.match(route, /knowledge_shadow/)
  assert.match(route, /databaseUnavailable/)
  assert.doesNotMatch(route, /\bFROM\s+prices\b/i)
  assert.doesNotMatch(route, /\bFROM\s+macro\b/i)
})


test(
  'continuous learning query executes against schema 45',
  { skip: !process.env.TEST_DATABASE_URL },
  async () => {
    const marker = 'getDatabasePool().query(`'
    const start = route.indexOf(marker)
    const end = route.indexOf('\n    `)', start + marker.length)
    assert.notEqual(start, -1)
    assert.notEqual(end, -1)
    const sql = route.slice(start + marker.length, end)
    const { Pool } = await import('pg')
    const pool = new Pool({ connectionString: process.env.TEST_DATABASE_URL })
    try {
      const result = await pool.query(sql)
      assert.equal(result.rowCount, 1)
      assert.equal(typeof result.rows[0].predictions, 'number')
      assert.equal(typeof result.rows[0].overdue_outcomes, 'number')
      assert.ok(Array.isArray(result.rows[0].action_metrics))
    } finally {
      await pool.end()
    }
  },
)
