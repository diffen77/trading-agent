import { NextRequest, NextResponse } from 'next/server'

import {
  readBasicAuthConfig,
  verifyBasicAuthorization,
} from './lib/server/basic-auth.mjs'
import {
  readServiceAuthConfig,
  verifyServiceAuthorization,
} from './lib/server/service-auth.mjs'

const challenge = 'Basic realm="Trading Agent", charset="UTF-8"'

function noStoreHeaders() {
  return {
    'Cache-Control': 'no-store',
    Pragma: 'no-cache',
  }
}

function authenticationUnavailable(request: NextRequest) {
  if (request.nextUrl.pathname.startsWith('/api/')) {
    return NextResponse.json(
      { error: 'Authentication is not configured' },
      { status: 503, headers: noStoreHeaders() },
    )
  }
  return new NextResponse('Authentication is not configured', {
    status: 503,
    headers: noStoreHeaders(),
  })
}

function authenticationRequired(request: NextRequest) {
  const headers = {
    ...noStoreHeaders(),
    'WWW-Authenticate': challenge,
  }
  if (request.nextUrl.pathname.startsWith('/api/')) {
    return NextResponse.json(
      { error: 'Authentication required' },
      { status: 401, headers },
    )
  }
  return new NextResponse('Authentication required', {
    status: 401,
    headers,
  })
}

export async function middleware(request: NextRequest) {
  let config
  let serviceConfig
  try {
    config = readBasicAuthConfig({
      DASHBOARD_AUTH_USERNAME: process.env.DASHBOARD_AUTH_USERNAME,
      DASHBOARD_AUTH_PASSWORD: process.env.DASHBOARD_AUTH_PASSWORD,
    })
    serviceConfig = readServiceAuthConfig({
      OPERATIONS_READ_TOKEN: process.env.OPERATIONS_READ_TOKEN,
    })
  } catch {
    return authenticationUnavailable(request)
  }

  const authorization = request.headers.get('authorization')
  const serviceAuthorized = (
    request.nextUrl.pathname === '/api/operations'
    && await verifyServiceAuthorization(authorization, serviceConfig)
  )
  const authorized = serviceAuthorized || await verifyBasicAuthorization(
    authorization,
    config,
  )
  if (!authorized) {
    return authenticationRequired(request)
  }

  const response = NextResponse.next()
  response.headers.set('Cache-Control', 'no-store')
  response.headers.set('Pragma', 'no-cache')
  return response
}

export const config = {
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico|api/health).*)',
  ],
}
