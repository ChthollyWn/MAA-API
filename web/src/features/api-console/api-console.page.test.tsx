import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { routeConfig } from '@/routes/router'
import { useAuth } from '@/stores/auth'
import { useLogBuffer } from '@/stores/log-buffer'
import { useRealtimeStatusStore } from '@/realtime/client'

const openApi = {
  openapi: '3.1.0',
  tags: [{ name: '测试接口', description: '用于测试的接口组' }],
  paths: {
    '/api/echo': {
      post: {
        tags: ['测试接口'], operationId: 'echoMessage', summary: '回显一条消息',
        requestBody: { required: true, content: { 'application/json': { schema: { type: 'object', required: ['message'], properties: { message: { type: 'string', example: 'hi', minLength: 5 }, note: { type: 'string', description: '附加说明' } } } } } },
      },
    },
  },
}
const originalRangeClientRects = Object.getOwnPropertyDescriptor(Range.prototype, 'getClientRects')
const originalRangeBoundingClientRect = Object.getOwnPropertyDescriptor(Range.prototype, 'getBoundingClientRect')

function restoreRangeMethod(name: 'getClientRects' | 'getBoundingClientRect', descriptor?: PropertyDescriptor) {
  if (descriptor) Object.defineProperty(Range.prototype, name, descriptor)
  else delete (Range.prototype as unknown as Record<string, unknown>)[name]
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', 'x-response-time-ms': '2.1' } })
}

function installNetwork(initialSnippets: Array<Record<string, unknown>> = []) {
  const requests: Request[] = []
  const snippets: Array<Record<string, unknown>> = [...initialSnippets]
  let failNextEcho = false
  let failNextRemoteSnippetFetch = false
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    requests.push(request.clone())
    const url = new URL(request.url)
    if (url.pathname === '/api/system/health') return json({ auth_enabled: false, status: 'ok', version: 'test' })
    if (url.pathname === '/openapi.json') return json(openApi)
    if (url.pathname === '/api/snippets' && url.origin !== window.location.origin && failNextRemoteSnippetFetch) {
      failNextRemoteSnippetFetch = false
      throw new TypeError('Failed to fetch: CORS blocked the remote response')
    }
    if (url.pathname === '/api/snippets' && request.method === 'GET') return json({ items: snippets, total: snippets.length })
    if (url.pathname === '/api/snippets' && request.method === 'POST') {
      const payload = JSON.parse(await request.clone().text()) as Record<string, unknown>
      const created = { id: 'favorite-1', path_params: {}, query: {}, headers: {}, body: null, ...payload, created_at: '2026-09-25T00:00:00Z', updated_at: '2026-09-25T00:00:00Z' }
      snippets.unshift(created)
      return json(created, 201)
    }
    if (url.pathname === '/api/snippets/favorite-1' && request.method === 'PUT') {
      const payload = JSON.parse(await request.clone().text()) as Record<string, unknown>
      Object.assign(snippets[0] ?? {}, payload, { updated_at: '2026-09-25T00:01:00Z' })
      return json(snippets[0])
    }
    if (url.pathname === '/api/snippets/favorite-1' && request.method === 'DELETE') {
      snippets.splice(0, 1)
      return new Response(null, { status: 204 })
    }
    if (url.pathname === '/api/echo' && request.method === 'POST') {
      if (failNextEcho) { failNextEcho = false; throw new Error('network offline') }
      return json({ echoed: true, pipeline_id: null })
    }
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return {
    requests, fetchMock,
    failNextEcho: () => { failNextEcho = true },
    failNextRemoteSnippetFetch: () => { failNextRemoteSnippetFetch = true },
  }
}

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  const router = createMemoryRouter(routeConfig, { initialEntries: [path] })
  render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>)
  return { router, queryClient }
}

describe('API console route and request workspace', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuth.getState().clearToken()
    Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 1280 })
    Object.defineProperty(Range.prototype, 'getClientRects', { configurable: true, value: () => Object.assign([], { item: () => null }) })
    Object.defineProperty(Range.prototype, 'getBoundingClientRect', { configurable: true, value: () => new DOMRect(0, 0, 0, 0) })
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    useLogBuffer.getState().clear()
    restoreRangeMethod('getClientRects', originalRangeClientRects)
    restoreRangeMethod('getBoundingClientRect', originalRangeBoundingClientRect)
  })

  it('loads its operation list from /openapi.json and opens the docs navigation', async () => {
    const network = installNetwork()
    renderAt('/more/api-console')

    expect(await screen.findByRole('heading', { name: 'API 调试台' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /回显一条消息/ })).toBeInTheDocument()
    expect(network.requests.some((request) => new URL(request.url).pathname === '/openapi.json')).toBe(true)
    expect(screen.getByRole('link', { name: 'OpenAPI 文档' })).toHaveAttribute('href', '/docs')
    expect(screen.getByRole('link', { name: 'ReDoc' })).toHaveAttribute('href', '/redoc')
    fireEvent.click(screen.getByText('接入指南'))
    expect(screen.getByRole('link', { name: '打开完整指南' })).toHaveAttribute('href', '/more/api-console/guide')
    expect(await screen.findByRole('heading', { name: '开放 API 接入指南' })).toBeInTheDocument()
    expect(screen.getByText(/仅凭 cookie 的请求只允许/)).toBeInTheDocument()
    const routeGuideLink = screen.getAllByRole('link', { name: /05-API规范与路由清单/ })[0]
    expect(routeGuideLink).toHaveAttribute('href', '/docs')
    expect(routeGuideLink).toHaveTextContent('/docs')
  })

  it('opens the same API console with the shared guide visible at its direct route', async () => {
    const network = installNetwork()
    renderAt('/more/api-console/guide')

    expect(await screen.findByRole('heading', { name: 'API 调试台' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: '开放 API 接入指南' })).toBeInTheDocument()
    expect(screen.getByText(/仅凭 cookie 的请求只允许/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '返回 API 调试台' })).toHaveAttribute('href', '/more/api-console')
    expect(await screen.findByRole('button', { name: /回显一条消息/ })).toBeInTheDocument()
    expect(network.requests.some((request) => new URL(request.url).pathname === '/openapi.json')).toBe(true)
  })

  it('shows the mobile request step at 375px and still sends a schema-invalid JSON body', async () => {
    const network = installNetwork()
    Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 375 })
    renderAt('/more/api-console')
    const operation = await screen.findByRole('button', { name: /回显一条消息/ })
    fireEvent.click(operation)

    expect(screen.getByRole('button', { name: '返回接口列表' })).toBeInTheDocument()
    expect(screen.getByTestId('api-console-grid').getAttribute('data-layout')).toBe('mobile')
    expect(screen.getByText('请求头').closest('details')).not.toHaveAttribute('open')
    expect(await screen.findByText(/请求体校验警告；仍可发送/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('临时 token（当前页）'), { target: { value: 'one-page-secret' } })
    fireEvent.click(screen.getByRole('button', { name: '发送请求' }))

    await waitFor(() => expect(network.requests.some((request) => request.method === 'POST' && new URL(request.url).pathname === '/api/echo')).toBe(true))
    const sent = network.requests.find((request) => request.method === 'POST' && new URL(request.url).pathname === '/api/echo')!
    expect(sent.headers.get('Authorization')).toBe('Bearer one-page-secret')
    expect(sent.headers.get('X-Request-Id')).toBeTruthy()
    expect(await sent.clone().text()).toContain('"message"')
    expect(await screen.findByRole('region', { name: '响应抽屉' })).toHaveTextContent('200 OK')
    expect(localStorage.getItem('one-page-secret')).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: '请求' }))
    expect(screen.queryByRole('region', { name: '响应抽屉' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '发送请求' })).toBeVisible()
  })

  it('keeps the three desktop columns available at a wide viewport', async () => {
    installNetwork()
    renderAt('/more/api-console')
    await screen.findByRole('heading', { name: 'API 调试台' })

    expect(screen.getByTestId('api-console-grid').getAttribute('data-layout')).toBe('desktop')
    expect(screen.getByRole('complementary', { name: '接口列表' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '请求构造器' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '响应与日志' })).toBeInTheDocument()
  })

  it('saves, renames, replays with the current session token, and deletes typed API snippets', async () => {
    const network = installNetwork()
    useAuth.getState().setToken('session-auth-secret')
    renderAt('/more/api-console')
    fireEvent.click(await screen.findByRole('button', { name: /回显一条消息/ }))
    fireEvent.change(screen.getByLabelText('收藏名称'), { target: { value: '回显调试' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(network.requests.some((request) => request.method === 'POST' && new URL(request.url).pathname === '/api/snippets')).toBe(true))
    const create = network.requests.find((request) => request.method === 'POST' && new URL(request.url).pathname === '/api/snippets')!
    const savedPayload = JSON.parse(await create.clone().text()) as Record<string, unknown>
    expect(savedPayload).toMatchObject({ name: '回显调试', method: 'POST', path: '/api/echo', body: { message: 'hi' } })
    expect(JSON.stringify(savedPayload)).not.toContain('session-auth-secret')
    expect(create.headers.get('X-Token')).toBe('session-auth-secret')

    fireEvent.change(screen.getByLabelText('收藏名称'), { target: { value: '回显调试改名' } })
    fireEvent.click(screen.getByRole('button', { name: '更新' }))
    await waitFor(() => expect(network.requests.some((request) => request.method === 'PUT' && new URL(request.url).pathname === '/api/snippets/favorite-1')).toBe(true))

    fireEvent.click(screen.getByRole('tab', { name: '收藏' }))
    fireEvent.click(await screen.findByRole('button', { name: '重放' }))
    fireEvent.click(screen.getByRole('button', { name: '发送请求' }))
    await waitFor(() => expect(network.requests.some((request) => request.method === 'POST' && new URL(request.url).pathname === '/api/echo')).toBe(true))
    const replay = network.requests.find((request) => request.method === 'POST' && new URL(request.url).pathname === '/api/echo')!
    expect(replay.headers.get('Authorization')).toBe('Bearer session-auth-secret')

    fireEvent.click(screen.getByRole('button', { name: '删除收藏 回显调试改名' }))
    await waitFor(() => expect(network.requests.some((request) => request.method === 'DELETE' && new URL(request.url).pathname === '/api/snippets/favorite-1')).toBe(true))
  })

  it('loads and mutates snippets on the selected remote service with the temporary console token', async () => {
    const network = installNetwork()
    vi.stubGlobal('WebSocket', undefined)
    renderAt('/more/api-console')
    await screen.findByRole('heading', { name: 'API 调试台' })
    fireEvent.change(screen.getByLabelText('临时 token（当前页）'), { target: { value: 'remote-temp-secret' } })
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'http://remote-maa.local:8002/base' } })

    await waitFor(() => expect(network.requests.some((request) => new URL(request.url).origin === 'http://remote-maa.local:8002' && new URL(request.url).pathname === '/api/snippets')).toBe(true))
    const remoteList = network.requests.find((request) => new URL(request.url).origin === 'http://remote-maa.local:8002' && new URL(request.url).pathname === '/api/snippets' && request.method === 'GET')!
    expect(remoteList.headers.get('X-Token')).toBe('remote-temp-secret')

    fireEvent.click(await screen.findByRole('button', { name: /回显一条消息/ }))
    fireEvent.change(screen.getByLabelText('收藏名称'), { target: { value: '远程收藏' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(network.requests.some((request) => request.method === 'POST' && new URL(request.url).origin === 'http://remote-maa.local:8002' && new URL(request.url).pathname === '/api/snippets')).toBe(true))
    const remoteCreate = network.requests.find((request) => request.method === 'POST' && new URL(request.url).origin === 'http://remote-maa.local:8002' && new URL(request.url).pathname === '/api/snippets')!
    expect(remoteCreate.headers.get('X-Token')).toBe('remote-temp-secret')

    fireEvent.change(screen.getByLabelText('收藏名称'), { target: { value: '远程收藏已改名' } })
    fireEvent.click(screen.getByRole('button', { name: '更新' }))
    await waitFor(() => expect(network.requests.some((request) => request.method === 'PUT' && new URL(request.url).origin === 'http://remote-maa.local:8002' && new URL(request.url).pathname === '/api/snippets/favorite-1')).toBe(true))
    const remoteUpdate = network.requests.find((request) => request.method === 'PUT' && new URL(request.url).origin === 'http://remote-maa.local:8002' && new URL(request.url).pathname === '/api/snippets/favorite-1')!
    expect(remoteUpdate.headers.get('X-Token')).toBe('remote-temp-secret')

    fireEvent.click(screen.getByRole('tab', { name: '收藏' }))
    fireEvent.click(await screen.findByRole('button', { name: '删除收藏 远程收藏已改名' }))
    await waitFor(() => expect(network.requests.some((request) => request.method === 'DELETE' && new URL(request.url).origin === 'http://remote-maa.local:8002' && new URL(request.url).pathname === '/api/snippets/favorite-1')).toBe(true))
    const remoteDelete = network.requests.find((request) => request.method === 'DELETE' && new URL(request.url).origin === 'http://remote-maa.local:8002' && new URL(request.url).pathname === '/api/snippets/favorite-1')!
    expect(remoteDelete.headers.get('X-Token')).toBe('remote-temp-secret')
    expect(network.requests.filter((request) => new URL(request.url).pathname.startsWith('/api/snippets') && request.method !== 'GET').every((request) => new URL(request.url).origin === 'http://remote-maa.local:8002')).toBe(true)
  })

  it('shows remote snippet CORS failures instead of hiding them', async () => {
    const network = installNetwork()
    vi.stubGlobal('WebSocket', undefined)
    renderAt('/more/api-console')
    await screen.findByRole('heading', { name: 'API 调试台' })
    fireEvent.change(screen.getByLabelText('临时 token（当前页）'), { target: { value: 'remote-temp-secret' } })
    network.failNextRemoteSnippetFetch()
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'http://remote-maa.local:8002/base' } })
    await waitFor(() => expect(network.requests.some((request) => new URL(request.url).origin === 'http://remote-maa.local:8002' && new URL(request.url).pathname === '/api/snippets')).toBe(true))

    fireEvent.click(screen.getByRole('tab', { name: '收藏' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('CORS blocked')
  })

  it('opens a remote console WebSocket from Base URL without changing shared realtime state or logs', async () => {
    class FakeSocket {
      static instances: FakeSocket[] = []
      onopen: ((event: Event) => void) | null = null
      onmessage: ((event: MessageEvent<string>) => void) | null = null
      onerror: ((event: Event) => void) | null = null
      onclose: ((event: CloseEvent) => void) | null = null
      sent: string[] = []
      constructor(readonly url: string) { FakeSocket.instances.push(this) }
      send(value: string) { this.sent.push(value) }
      close() {}
    }
    const network = installNetwork()
    useAuth.getState().setToken('socket-secret')
    const previousStatus = useRealtimeStatusStore.getState().status
    vi.stubGlobal('WebSocket', FakeSocket)
    renderAt('/more/api-console')
    await screen.findByRole('heading', { name: 'API 调试台' })
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'http://maa-box.local:8002/base' } })

    await waitFor(() => expect(FakeSocket.instances.length).toBeGreaterThan(1))
    const remote = FakeSocket.instances.at(-1)!
    expect(remote.url).toBe('ws://maa-box.local:8002/api/ws?token=socket-secret')
    remote.onopen?.(new Event('open'))
    expect(JSON.parse(remote.sent[0])).toMatchObject({ type: 'subscribe', data: { channels: expect.arrayContaining(['log', 'pipeline_status']) } })
    remote.onmessage?.({ data: JSON.stringify({ type: 'log', data: { id: 900, source: 'service', content: 'isolated' } }) } as MessageEvent<string>)
    expect(useRealtimeStatusStore.getState().status).toBe(previousStatus)
    expect(useLogBuffer.getState().bySource).toEqual({})
    expect(network.requests.some((request) => new URL(request.url).origin === 'http://maa-box.local:8002')).toBe(true)
  })

  it('keeps network failures in local history without adding the active token', async () => {
    const network = installNetwork()
    useAuth.getState().setToken('current-session-secret')
    renderAt('/more/api-console')
    fireEvent.click(await screen.findByRole('button', { name: /回显一条消息/ }))
    network.failNextEcho()
    fireEvent.click(screen.getByRole('button', { name: '发送请求' }))

    await waitFor(() => expect(network.requests.some((request) => request.method === 'POST' && new URL(request.url).pathname === '/api/echo')).toBe(true))
    await waitFor(() => expect(JSON.parse(localStorage.getItem('maa.api-console.history') ?? '[]')).toHaveLength(1))
    const saved = JSON.parse(localStorage.getItem('maa.api-console.history') ?? '[]') as Array<Record<string, unknown>>
    expect(saved[0]).toMatchObject({ status: 0, method: 'POST', path: '/api/echo' })
    expect(JSON.stringify(saved[0])).not.toContain('current-session-secret')
  })

  it('prefills the existing schedule editor directly from a saved pipeline favorite', async () => {
    const pipelineFavorite = {
      id: 'favorite-pipeline', name: '每日刷图', method: 'POST', path: '/api/pipelines',
      path_params: {}, query: {}, headers: {}, body: { tasks: [{ name: 'Fight', stage: '1-7' }] },
      created_at: '2026-09-25T00:00:00Z', updated_at: '2026-09-25T00:00:00Z',
    }
    installNetwork([pipelineFavorite])
    renderAt('/more/api-console')
    fireEvent.click(await screen.findByRole('tab', { name: '收藏' }))
    fireEvent.click(await screen.findByRole('button', { name: '转为定时任务' }))

    expect(await screen.findByRole('heading', { name: '新建定时任务' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Fight' })).toBeInTheDocument()
    expect(localStorage.getItem('maa.api-console.schedule-template')).toBeNull()
  })
})
