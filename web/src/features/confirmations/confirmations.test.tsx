import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfirmationsPanel } from '@/features/confirmations/ConfirmationsPanel'
import { handleServerEvent } from '@/realtime/query-bridge'
import type { ServerEvent } from '@/realtime/events'
import { useAuth } from '@/stores/auth'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const pendingConfirmation = {
  confirmation_id: 'confirm-1',
  action: 'submit_pipeline',
  risk_level: 'consume',
  reason: '碎石 2 颗',
  payload: { tool_name: 'submit_pipeline', arguments: { tasks: [{ name: 'Fight', stone: 2 }] } },
  status: 'pending',
  requested_by: 'rest',
  audit_id: 91,
  expires_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
  created_at: '2026-09-25T12:20:00Z',
  resolved_at: null,
  resolved_by: null,
  resolved_reason: null,
  execution: null,
}

function renderPanel(fetchMock: typeof fetch) {
  vi.stubGlobal('fetch', fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const view = render(<QueryClientProvider client={queryClient}><ConfirmationsPanel /></QueryClientProvider>)
  return { ...view, queryClient }
}

describe('M11 pending confirmations panel', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuth.getState().clearToken()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('loads pending approvals, shows the exact action impact, and submits approval', async () => {
    const requests: Request[] = []
    let approved = false
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
      requests.push(request)
      const url = new URL(request.url)
      if (url.pathname === '/api/confirmations' && request.method === 'GET') {
        return json({ items: approved ? [] : [pendingConfirmation], total: approved ? 0 : 1, page: 1, size: 20 })
      }
      if (url.pathname === '/api/confirmations/confirm-1' && request.method === 'POST') {
        expect(await request.json()).toEqual({ approved: true })
        approved = true
        return json({ ...pendingConfirmation, status: 'approved' })
      }
      return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
    })

    renderPanel(fetchMock)

    expect(await screen.findByRole('heading', { name: '需要人工确认' })).toBeInTheDocument()
    expect(screen.getByText(/碎石 2 颗/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('查看操作参数'))
    expect(screen.getByText(/Fight/)).toBeInTheDocument()
    expect(screen.getByText(/stone/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '批准' }))

    await waitFor(() => expect(approved).toBe(true))
    await waitFor(() => expect(screen.queryByText('submit_pipeline')).not.toBeInTheDocument())
    expect(requests.some((request) => request.url.endsWith('/api/confirmations/confirm-1'))).toBe(true)
  })

  it('shows a recovery action when loading approvals fails', async () => {
    renderPanel(vi.fn<typeof fetch>(async () => json(
      { error: { code: 'SERVICE_UNAVAILABLE', message: 'service down' } }, 503,
    )))

    expect(await screen.findByRole('alert')).toHaveTextContent(/service down/)
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })

  it('identifies the session whose screen controls the grant will authorize', async () => {
    const grant = {
      ...pendingConfirmation,
      action: 'grant_atomic_ops',
      reason: '该会话尚未获得有效的原子操作授权',
      payload: { grant: true, session_id: 'session-target-42', window_seconds: 900 },
    }
    const fetchMock = vi.fn<typeof fetch>(async () => json({ items: [grant], total: 1, page: 1, size: 20 }))

    renderPanel(fetchMock)

    expect(await screen.findByText(/session-target-42/)).toBeInTheDocument()
    expect(screen.getAllByText(/15 分钟/).some((element) => element.tagName === 'P')).toBe(true)
  })

  it('refreshes a stale card after the server rejects a raced duplicate decision', async () => {
    let resolved = false
    let reads = 0
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
      const url = new URL(request.url)
      if (url.pathname === '/api/confirmations' && request.method === 'GET') {
        reads += 1
        return json({ items: resolved ? [] : [pendingConfirmation], total: resolved ? 0 : 1, page: 1, size: 20 })
      }
      if (url.pathname === '/api/confirmations/confirm-1' && request.method === 'POST') {
        resolved = true
        return json({ error: { code: 'CONFIRMATION_ALREADY_RESOLVED', message: 'already resolved' } }, 409)
      }
      return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
    })

    renderPanel(fetchMock)
    expect(await screen.findByText('submit_pipeline')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '批准' }))

    await waitFor(() => expect(reads).toBeGreaterThan(1))
    await waitFor(() => expect(screen.queryByText('submit_pipeline')).not.toBeInTheDocument())
  })

  it('refetches pending approvals when the shared WebSocket reports resolution', async () => {
    let reads = 0
    const fetchMock = vi.fn<typeof fetch>(async () => {
      reads += 1
      return json({ items: reads === 1 ? [pendingConfirmation] : [], total: 0, page: 1, size: 20 })
    })
    const { queryClient } = renderPanel(fetchMock)
    expect(await screen.findByText('submit_pipeline')).toBeInTheDocument()

    act(() => handleServerEvent({
      type: 'confirm_resolved',
      data: { confirmation_id: 'confirm-1', resolution: 'approved' },
      ts: Date.now() / 1000,
    } as ServerEvent, queryClient))

    await waitFor(() => expect(reads).toBeGreaterThan(1), { timeout: 1000 })
    await waitFor(() => expect(screen.queryByText('submit_pipeline')).not.toBeInTheDocument())
  })

  it('reloads approvals after the WebSocket subscription acknowledgement', async () => {
    let reads = 0
    const fetchMock = vi.fn<typeof fetch>(async () => {
      reads += 1
      return json({ items: reads === 1 ? [pendingConfirmation] : [], total: 0, page: 1, size: 20 })
    })
    const { queryClient } = renderPanel(fetchMock)
    expect(await screen.findByText('submit_pipeline')).toBeInTheDocument()

    act(() => handleServerEvent({
      type: 'subscribed',
      data: { channels: ['agent', 'logs'], backfilled: 0 },
      ts: Date.now() / 1000,
    } as ServerEvent, queryClient))

    await waitFor(() => expect(reads).toBeGreaterThan(1), { timeout: 1000 })
    await waitFor(() => expect(screen.queryByText('submit_pipeline')).not.toBeInTheDocument())
  })

  it('fetches a new approval after the shared WebSocket reports a request', async () => {
    let requested = false
    const fetchMock = vi.fn<typeof fetch>(async () => json({
      items: requested ? [pendingConfirmation] : [],
      total: requested ? 1 : 0,
      page: 1,
      size: 20,
    }))
    const { queryClient } = renderPanel(fetchMock)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    requested = true

    act(() => handleServerEvent({
      type: 'confirm_request',
      data: { confirmation_id: 'confirm-1', action: 'submit_pipeline' },
      ts: Date.now() / 1000,
    } as ServerEvent, queryClient))

    expect(await screen.findByText('submit_pipeline')).toBeInTheDocument()
  })
})
