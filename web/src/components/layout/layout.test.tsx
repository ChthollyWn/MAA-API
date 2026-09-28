import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AppShell } from '@/components/layout/AppShell'
import LoginPage from '@/routes/login'
import { BrandMark } from '@/components/layout/BrandMark'
import { useAuth } from '@/stores/auth'

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function stubMedia(matches: (query: string) => boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: matches(query),
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
}

function renderWithRouter(element: React.ReactNode, initialEntries = ['/']) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route element={element} path="/">
            <Route index element={<label>搜索日志<input type="search" /></label>} />
            <Route element={<LocationProbe />} path="login" />
          </Route>
          <Route element={<div>Application page <LocationProbe /></div>} path="/dashboard" />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('App shell navigation', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuth.getState().clearToken()
    vi.stubGlobal('fetch', vi.fn(async () => json({ auth_enabled: true, status: 'ok', version: '1' })))
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    delete document.documentElement.dataset.tabbar
  })

  it('shows five labelled destinations in the bottom tab bar on phones', () => {
    renderWithRouter(<AppShell />)

    const navigation = screen.getByRole('navigation', { name: '主导航' })
    const expected = { 首页: '/', 任务: '/tasks', 日志: '/logs', Agent: '/agent', 更多: '/more' }
    for (const [label, href] of Object.entries(expected)) {
      expect(within(navigation).getByRole('link', { name: label })).toHaveAttribute('href', href)
    }
    expect(within(navigation).getAllByRole('link')).toHaveLength(5)
    expect(navigation.className).toContain('pb-safe')
    expect(screen.getByRole('main').className).toContain('var(--nav-offset)')
    expect(within(navigation).getByRole('link', { name: '首页' })).toHaveAttribute('aria-current', 'page')
  })

  it('switches to a sidebar that also lists the secondary destinations on desktop', () => {
    stubMedia((query) => query.includes('min-width: 1024px'))
    renderWithRouter(<AppShell />)

    const navigations = screen.getAllByRole('navigation', { name: '主导航' })
    expect(navigations).toHaveLength(1)
    const sidebar = navigations[0]!
    for (const label of ['首页', '任务', '日志', 'Agent', '更多', '热更新', 'Agent 审计', 'API 调试台', '设置']) {
      expect(within(sidebar).getByRole('link', { name: label })).toBeInTheDocument()
    }
    expect(screen.getByRole('link', { name: 'MAA-API 首页' })).toHaveAttribute('href', '/')
    expect(screen.getByRole('main').className).toContain('pl-60')
  })

  it('hides the tab bar while a text field is focused on a touch device', async () => {
    stubMedia((query) => query.includes('pointer: coarse'))
    renderWithRouter(<AppShell />)
    const input = screen.getByLabelText('搜索日志')

    act(() => input.focus())
    await waitFor(() => expect(screen.queryByRole('navigation', { name: '主导航' })).not.toBeInTheDocument())
    expect(document.documentElement.dataset.tabbar).toBe('hidden')

    act(() => input.blur())
    expect(await screen.findByRole('navigation', { name: '主导航' })).toBeInTheDocument()
    expect(document.documentElement.dataset.tabbar).toBe('visible')
  })

  it('keeps the tab bar when a field is focused with a mouse and keyboard', () => {
    renderWithRouter(<AppShell />)

    act(() => screen.getByLabelText('搜索日志').focus())

    expect(screen.getByRole('navigation', { name: '主导航' })).toBeInTheDocument()
  })
})

describe('Login page', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuth.getState().clearToken()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('sends the returnTo path for an unauthenticated login and masks the entered token by default', async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async (input) => {
      const request = input instanceof Request ? input : new Request(input)
      if (request.url.endsWith('/api/system/health')) return json({ auth_enabled: true, status: 'ok', version: '1' })
      return new Response(null, { status: 204 })
    })
    vi.stubGlobal('fetch', fetchMock)
    renderWithRouter(<LoginPage />, ['/login?returnTo=%2Fdashboard%3Ftab%3Dqueue'])

    const input = await screen.findByLabelText('访问 token')
    expect(input.getAttribute('type')).toBe('password')
    fireEvent.change(input, { target: { value: 'secret' } })
    fireEvent.click(screen.getByRole('button', { name: '显示 token' }))
    expect(input.getAttribute('type')).toBe('text')

    fireEvent.click(screen.getByRole('button', { name: '登录' }))
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([request]) =>
        request instanceof Request &&
        request.url.endsWith('/api/system/auth/cookie') &&
        request.method === 'POST' &&
        request.headers.get('X-Token') === 'secret',
      )).toBe(true)
    })
    expect(await screen.findByTestId('location')).toHaveTextContent('/dashboard?tab=queue')
    expect(useAuth.getState().token).toBe('secret')
  })

  it('shows a 403 inline and preserves the saved token', async () => {
    useAuth.getState().setToken('existing-token')
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input)
      if (url.includes('/api/system/health')) return json({ auth_enabled: true, status: 'ok', version: '1' })
      return json({ error: { code: 'FORBIDDEN', message: 'forbidden' } }, 403)
    }))
    renderWithRouter(<LoginPage />)

    const input = await screen.findByLabelText('访问 token')
    fireEvent.change(input, { target: { value: 'bad-token' } })
    fireEvent.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('服务拒绝了此认证请求')
    expect(useAuth.getState().token).toBe('existing-token')
  })

  it('explains that offline caching and push need HTTPS when served over plain HTTP', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ auth_enabled: true, status: 'ok', version: '1' })))
    renderWithRouter(<LoginPage />, ['/login'])

    expect(await screen.findByText(/Service Worker 离线缓存/)).toBeInTheDocument()
  })

  it('offers a retry when the service cannot be reached', async () => {
    let healthy = false
    vi.stubGlobal('fetch', vi.fn(async () => (healthy
      ? json({ auth_enabled: true, status: 'ok', version: '1' })
      : json({ error: { code: 'SERVICE_UNAVAILABLE', message: 'down' } }, 503))))
    renderWithRouter(<LoginPage />, ['/login'])

    expect(await screen.findByText('无法连接到 MAA-API')).toBeInTheDocument()
    healthy = true
    fireEvent.click(screen.getByRole('button', { name: '重试连接' }))
    expect(await screen.findByLabelText('访问 token')).toBeInTheDocument()
  })
})

describe('BrandMark', () => {
  it('renders the brand artwork and keeps the caller size classes', () => {
    const { container } = render(<BrandMark className="size-14 shadow-float" />)

    // 品牌标现在直接读插画；换成自己画几何会让图标与品牌标再次分叉。
    const image = container.querySelector('img')
    expect(image).not.toBeNull()
    expect(image).toHaveAttribute('src', '/logo.webp')
    expect(image?.className).toContain('size-14')
    expect(image?.className).toContain('shadow-float')
    // 装饰性元素：读屏不该念出它。
    expect(image).toHaveAttribute('aria-hidden', 'true')
  })
})

