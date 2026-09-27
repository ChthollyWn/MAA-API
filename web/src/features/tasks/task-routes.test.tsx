import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { routeConfig } from '@/routes/router'
import { useAuth } from '@/stores/auth'
import { useComposerStore } from './composer'

vi.mock('@/realtime/RealtimeProvider', () => ({
  useRealtimeStatus: () => ({ status: 'CONNECTED', reconnectAttempt: 0, rtt: null, truncated: false, closeCode: null, error: null }),
}))

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function installApiMock(options: { includeDeepLinkInHistory?: boolean } = {}) {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const { pathname } = new URL(request.url)
    if (pathname === '/api/system/health') return json({ auth_enabled: false, status: 'ok', version: 'test' })
    if (pathname === '/api/tasks/types') return json({ items: [], total: 0, page: 1, size: 20 })
    if (pathname === '/api/queue') return json({ running: null, pending: [], counts: { pending: 0, running: 0 }, paused: false })
    if (pathname === '/api/pipelines/current') return json({ pipeline: null })
    if (pathname === '/api/pipelines') {
      const item = options.includeDeepLinkInHistory === false
        ? { id: 'pipeline-recent', title: '较新流水线', status: 'COMPLETED', task_count: 1 }
        : { id: 'pipeline-42', title: '深链任务', status: 'COMPLETED', task_count: 1 }
      return json({ items: [item], total: 21, page: 1, size: 20 })
    }
    if (pathname === '/api/pipelines/pipeline-42') return json({ id: 'pipeline-42', title: '深链任务', status: 'COMPLETED', task_count: 1, tasks: [] })
    if (pathname === '/api/pipelines/pipeline-42/logs') return json({ items: [], total: 0, page: 1, size: 100 })
    if (pathname === '/api/pipelines/pipeline-42/screenshots') return json({ items: [], total: 0 })
    if (pathname === '/api/schedules') return json({ items: [], total: 0 })
    if (pathname === '/api/confirmations') return json({ items: [], total: 0, page: 1, size: 20 })
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const router = createMemoryRouter(routeConfig, { initialEntries: [path] })
  render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>)
  return { router }
}

describe('task area routes', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuth.getState().clearToken()
    useComposerStore.getState().reset()
    installApiMock()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('opens a history deep link on its own detail page even when it is not on the current page, and returns to the list (T10)', async () => {
    installApiMock({ includeDeepLinkInHistory: false })
    const { router } = renderAt('/tasks/history/pipeline-42')

    expect(await screen.findByRole('heading', { name: '深链任务' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '流水线详情', level: 1 })).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: '历史' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '返回' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/history'))
    expect(await screen.findByRole('link', { name: /较新流水线/ })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '历史' })).toHaveAttribute('aria-checked', 'true')
  })

  it('writes the task area segments into browser history so back and forward work (T11)', async () => {
    const { router } = renderAt('/tasks')

    expect(await screen.findByRole('heading', { name: '任务', level: 1 })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '新建' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(screen.getByRole('radio', { name: '队列' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/queue'))
    await waitFor(() => expect(screen.getByRole('radio', { name: '队列' })).toHaveAttribute('aria-checked', 'true'))

    await act(async () => router.navigate(-1))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks'))
    expect(await screen.findByRole('radio', { name: '新建' })).toHaveAttribute('aria-checked', 'true')
    await act(async () => router.navigate(1))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/queue'))
    expect(await screen.findByRole('radio', { name: '队列' })).toHaveAttribute('aria-checked', 'true')

    fireEvent.click(screen.getByRole('radio', { name: '定时' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/schedules'))
  })

  it('opens a history row as a detail page and returns to the list (T10)', async () => {
    const { router } = renderAt('/tasks/history')

    fireEvent.click(await screen.findByRole('link', { name: /深链任务/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/history/pipeline-42'))
    expect(await screen.findByRole('heading', { name: '深链任务' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '返回' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/history'))
    expect(await screen.findByRole('link', { name: /深链任务/ })).toBeInTheDocument()
  })
})
