import { readRuntimeSecret } from './runtime-secrets.mjs'


for (const name of [
  'DATABASE_URL',
  'DASHBOARD_AUTH_USERNAME',
  'DASHBOARD_AUTH_PASSWORD',
]) {
  process.env[name] = readRuntimeSecret(process.env, name, {
    required: true,
  })
}

process.env.OPERATIONS_READ_TOKEN = readRuntimeSecret(
  process.env,
  'OPERATIONS_READ_TOKEN',
  { defaultValue: '' },
)

await import('./server.js')
