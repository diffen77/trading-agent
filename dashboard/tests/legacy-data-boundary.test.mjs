import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'


const routeFiles = [
  '../app/api/history/route.ts',
  '../app/api/macro/route.ts',
  '../app/api/prospects/route.ts',
  '../app/api/stocks/route.ts',
]
const authorizedQuoteQueryFiles = [
  '../lib/server/authorized-market-data.mjs',
  '../lib/server/operational-status.mjs',
  '../lib/server/portfolio-valuation.mjs',
]


test('dashboard routes never read legacy prices or macro tables', () => {
  for (const file of routeFiles) {
    const source = readFileSync(new URL(file, import.meta.url), 'utf8')
    assert.doesNotMatch(source, /\bFROM\s+prices\b/i, file)
    assert.doesNotMatch(source, /\bFROM\s+macro\b/i, file)
  }
})


test('authorized dashboard quotes require exact provider contract files', () => {
  for (const file of authorizedQuoteQueryFiles) {
    const source = readFileSync(new URL(file, import.meta.url), 'utf8')
    assert.match(source, /JOIN\s+market_data_files\s+source_file/i, file)
    assert.match(
      source,
      /source_file\.provider_contract_id\s*=/i,
      file,
    )
  }
})
