import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { Toaster } from '@/components/ui/toaster'
import { routeConfig } from '@/routes/router'
import { useAuth } from '@/stores/auth'

vi.mock('@/realtime/RealtimeProvider', () => ({
  useRealtimeStatus: () => ({ status: 'CONNECTED', reconnectAttempt: 0, rtt: null, truncated: false, closeCode: null, error: null }),
}))

type Json = Record<string, unknown>

interface RecordedRequest {
  method: string
  path: string
  body: unknown
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function navNode(id: string, title: string, overrides: Json = {}): Json {
  return {
    id,
    title,
    kind: 'screen',
    origin: 'seed',
    recognizer: {
      rois: [[0, 0, 1, 1]],
      max_distance: 10,
      description: '',
      generic: false,
    },
    exemplars: [],
    created_at: '2026-11-09T00:00:00Z',
    updated_at: '2026-11-09T00:00:00Z',
    ...overrides,
  }
}

const EDGE_ID = '11111111-1111-4111-8111-111111111111'

function navEdge(overrides: Json = {}): Json {
  return {
    id: EDGE_ID,
    from_node: 'home',
    to_node: 'infrast',
    action: { type: 'tap', x: 0.8164, y: 0.875 },
    action_hash: 'hash-1',
    cost_ms: 1500,
    success_count: 2,
    fail_count: 0,
    last_used_at: null,
    origin: 'seed',
    enabled: true,
    created_at: '2026-11-09T00:00:00Z',
    updated_at: '2026-11-09T00:00:00Z',
    ...overrides,
  }
}

function installApi() {
  const requests: RecordedRequest[] = []
  let edges: Json[] = [navEdge()]

  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    const body = init?.body ? JSON.parse(String(init.body)) : request.body ? JSON.parse(await request.text()) : null
    requests.push({ method: request.method, path: url.pathname, body })
    if (url.pathname === '/api/system/health') return json({ auth_enabled: false, status: 'ok', version: 'test' })
    if (url.pathname === '/api/system/auth/cookie') return new Response(null, { status: 204 })
    if (url.pathname === '/api/confirmations') return json({ items: [], total: 0, page: 1, size: 20 })
    if (url.pathname === '/api/agent/nav/graph' && request.method === 'GET') {
      return json({
        nodes: [navNode('home', '主界面'), navNode('infrast', '基建', { origin: 'learned' })],
        edges,
        enabled: true,
        learn: true,
        vision_available: false,
      })
    }
    const edgeMatch = /^\/api\/agent\/nav\/edges\/([^/]+)$/.exec(url.pathname)
    if (edgeMatch && request.method === 'PUT') {
      const current = edges.find((edge) => edge.id === edgeMatch[1])
      if (!current) return json(navEdge({ id: edgeMatch[1], ...(body ?? {}) }))
      const updated = { ...current, ...(body ?? {}) }
      edges = edges.map((edge) => (edge.id === edgeMatch[1] ? updated : edge))
      return json(updated)
    }
    if (edgeMatch && request.method === 'DELETE') {
      edges = edges.filter((edge) => edge.id !== edgeMatch[1])
      return new Response(null, { status: 204 })
    }
    if (url.pathname === '/api/agent/tools/where_am_i/invoke' && request.method === 'POST') {
      return json({
        node: { id: 'infrast', title: '基建', kind: 'screen' },
        method: 'hash',
        distance: 1.5,
        confidence: 0.92,
        candidates: [],
      })
    }
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { requests, fetchMock, setEdges: (next: Json[]) => { edges = next } }
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(routeConfig, { initialEntries: ['/more/nav'] })
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster />
    </QueryClientProvider>,
  )
  return { router, queryClient }
}

describe('M86 界面导航图页', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuth.getState().clearToken()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('renders nodes and edges', async () => {
    const api = installApi()
    renderPage()

    expect(await screen.findByRole('heading', { level: 1, name: '界面导航图' })).toBeInTheDocument()
    expect(await screen.findByText('2 个界面 · 1 条跳转')).toBeInTheDocument()
    expect(screen.getAllByText('主界面').length).toBeGreaterThan(0)
    expect(screen.getAllByText('基建').length).toBeGreaterThan(0)
    expect(screen.getByRole('cell', { name: '点击 (0.8164, 0.875)' })).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: /启用/ })).toBeChecked()

    const graphRequests = api.requests.filter((item) => item.path === '/api/agent/nav/graph')
    expect(graphRequests.length).toBeGreaterThan(0)
    expect(graphRequests[0].method).toBe('GET')
  })

  it('toggles an edge', async () => {
    const api = installApi()
    renderPage()

    fireEvent.click(await screen.findByRole('switch', { name: /启用/ }))

    await waitFor(() => {
      expect(
        api.requests.some(
          (item) => item.method === 'PUT' && item.path === `/api/agent/nav/edges/${EDGE_ID}` && JSON.stringify(item.body) === JSON.stringify({ enabled: false }),
        ),
      ).toBe(true)
    })
    expect(await screen.findByText('已停用跳转')).toBeInTheDocument()
  })

  it('deletes an edge after confirm', async () => {
    const api = installApi()
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '删除跳转' }))
    const dialog = await screen.findByRole('alertdialog', { name: '删除这条跳转？' })
    expect(dialog).toHaveTextContent('删除后导航不会再走这条路，需要时可以重新新建。')
    fireEvent.click(within(dialog).getByRole('button', { name: '删除跳转' }))

    await waitFor(() => {
      expect(
        api.requests.some((item) => item.method === 'DELETE' && item.path === `/api/agent/nav/edges/${EDGE_ID}`),
      ).toBe(true)
    })
    expect(await screen.findByText('已删除跳转')).toBeInTheDocument()
  })

  it('creates a tap edge', async () => {
    const api = installApi()
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '新建跳转' }))
    fireEvent.change(await screen.findByLabelText('从'), { target: { value: 'infrast' } })
    fireEvent.change(screen.getByLabelText('到'), { target: { value: 'home' } })
    fireEvent.change(screen.getByLabelText('x'), { target: { value: '0.5' } })
    fireEvent.change(screen.getByLabelText('y'), { target: { value: '0.25' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => {
      const created = api.requests.find(
        (item) => item.method === 'PUT' && /^\/api\/agent\/nav\/edges\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(item.path),
      )
      expect(created).toBeTruthy()
      expect(created?.body).toMatchObject({
        from_node: 'infrast',
        to_node: 'home',
        action: { type: 'tap', x: 0.5, y: 0.25 },
      })
    })
  })

  it('shows where_am_i result', async () => {
    const api = installApi()
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '识别当前画面' }))

    expect(await screen.findByText('当前画面：基建')).toBeInTheDocument()
    const invoke = api.requests.find(
      (item) => item.method === 'POST' && item.path === '/api/agent/tools/where_am_i/invoke',
    )
    expect(invoke?.body).toEqual({ arguments: {} })
  })
})
