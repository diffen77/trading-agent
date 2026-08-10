const MINIMUM_PASSWORD_BYTES = 16
const MAXIMUM_CREDENTIAL_BYTES = 1024
const CONTROL_CHARACTER = /[\u0000-\u001f\u007f]/

function utf8Bytes(value) {
  return new TextEncoder().encode(value)
}

function encodeBase64Utf8(value) {
  const bytes = utf8Bytes(value)
  return btoa(String.fromCharCode(...bytes))
}

function decodeBase64Utf8(value) {
  if (
    value.length === 0
    || value.length % 4 !== 0
    || !/^[A-Za-z0-9+/]+={0,2}$/.test(value)
  ) {
    return null
  }

  try {
    const binary = atob(value)
    const bytes = Uint8Array.from(binary, (character) => (
      character.charCodeAt(0)
    ))
    const decoded = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
    return encodeBase64Utf8(decoded) === value ? decoded : null
  } catch {
    return null
  }
}

async function digest(value) {
  return new Uint8Array(
    await globalThis.crypto.subtle.digest('SHA-256', utf8Bytes(value)),
  )
}

function equalDigest(left, right) {
  let difference = 0
  for (let index = 0; index < left.length; index += 1) {
    difference |= left[index] ^ right[index]
  }
  return difference === 0
}

export function readBasicAuthConfig(environment) {
  const username = environment.DASHBOARD_AUTH_USERNAME
  const password = environment.DASHBOARD_AUTH_PASSWORD

  if (typeof username !== 'string' || username.length === 0) {
    throw new Error('DASHBOARD_AUTH_USERNAME must be configured')
  }
  if (
    username !== username.trim()
    || username.includes(':')
    || CONTROL_CHARACTER.test(username)
    || utf8Bytes(username).length > MAXIMUM_CREDENTIAL_BYTES
  ) {
    throw new Error('Dashboard authentication username is invalid')
  }
  if (typeof password !== 'string') {
    throw new Error('DASHBOARD_AUTH_PASSWORD must be configured')
  }

  const passwordBytes = utf8Bytes(password).length
  if (passwordBytes < MINIMUM_PASSWORD_BYTES) {
    throw new Error('Dashboard authentication password must be at least 16 bytes')
  }
  if (
    passwordBytes > MAXIMUM_CREDENTIAL_BYTES
    || CONTROL_CHARACTER.test(password)
  ) {
    throw new Error('Dashboard authentication password is invalid')
  }

  return Object.freeze({ username, password })
}

export function encodeBasicAuthorization(username, password) {
  return `Basic ${encodeBase64Utf8(`${username}:${password}`)}`
}

export async function verifyBasicAuthorization(authorization, config) {
  if (typeof authorization !== 'string') {
    return false
  }

  const match = /^Basic ([A-Za-z0-9+/]+={0,2})$/i.exec(authorization)
  if (!match) {
    return false
  }

  const decoded = decodeBase64Utf8(match[1])
  const separatorIndex = decoded?.indexOf(':') ?? -1
  if (separatorIndex < 1) {
    return false
  }

  const suppliedUsername = decoded.slice(0, separatorIndex)
  const suppliedPassword = decoded.slice(separatorIndex + 1)
  const [
    suppliedUsernameDigest,
    expectedUsernameDigest,
    suppliedPasswordDigest,
    expectedPasswordDigest,
  ] = await Promise.all([
    digest(suppliedUsername),
    digest(config.username),
    digest(suppliedPassword),
    digest(config.password),
  ])

  return (
    equalDigest(suppliedUsernameDigest, expectedUsernameDigest)
    && equalDigest(suppliedPasswordDigest, expectedPasswordDigest)
  )
}
