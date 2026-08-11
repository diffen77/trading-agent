import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const overview = readFileSync(
  new URL('../app/visitor-overview.tsx', import.meta.url),
  'utf8',
)

test('visitor overview answers the four primary trading questions', () => {
  assert.match(overview, /Portföljen just nu/)
  assert.match(overview, /Senaste handlingen/)
  assert.match(overview, /Sedan start/)
  assert.match(overview, /Mot OMXSGI/)
})

test('visitor overview is honest when benchmark data is unavailable', () => {
  assert.match(overview, /Inväntar verifierad data/)
  assert.match(overview, /Ingen jämförelse fabriceras/)
})

test('latest action prioritizes the company name over its identifier', () => {
  assert.match(
    overview,
    /latestTrade\.company_name\?\.trim\(\) \|\| latestTrade\.ticker/,
  )
  assert.match(overview, /ISIN eller ticker/)
})
