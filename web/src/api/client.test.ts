import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './client'
import { ApiError, apiErrorFromResponse } from './errors'
import { shouldRetryQuery, queryClient } from '@/lib/query-client'
import {
  AUTH_TOKEN_STORAGE_KEY,
  UNAUTHORIZED_EVENT,
  useAuth,
} from '@/stores/auth'

describe('API client', () => {
  let requests: Request[]
  let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>

  beforeEach(() => {
    requests = []
    localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY)
    useAuth.getState().clearToken()
    fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      return new Response(null, { status: 204 })
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('injects X-Token and a request id, while leaving health checks unauthenticated', async () => {
    useAuth.getState().setToken('secret-token')

    await api.DELETE('/api/system/auth/cookie')
    await api.GET('/api/system/health')

    expect(requests).toHaveLength(2)
    expect(requests[0]?.headers.get('X-Token')).toBe('secret-token')
    expect(requests[0]?.headers.get('X-Request-Id')).toBeTruthy()
    expect(requests[1]?.headers.has('X-Token')).toBe(false)
    expect(requests[1]?.headers.get('X-Request-Id')).toBeTruthy()
    expect(localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)).toBe('secret-token')
  })

  it('returns an empty successful result for a 204 response', async () => {
    const result = await api.POST('/api/system/auth/cookie')

    expect(result.response.status).toBe(204)
    expect(result.data).toBeUndefined()
    expect(result.error).toBeUndefined()
  })

  it('clears auth and emits the unauthorized event on 401, but preserves auth on 403', async () => {
    let status = 401
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      return new Response(
        JSON.stringify({
          error: {
            code: status === 401 ? 'UNAUTHORIZED' : 'FORBIDDEN',
            message: 'Request denied',
            details: {},
          },
        }),
        { status, headers: { 'Content-Type': 'application/json' } },
      )
    })

    const onUnauthorized = vi.fn()
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    useAuth.getState().setToken('secret-token')

    const unauthorized = await api.DELETE('/api/system/auth/cookie')
    expect(unauthorized.response.status).toBe(401)
    expect(unauthorized.error).toEqual({
      error: { code: 'UNAUTHORIZED', message: 'Request denied', details: {} },
    })
    expect(useAuth.getState().token).toBeNull()
    expect(localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)).toBeNull()
    expect(onUnauthorized).toHaveBeenCalledTimes(1)

    status = 403
    useAuth.getState().setToken('still-valid')
    const forbidden = await api.DELETE('/api/system/auth/cookie')
    expect(forbidden.response.status).toBe(403)
    expect(useAuth.getState().token).toBe('still-valid')
    expect(onUnauthorized).toHaveBeenCalledTimes(1)

    window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  })

  it('normalizes the standard error envelope', () => {
    const error = apiErrorFromResponse(
      new Response(null, { status: 422, statusText: 'Unprocessable Entity' }),
      {
        error: {
          code: 'INVALID_INPUT',
          message: 'Invalid input',
          details: { field: 'name' },
        },
      },
    )

    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(422)
    expect(error.code).toBe('INVALID_INPUT')
    expect(error.message).toBe('Invalid input')
    expect(error.details).toEqual({ field: 'name' })
  })

  it('retries network and ordinary server errors at most three times, but not 4xx or 504', () => {
    expect(shouldRetryQuery(0, new Error('offline'))).toBe(true)
    expect(shouldRetryQuery(2, new Error('offline'))).toBe(true)
    expect(shouldRetryQuery(3, new Error('offline'))).toBe(false)
    expect(shouldRetryQuery(0, apiErrorFromResponse(new Response(null, { status: 500 }), {}))).toBe(true)
    expect(shouldRetryQuery(0, apiErrorFromResponse(new Response(null, { status: 504 }), {}))).toBe(false)
    expect(shouldRetryQuery(0, apiErrorFromResponse(new Response(null, { status: 401 }), {}))).toBe(false)
    expect(shouldRetryQuery(0, apiErrorFromResponse(new Response(null, { status: 403 }), {}))).toBe(false)
    expect(queryClient.getDefaultOptions().mutations?.retry).toBe(false)
  })
})
