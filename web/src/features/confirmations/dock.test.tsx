import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { routeConfig } from '@/routes/router'
import { useAuth } from '@/stores/auth'
import { useConfirmationUi } from '@/stores/confirmations-ui'

const pending = {
  confirmation_id: 'confirm-42',
  action: 'submit_pipeline',
  risk_level: 'consume',
  reason: '碎石 2 颗',
  status: 'pending',
  expires_at: new Date(Date.now() + 600_000).toISOString(),
  caller: { kind: 'internal', name: '内置 Agent', session_id: 'session-1' },
  payload: { tool_name: 'submit_pipeline', arguments: { tasks: [{ name: 'Fight', stone: 2 }] } },
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function installApi(options: { authEnabled?: boolean; items?: unknown[] } = {}) {
  let items = options.items ?? [pending]
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const { pathname } = new URL(request.url)
    if (pathname === '/api/system/health') return json({ auth_enabled: options.authEnabled ?? false, status: 'ok', version: 'test' })
    if (pathname === '/api/confirmations' && request.method === 'GET') return json({ items, total: items.length, page: 1, size: 20 })
    if (pathname === '/api/confirmations/confirm-42' && request.method === 'POST') {
      items = []
      return json({ ...pending, status: 'approved' })
    }
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  const router = createMemoryRouter(routeConfig, { initialEntries: [path] })
  render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>)
  return router
}

describe('global confirmation dock', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuth.getState().clearToken()
    useConfirmationUi.setState({ open: false, pendingCount: 0 })
    Element.prototype.scrollIntoView = vi.fn()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('opens from a deep link on any page, highlights the card and clears the link once resolved', async () => {
    const fetchMock = installApi()
    const router = renderAt('/more?confirm=confirm-42')

    const sheet = await screen.findByRole('dialog', { name: '需要人工确认' })
    expect(await within(sheet).findByText(/碎石 2 颗/)).toBeInTheDocument()
    expect(sheet.querySelector('.ring-2')).not.toBeNull()

    fireEvent.click(within(sheet).getByRole('button', { name: '批准' }))

    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) =>
      input instanceof Request && input.method === 'POST' && input.url.endsWith('/api/confirmations/confirm-42'),
    )).toBe(true))
    await waitFor(() => expect(router.state.location.search).toBe(''))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '需要人工确认' })).not.toBeInTheDocument())
  })

  it('shows a pending entry in the title bar outside the home page', async () => {
    installApi()
    renderAt('/more')

    const entry = await screen.findByRole('button', { name: '1 项待确认' })
    fireEvent.click(entry)

    const sheet = await screen.findByRole('dialog', { name: '需要人工确认' })
    expect(within(sheet).getByText('submit_pipeline')).toBeInTheDocument()
  })

  it('does not read confirmations before the user is authenticated', async () => {
    const fetchMock = installApi({ authEnabled: true })
    const router = renderAt('/more')

    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(fetchMock.mock.calls.some(([input]) => input instanceof Request && new URL(input.url).pathname === '/api/confirmations')).toBe(false)
  })
})
