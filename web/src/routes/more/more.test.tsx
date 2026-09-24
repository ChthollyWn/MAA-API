import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, MemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import MorePage from '@/routes/more'
import { routeConfig } from '@/routes/router'
import { useAuth } from '@/stores/auth'

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
    if (pathname === '/api/pipelines') {
      const item = options.includeDeepLinkInHistory === false
        ? { id: 'pipeline-recent', title: '较新流水线', status: 'COMPLETED', task_count: 1 }
        : { id: 'pipeline-42', title: '深链任务', status: 'COMPLETED', task_count: 1 }
      return json({ items: [item], total: 21, page: 1, size: 20 })
    }
    if (pathname === '/api/pipelines/pipeline-42') return json({ id: 'pipeline-42', title: '深链任务', status: 'COMPLETED', task_count: 1, tasks: [] })
    if (pathname === '/api/schedules') return json({ items: [], total: 0 })
    if (pathname === '/api/updates/status') return json({ running: null, last_checked_at: null })
    if (pathname === '/api/updates') return json({ items: [], total: 0, page: 1, size: 20 })
    if (pathname === '/api/settings/schema') return json({ items: [] })
    if (pathname === '/api/settings') return json({ items: {} })
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const router = createMemoryRouter(routeConfig, { initialEntries: [path] })
  render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>)
  return { router, queryClient }
}

describe('more routes and task deep links', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuth.getState().clearToken()
    installApiMock()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows only the working M9 destinations as grouped list links', () => {
    render(<MemoryRouter><MorePage /></MemoryRouter>)

    expect(screen.getByRole('heading', { name: '运行与维护' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '管理' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /热更新/ })).toHaveAttribute('href', '/more/updates')
    expect(screen.getByRole('link', { name: /定时任务/ })).toHaveAttribute('href', '/more/schedules')
    expect(screen.getByRole('link', { name: /设置/ })).toHaveAttribute('href', '/more/settings')
    expect(screen.getAllByRole('link')).toHaveLength(4)
  })

  it('adds the API console destination and a protected direct route', () => {
    render(<MemoryRouter><MorePage /></MemoryRouter>)

    expect(screen.getByRole('link', { name: /API 调试台/ })).toHaveAttribute('href', '/more/api-console')
    const children = routeConfig.find((route) => route.path === '/')?.children ?? []
    expect(children.some((route) => route.path === 'more/api-console')).toBe(true)
  })

  it('opens a child page directly and supports browser back and forward', async () => {
    const { router } = renderAt('/more/settings')

    expect(await screen.findByRole('heading', { name: '设置' })).toBeInTheDocument()
    const moreTab = screen.getByRole('link', { name: '更多' })
    expect(moreTab).toHaveAttribute('aria-current', 'page')
    expect(moreTab.className).toContain('text-primary')
    await act(async () => router.navigate('/more'))
    expect(await screen.findByRole('heading', { name: '更多' })).toBeInTheDocument()
    await act(async () => router.navigate('/more/schedules'))
    expect(await screen.findByRole('heading', { name: '定时任务' })).toBeInTheDocument()
    await act(async () => router.navigate(-1))
    expect(await screen.findByRole('heading', { name: '更多' })).toBeInTheDocument()
    await act(async () => router.navigate(1))
    expect(await screen.findByRole('heading', { name: '定时任务' })).toBeInTheDocument()
  })

  it('maps a task history deep link to the history tab and expands its matching item', async () => {
    installApiMock({ includeDeepLinkInHistory: false })
    renderAt('/tasks/history/pipeline-42')

    const historyTab = await screen.findByRole('tab', { name: '历史' })
    await waitFor(() => expect(historyTab).toHaveAttribute('aria-selected', 'true'))
    expect(await screen.findByRole('button', { name: /深链任务/ })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('此深链目标不在当前分页；已将它固定显示在列表顶部。')).toHaveAttribute('role', 'status')
  })

  it('keeps task tab selection in browser history', async () => {
    const { router } = renderAt('/tasks')

    const queueTab = await screen.findByRole('tab', { name: '队列' })
    fireEvent.click(queueTab)
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/queue'))
    await waitFor(() => expect(queueTab).toHaveAttribute('aria-selected', 'true'))

    await act(async () => router.navigate(-1))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks'))
    expect(await screen.findByRole('tab', { name: '创建' })).toHaveAttribute('aria-selected', 'true')
    await act(async () => router.navigate(1))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/queue'))
    expect(await screen.findByRole('tab', { name: '队列' })).toHaveAttribute('aria-selected', 'true')
  })

  it('writes an expanded task row to the deep-link route and clears it when collapsed', async () => {
    const { router } = renderAt('/tasks/history')
    const row = await screen.findByRole('button', { name: /深链任务/ })

    fireEvent.click(row)
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/history/pipeline-42'))
    expect(await screen.findByRole('button', { name: /深链任务/ })).toHaveAttribute('aria-expanded', 'true')

    fireEvent.click(screen.getByRole('button', { name: /深链任务/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/history'))
    expect(screen.getByRole('button', { name: /深链任务/ })).toHaveAttribute('aria-expanded', 'false')
  })
})
