import {
  closeSync,
  constants,
  fstatSync,
  lstatSync,
  openSync,
  readFileSync,
} from 'node:fs'
import { isAbsolute } from 'node:path'


const NAME_PATTERN = /^[A-Z][A-Z0-9_]*$/


export class RuntimeSecretError extends Error {
  constructor(message) {
    super(message)
    this.name = 'RuntimeSecretError'
  }
}


function validateValue(name, value, { required, defaultValue, maxBytes }) {
  if (Buffer.byteLength(value, 'utf8') > maxBytes) {
    throw new RuntimeSecretError(`${name} exceeds the size limit`)
  }
  if (value.endsWith('\n')) {
    value = value.slice(0, -1)
  }
  if (value.includes('\n') || value.includes('\r')) {
    throw new RuntimeSecretError(`${name} must be a single line`)
  }
  for (const character of value) {
    const codePoint = character.codePointAt(0)
    if (codePoint < 32 || codePoint === 127) {
      throw new RuntimeSecretError(`${name} contains control characters`)
    }
  }
  if (!value) {
    if (required) {
      throw new RuntimeSecretError(`${name} is required`)
    }
    return defaultValue
  }
  return value
}


function readLockedFile(name, path, maxBytes) {
  if (!isAbsolute(path)) {
    throw new RuntimeSecretError(`${name}_FILE must be absolute`)
  }
  let metadata
  try {
    metadata = lstatSync(path)
  } catch (error) {
    throw new RuntimeSecretError(
      `${name}_FILE is unavailable`,
      { cause: error },
    )
  }
  if (metadata.isSymbolicLink()) {
    throw new RuntimeSecretError(`${name}_FILE must not be a symlink`)
  }

  let descriptor
  try {
    descriptor = openSync(
      path,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
    )
    metadata = fstatSync(descriptor)
    if (!metadata.isFile()) {
      throw new RuntimeSecretError(
        `${name}_FILE must be a regular file`,
      )
    }
    if ((metadata.mode & 0o077) !== 0) {
      throw new RuntimeSecretError(
        `${name}_FILE permissions must allow only the owner`,
      )
    }
    if (metadata.size > maxBytes) {
      throw new RuntimeSecretError(
        `${name}_FILE exceeds the size limit`,
      )
    }
    const payload = readFileSync(descriptor)
    if (payload.length > maxBytes) {
      throw new RuntimeSecretError(
        `${name}_FILE exceeds the size limit`,
      )
    }
    try {
      return new TextDecoder('utf-8', { fatal: true }).decode(payload)
    } catch (error) {
      throw new RuntimeSecretError(
        `${name}_FILE must contain UTF-8`,
        { cause: error },
      )
    }
  } catch (error) {
    if (error instanceof RuntimeSecretError) {
      throw error
    }
    throw new RuntimeSecretError(
      `${name}_FILE is unavailable`,
      { cause: error },
    )
  } finally {
    if (descriptor !== undefined) {
      closeSync(descriptor)
    }
  }
}


export function readRuntimeSecret(
  environ,
  name,
  {
    required = false,
    defaultValue = undefined,
    maxBytes = 16_384,
  } = {},
) {
  if (!NAME_PATTERN.test(name)) {
    throw new RuntimeSecretError('secret name is invalid')
  }
  if (!Number.isInteger(maxBytes) || maxBytes < 1) {
    throw new RuntimeSecretError('maxBytes must be a positive integer')
  }

  const inlinePresent = Object.hasOwn(environ, name)
  const fileName = `${name}_FILE`
  const filePresent = (
    Object.hasOwn(environ, fileName) && Boolean(environ[fileName])
  )
  if (inlinePresent && filePresent) {
    throw new RuntimeSecretError(
      `${name} and ${fileName} are both configured`,
    )
  }

  let value
  if (filePresent) {
    value = readLockedFile(name, environ[fileName], maxBytes)
  } else if (inlinePresent) {
    value = environ[name]
  } else {
    if (required) {
      throw new RuntimeSecretError(`${name} is required`)
    }
    return defaultValue
  }
  return validateValue(
    name,
    value,
    { required, defaultValue, maxBytes },
  )
}
