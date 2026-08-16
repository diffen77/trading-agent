const MINIMUM_TOKEN_BYTES = 32
const MAXIMUM_TOKEN_BYTES = 1024
const TOKEN_PATTERN = /^[A-Za-z0-9._~-]+$/


function utf8Bytes(value) {
  return new TextEncoder().encode(value)
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


export function readServiceAuthConfig(environment) {
  const token = environment.OPERATIONS_READ_TOKEN
  if (token == null || token === '') return null
  if (typeof token !== 'string') {
    throw new Error('OPERATIONS_READ_TOKEN must be configured as text')
  }
  const tokenBytes = utf8Bytes(token).length
  if (tokenBytes < MINIMUM_TOKEN_BYTES) {
    throw new Error('OPERATIONS_READ_TOKEN must be at least 32 bytes')
  }
  if (tokenBytes > MAXIMUM_TOKEN_BYTES || !TOKEN_PATTERN.test(token)) {
    throw new Error('OPERATIONS_READ_TOKEN is invalid')
  }
  return Object.freeze({ token })
}


export function encodeServiceAuthorization(token) {
  return `Bearer ${token}`
}


export async function verifyServiceAuthorization(authorization, config) {
  if (typeof authorization !== 'string' || !config) return false
  const match = /^Bearer ([A-Za-z0-9._~-]+)$/.exec(authorization)
  if (!match) return false
  if (utf8Bytes(match[1]).length > MAXIMUM_TOKEN_BYTES) return false
  const [suppliedDigest, expectedDigest] = await Promise.all([
    digest(match[1]),
    digest(config.token),
  ])
  return equalDigest(suppliedDigest, expectedDigest)
}
