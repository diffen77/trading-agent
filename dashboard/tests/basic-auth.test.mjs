import assert from 'node:assert/strict'
import test from 'node:test'

import {
  encodeBasicAuthorization,
  readBasicAuthConfig,
  verifyBasicAuthorization,
} from '../lib/server/basic-auth.mjs'

const validEnvironment = {
  DASHBOARD_AUTH_USERNAME: 'operator',
  DASHBOARD_AUTH_PASSWORD: 'a-long-test-password',
}

test('authentication configuration is fail-closed when missing', () => {
  assert.throws(
    () => readBasicAuthConfig({}),
    /DASHBOARD_AUTH_USERNAME/,
  )
})

test('authentication configuration rejects weak or ambiguous credentials', () => {
  assert.throws(
    () => readBasicAuthConfig({
      DASHBOARD_AUTH_USERNAME: 'bad:user',
      DASHBOARD_AUTH_PASSWORD: 'a-long-test-password',
    }),
    /username/i,
  )
  assert.throws(
    () => readBasicAuthConfig({
      DASHBOARD_AUTH_USERNAME: 'operator',
      DASHBOARD_AUTH_PASSWORD: 'too-short',
    }),
    /at least 16/i,
  )
})

test('valid Basic credentials authenticate', async () => {
  const config = readBasicAuthConfig(validEnvironment)
  const authorization = encodeBasicAuthorization(
    validEnvironment.DASHBOARD_AUTH_USERNAME,
    validEnvironment.DASHBOARD_AUTH_PASSWORD,
  )

  assert.equal(
    await verifyBasicAuthorization(authorization, config),
    true,
  )
})

test('wrong and malformed credentials fail without throwing', async () => {
  const config = readBasicAuthConfig(validEnvironment)

  assert.equal(await verifyBasicAuthorization(null, config), false)
  assert.equal(await verifyBasicAuthorization('Bearer token', config), false)
  assert.equal(await verifyBasicAuthorization('Basic !!!', config), false)
  assert.equal(
    await verifyBasicAuthorization(
      encodeBasicAuthorization('operator', 'a-long-wrong-password'),
      config,
    ),
    false,
  )
})

test('passwords may contain colons and UTF-8', async () => {
  const environment = {
    DASHBOARD_AUTH_USERNAME: 'diffen',
    DASHBOARD_AUTH_PASSWORD: 'lösen:ord-med-längd',
  }
  const config = readBasicAuthConfig(environment)
  const authorization = encodeBasicAuthorization(
    environment.DASHBOARD_AUTH_USERNAME,
    environment.DASHBOARD_AUTH_PASSWORD,
  )

  assert.equal(
    await verifyBasicAuthorization(authorization, config),
    true,
  )
})
