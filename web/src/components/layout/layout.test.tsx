import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AppShell } from '@/components/layout/AppShell'
import LoginPage from '@/routes/login'
import { useAuth } from '@/stores/auth'
import { THEME_STORAGE_KEY, useUiStore } from '@/stores/ui'

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

function renderWithRouter(element: React.ReactNode, initialEntries = ['/']) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route element={element} path="/">
          <Route element={<LocationProbe />} path="login" />
          <Route element={<LocationProbe />} path="more/api-console/*" />
        </Route>
          <Route element={<div>Application page <LocationProbe /></div>} path="/dashboard" />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('App shell and entry routes', () => {
  beforeEach(() => {
    document.head.innerHTML = '<meta name="theme-color" content="#101411" />'
    localStorage.clear()
    useAuth.getState().clearToken()
    useUiStore.getState().setTheme('dark')
  })

  afterEach(() => {
    cleanup()
    document.head.replaceChildren()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('renders all five labeled primary navigation destinations with safe-area padding', () => {
    renderWithRouter(<AppShell />)

    for (const label of ['首页', '任务', '日志', 'Agent', '更多']) {
      expect(screen.getByRole('link', { name: label })).toBeTruthy()
    }
    const navigation = screen.getByRole('navigation', { name: '主导航' })
    expect(navigation.className).toContain('pb-safe')
    expect(document.querySelector('main')?.className).toContain('3.5rem+env(safe-area-inset-bottom')
    expect(screen.getByText(/Service Worker 离线缓存/)).toBeTruthy()
  })

  it('gives the API console guide route the same full-width shell as the workbench', () => {
    renderWithRouter(<AppShell />, ['/more/api-console/guide'])

    expect(screen.getByRole('main').className).toContain('max-w-none')
    expect(screen.getByTestId('location')).toHaveTextContent('/more/api-console/guide')
  })

  it('persists theme selection and synchronizes the document theme and browser chrome color', () => {
    renderWithRouter(<AppShell />)
    fireEvent.mouseDown(screen.getByRole('tab', { name: '浅色主题' }), { button: 0, ctrlKey: false })

    expect(useUiStore.getState().theme).toBe('light')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute('content')).toBe('#f4f6f3')
  })

  it('sends the returnTo path for an unauthenticated login and masks the entered token by default', async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async (input) => {
      const request = input instanceof Request ? input : new Request(input)
      if (request.url.endsWith('/api/system/health')) {
        return new Response(JSON.stringify({ auth_enabled: true, status: 'ok', version: '1' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
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
      expect(fetchMock.mock.calls.some(([input]) =>
        input instanceof Request &&
        input.url.endsWith('/api/system/auth/cookie') &&
        input.method === 'POST' &&
        input.headers.get('X-Token') === 'secret',
      )).toBe(true)
    })
    expect(await screen.findByTestId('location')).toHaveTextContent('/dashboard?tab=queue')
    expect(useAuth.getState().token).toBe('secret')
  })

  it('clears auth cookie and navigates to login when the user logs out', async () => {
    useAuth.getState().setToken('saved-token')
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)
    renderWithRouter(<AppShell />)

    fireEvent.click(screen.getByRole('button', { name: '登出' }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) =>
      input instanceof Request &&
      input.url.endsWith('/api/system/auth/cookie') &&
      input.method === 'DELETE' &&
      input.headers.get('X-Token') === 'saved-token',
    )).toBe(true))
    expect(useAuth.getState().token).toBeNull()
    expect(await screen.findByTestId('location')).toHaveTextContent('/login')
  })

  it('shows a 403 inline and preserves the saved token', async () => {
    useAuth.getState().setToken('existing-token')
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async (input) => {
      const url = input instanceof Request ? input.url : String(input)
      if (url.includes('/api/system/health')) {
        return new Response(JSON.stringify({ auth_enabled: true, status: 'ok', version: '1' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({ error: { code: 'FORBIDDEN', message: 'forbidden' } }), {
        status: 403,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)
    renderWithRouter(<LoginPage />)

    const input = await screen.findByLabelText('访问 token')
    fireEvent.change(input, { target: { value: 'bad-token' } })
    fireEvent.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('服务拒绝了此认证请求')
    expect(useAuth.getState().token).toBe('existing-token')
  })
})
