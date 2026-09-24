import createClient, { type Middleware } from 'openapi-fetch'
import type { paths } from '@/types/api'
import { useAuth } from '@/stores/auth'

function createRequestId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }

  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

export const authMiddleware: Middleware = {
  onRequest({ request, schemaPath }) {
    if (schemaPath !== '/api/system/health') {
      const token = useAuth.getState().token
      if (token && !request.headers.has('X-Token')) {
        request.headers.set('X-Token', token)
      }
    }

    if (!request.headers.has('X-Request-Id')) {
      request.headers.set('X-Request-Id', createRequestId())
    }

    return request
  },
  onResponse({ response }) {
    if (response.status === 401) {
      useAuth.getState().onUnauthorized()
    }
    return response
  },
}

// Keep fetch lookup dynamic so the client works with app-level fetch instrumentation and tests.
const fetchThroughGlobal: typeof fetch = (input, init) => globalThis.fetch(input, init)

export const api = createClient<paths>({
  // openapi-fetch's Request constructor needs an absolute URL in Node/jsdom;
  // using the browser origin still keeps every call same-origin in the app.
  baseUrl: typeof window === 'undefined' ? '' : window.location.origin,
  fetch: fetchThroughGlobal,
})

api.use(authMiddleware)
