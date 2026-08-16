import assert from 'node:assert/strict'
import test from 'node:test'

import {
  encodeServiceAuthorization,
  readServiceAuthConfig,
  verifyServiceAuthorization,
} from '../lib/server/service-auth.mjs'


const validToken = 'test-read-token-with-at-least-32-bytes'


test('service authentication is optional but rejects weak tokens', () => {
  assert.equal(readServiceAuthConfig({}), null)
  assert.throws(
    () => readServiceAuthConfig({ OPERATIONS_READ_TOKEN: 'too-short' }),
    /at least 32 bytes/i,
  )
})


test('valid service Bearer token authenticates', async () => {
  const config = readServiceAuthConfig({
    OPERATIONS_READ_TOKEN: validToken,
  })

  assert.equal(
    await verifyServiceAuthorization(
      encodeServiceAuthorization(validToken),
      config,
    ),
    true,
  )
})


test('wrong or malformed service tokens fail without throwing', async () => {
  const config = readServiceAuthConfig({
    OPERATIONS_READ_TOKEN: validToken,
  })

  assert.equal(await verifyServiceAuthorization(null, config), false)
  assert.equal(
    await verifyServiceAuthorization('Basic dXNlcjpwYXNz', config),
    false,
  )
  assert.equal(
    await verifyServiceAuthorization('Bearer wrong-token-value', config),
    false,
  )
  assert.equal(
    await verifyServiceAuthorization(
      encodeServiceAuthorization(validToken),
      null,
    ),
    false,
  )
  assert.equal(
    await verifyServiceAuthorization(`Bearer ${'a'.repeat(1025)}`, config),
    false,
  )
})
