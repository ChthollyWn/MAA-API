import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, MemoryRouter, Route, RouterProvider, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import MorePage from '@/routes/more'
import { routeConfig } from '@/routes/router'
import { useAuth } from '@/stores/auth'
import { THEME_STORAGE_KEY, useUiStore } from '@/stores/ui'

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
    if (pathname === '/api/system/auth/cookie') return new Response(null, { status: 204 })
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
    if (pathname === '/api/confirmations') return json({ items: [], total: 0, page: 1, size: 20 })
    if (pathname === '/api/updates/status') return json({ running: null, last_checked_at: null })
    if (pathname === '/api/updates') return json({ items: [], total: 0, page: 1, size: 20 })
    if (pathname === '/api/settings/schema') return json({ items: [] })
    if (pathname === '/api/settings') return json({ items: {} })
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderAt(path: string | { pathname: string; state?: unknown }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const router = createMemoryRouter(routeConfig, { initialEntries: [path] })
  render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>)
  return { router, queryClient }
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function renderMore() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/more']}>
        <Routes>
          <Route path="/more" element={<MorePage />} />
          <Route path="/login" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('more page', () => {
  beforeEach(() => {
    localStorage.clear()
    document.head.innerHTML = '<meta name="theme-color" content="#000000" />'
    useAuth.getState().clearToken()
    useUiStore.getState().setTheme('dark')
    installApiMock()
  })

  afterEach(() => {
    cleanup()
    document.head.replaceChildren()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('groups the secondary destinations and no longer lists schedules', async () => {
    renderMore()

    for (const heading of ['运维', '系统', '外观', '关于']) {
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
    }
    expect(screen.getByRole('link', { name: /热更新/ })).toHaveAttribute('href', '/more/updates')
    expect(screen.getByRole('link', { name: /Agent 审计/ })).toHaveAttribute('href', '/more/audit')
    expect(screen.getByRole('link', { name: /API 调试台/ })).toHaveAttribute('href', '/more/api-console')
    expect(screen.getByRole('link', { name: /设置/ })).toHaveAttribute('href', '/more/settings')
    expect(screen.queryByRole('link', { name: /定时任务/ })).not.toBeInTheDocument()
    expect(await screen.findByText('test')).toBeInTheDocument()
  })

  it('keeps protected routes for the API console and the Agent audit', () => {
    const children = routeConfig.find((route) => route.path === '/')?.children ?? []
    expect(children.some((route) => route.path === 'more/api-console')).toBe(true)
    expect(children.some((route) => route.path === 'more/audit')).toBe(true)
  })

  it('persists the appearance choice and synchronizes the document theme and browser chrome color', () => {
    renderMore()

    fireEvent.click(screen.getByRole('radio', { name: '浅色' }))

    expect(useUiStore.getState().theme).toBe('light')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute('content')).toBe('#f6f7f9')
  })

  it('confirms before logging out, then clears the auth cookie and navigates to login', async () => {
    useAuth.getState().setToken('saved-token')
    const fetchMock = installApiMock()
    renderMore()

    fireEvent.click(screen.getByRole('button', { name: '退出登录' }))
    const dialog = await screen.findByRole('alertdialog', { name: '退出登录？' })
    expect(fetchMock.mock.calls.some(([input]) => input instanceof Request && input.method === 'DELETE')).toBe(false)
    fireEvent.click(within(dialog).getByRole('button', { name: '退出登录' }))

    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) =>
      input instanceof Request &&
      input.url.endsWith('/api/system/auth/cookie') &&
      input.method === 'DELETE' &&
      input.headers.get('X-Token') === 'saved-token',
    )).toBe(true))
    await waitFor(() => expect(useAuth.getState().token).toBeNull())
    expect(await screen.findByTestId('location')).toHaveTextContent('/login')
  })

  it('keeps the token and explains a rejected logout', async () => {
    useAuth.getState().setToken('saved-token')
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : new Request(String(input))
      if (request.method === 'DELETE') return json({ error: { code: 'FORBIDDEN', message: 'forbidden' } }, 403)
      return json({ auth_enabled: true, status: 'ok', version: 'test' })
    }))
    renderMore()

    fireEvent.click(screen.getByRole('button', { name: '退出登录' }))
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '退出登录' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('服务拒绝了登出请求，请检查凭据后重试。')
    expect(useAuth.getState().token).toBe('saved-token')
  })
})

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

  it('opens a child page directly and supports browser back and forward', async () => {
    const { router } = renderAt('/more/settings')

    expect(await screen.findByRole('heading', { name: '设置' })).toBeInTheDocument()
    const moreTab = within(screen.getByRole('navigation', { name: '主导航' })).getByRole('link', { name: '更多' })
    expect(moreTab).toHaveAttribute('aria-current', 'page')
    expect(moreTab.className).toContain('text-primary-text')
    await act(async () => router.navigate('/more'))
    expect(await screen.findByRole('heading', { name: '更多', level: 1 })).toBeInTheDocument()
    await act(async () => router.navigate('/more/updates'))
    expect(await screen.findByRole('heading', { name: '热更新', level: 1 })).toBeInTheDocument()
    await act(async () => router.navigate(-1))
    expect(await screen.findByRole('heading', { name: '更多', level: 1 })).toBeInTheDocument()
    await act(async () => router.navigate(1))
    expect(await screen.findByRole('heading', { name: '热更新', level: 1 })).toBeInTheDocument()
  })

  it('redirects the old schedules address into the task area and keeps the navigation state', async () => {
    const state = { apiConsolePrefill: { template: { tasks: [{ name: 'Fight', params: {} }] } } }
    const { router } = renderAt({ pathname: '/more/schedules', state })

    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/schedules'))
    expect(router.state.location.state).toEqual(state)
  })
})
