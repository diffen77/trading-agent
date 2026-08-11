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

await import('./server.js')
