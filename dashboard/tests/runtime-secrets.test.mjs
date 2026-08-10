import assert from 'node:assert/strict'
import {
  chmodSync,
  mkdtempSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

import {
  RuntimeSecretError,
  readRuntimeSecret,
} from '../runtime-secrets.mjs'


function temporaryDirectory() {
  return mkdtempSync(join(tmpdir(), 'trading-agent-dashboard-secret-'))
}

function writeSecret(directory, name, value, mode = 0o600) {
  const path = join(directory, name)
  writeFileSync(path, value, { mode })
  chmodSync(path, mode)
  return path
}


test('dashboard reads one bounded runtime secret file', () => {
  const directory = temporaryDirectory()
  const path = writeSecret(directory, 'password', 'synthetic-password\n')

  assert.equal(
    readRuntimeSecret(
      { DASHBOARD_AUTH_PASSWORD_FILE: path },
      'DASHBOARD_AUTH_PASSWORD',
      { required: true },
    ),
    'synthetic-password',
  )
})


test('dashboard rejects inline/file ambiguity and insecure files', () => {
  const directory = temporaryDirectory()
  const path = writeSecret(directory, 'password', 'synthetic-password\n')

  assert.throws(
    () => readRuntimeSecret(
      {
        DASHBOARD_AUTH_PASSWORD: 'inline-password',
        DASHBOARD_AUTH_PASSWORD_FILE: path,
      },
      'DASHBOARD_AUTH_PASSWORD',
    ),
    RuntimeSecretError,
  )

  chmodSync(path, 0o640)
  assert.throws(
    () => readRuntimeSecret(
      { DASHBOARD_AUTH_PASSWORD_FILE: path },
      'DASHBOARD_AUTH_PASSWORD',
    ),
    /permissions/,
  )

  chmodSync(path, 0o600)
  const link = join(directory, 'password-link')
  symlinkSync(path, link)
  assert.throws(
    () => readRuntimeSecret(
      { DASHBOARD_AUTH_PASSWORD_FILE: link },
      'DASHBOARD_AUTH_PASSWORD',
    ),
    /symlink/,
  )
})


test('dashboard rejects multiline secrets and defaults optional empty files', () => {
  const directory = temporaryDirectory()
  const multiline = writeSecret(directory, 'multiline', 'first\nsecond\n')
  const empty = writeSecret(directory, 'empty', '')
  const invalidUtf8 = writeSecret(
    directory,
    'invalid-utf8',
    Buffer.from([0xff]),
  )

  assert.throws(
    () => readRuntimeSecret(
      { SERVICE_TOKEN_FILE: multiline },
      'SERVICE_TOKEN',
    ),
    /single line/,
  )
  assert.equal(
    readRuntimeSecret(
      { OPTIONAL_TOKEN_FILE: empty },
      'OPTIONAL_TOKEN',
      { defaultValue: 'disabled' },
    ),
    'disabled',
  )
  assert.throws(
    () => readRuntimeSecret(
      { OPTIONAL_TOKEN_FILE: invalidUtf8 },
      'OPTIONAL_TOKEN',
    ),
    /UTF-8/,
  )
})
