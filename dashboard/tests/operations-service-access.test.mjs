import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'


const middleware = readFileSync(
  new URL('../middleware.ts', import.meta.url),
  'utf8',
)
const entrypoint = readFileSync(
  new URL('../runtime-entrypoint.mjs', import.meta.url),
  'utf8',
)
const compose = readFileSync(
  new URL('../../ops/release/compose.production.yml', import.meta.url),
  'utf8',
)


test('service token is restricted to the operations endpoint', () => {
  assert.match(middleware, /OPERATIONS_READ_TOKEN/)
  assert.match(
    middleware,
    /const serviceAuthorized = \(\s*request\.nextUrl\.pathname === '\/api\/operations'\s*&& await verifyServiceAuthorization/,
  )
})


test('production loads the read token from a locked runtime secret', () => {
  assert.match(entrypoint, /'OPERATIONS_READ_TOKEN'/)
  assert.match(
    compose,
    /OPERATIONS_READ_TOKEN_FILE: \/run\/secrets\/operations_read_token/,
  )
  assert.match(
    compose,
    /operations_read_token:\s+environment: TRADING_AGENT_OPERATIONS_READ_TOKEN_SECRET/,
  )
})
