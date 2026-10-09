import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider, type Location } from 'react-router'
import { copyText } from '@/lib/clipboard'
import { routeConfig } from '@/routes/router'
import { useAuth } from '@/stores/auth'
import { useLogBuffer } from '@/stores/log-buffer'
import { useRealtimeStatusStore } from '@/realtime/client'

vi.mock('@/lib/clipboard', () => ({ copyText: vi.fn(async () => true) }))

// 调试台页面与 CodeMirror 都是懒加载分块，全量并行时冷加载要好几秒，JSON 校验还有 250ms 防抖；
// 所有异步等待都给足预算，不依赖默认 1 秒的 findBy / waitFor。
const SLOW = { timeout: 10_000 }

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
    '/api/items/{item_id}': {
      get: {
        tags: ['测试接口'], operationId: 'readItem', summary: '读取一项',
        parameters: [
          { name: 'item_id', in: 'path', required: true, schema: { type: 'string', example: 'item-1' } },
          { name: 'verbose', in: 'query', required: false, description: '返回完整字段', schema: { type: 'boolean', default: true } },
        ],
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
    if (url.pathname.startsWith('/api/items/') && request.method === 'GET') return json({ id: decodeURIComponent(url.pathname.split('/').at(-1) ?? '') })
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return {
    requests, fetchMock,
    failNextEcho: () => { failNextEcho = true },
    failNextRemoteSnippetFetch: () => { failNextRemoteSnippetFetch = true },
  }
}

type Network = ReturnType<typeof installNetwork>

function sentTo(network: Network, method: string, pathname: string, origin = window.location.origin): Request[] {
  return network.requests.filter((request) => {
    const url = new URL(request.url)
    return request.method === method && url.pathname === pathname && url.origin === origin
  })
}

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  const router = createMemoryRouter(routeConfig, { initialEntries: [path] })
  const locations: Location[] = []
  router.subscribe((state) => { locations.push(state.location) })
  render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>)
  return { router, locations }
}

function setViewport(width: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width })
}

function storedValues(): string {
  return Array.from({ length: localStorage.length }, (_, index) => {
    const key = localStorage.key(index) ?? ''
    return `${key}=${localStorage.getItem(key) ?? ''}`
  }).join('\n')
}

async function openOperation(name: RegExp) {
  fireEvent.click(await screen.findByRole('button', { name }, SLOW))
}

async function openEnvironment() {
  fireEvent.click(await screen.findByRole('button', { name: '请求环境' }, SLOW))
  return screen.findByRole('dialog', { name: '请求环境' }, SLOW)
}

async function closeEnvironment(sheet: HTMLElement) {
  fireEvent.click(within(sheet).getByRole('button', { name: '完成' }))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '请求环境' })).not.toBeInTheDocument(), SLOW)
}

async function confirmFavoriteDeletion(name: string) {
  fireEvent.click(await screen.findByRole('button', { name: `收藏 ${name} 的更多操作` }, SLOW))
  const sheet = await screen.findByRole('dialog', { name }, SLOW)
  fireEvent.click(within(sheet).getByRole('button', { name: '删除收藏' }))
  return screen.findByRole('alertdialog', { name: `删除收藏「${name}」？` }, SLOW)
}

/** jsdom 的 TouchEvent 不接受普通对象作为触点；直接给事件挂上 React 会读取的触点列表。 */
function swipe(element: Element, fromY: number, toY: number) {
  const start = new Event('touchstart', { bubbles: true, cancelable: true })
  Object.defineProperty(start, 'touches', { value: [{ clientY: fromY }] })
  fireEvent(element, start)
  const end = new Event('touchend', { bubbles: true, cancelable: true })
  Object.defineProperty(end, 'changedTouches', { value: [{ clientY: toY }] })
  fireEvent(element, end)
}

describe('API console', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuth.getState().clearToken()
    setViewport(1440)
    // 专用 WebSocket 默认不可用（离线）；需要它的用例自己换成假实现。
    vi.stubGlobal('WebSocket', undefined)
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

  it('K1 loads operations from /openapi.json, keeps the /docs and /redoc links, and opens the full-page guide', async () => {
    const network = installNetwork()
    const { locations } = renderAt('/more/api-console')

    expect(await screen.findByRole('heading', { name: 'API 调试台', level: 1 }, SLOW)).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /回显一条消息/ }, SLOW)).toBeInTheDocument()
    expect(network.requests.some((request) => new URL(request.url).pathname === '/openapi.json')).toBe(true)
    expect(screen.getByRole('link', { name: 'OpenAPI 文档' })).toHaveAttribute('href', '/docs')
    expect(screen.getByRole('link', { name: 'ReDoc' })).toHaveAttribute('href', '/redoc')
    // 指南是独立页面，调试台里不再内嵌浮层预览。
    expect(screen.queryByRole('heading', { name: '开放 API 接入指南' })).not.toBeInTheDocument()

    const guideLink = screen.getByRole('link', { name: '接入指南' })
    expect(guideLink).toHaveAttribute('href', '/more/api-console/guide')
    fireEvent.click(guideLink)

    expect(await screen.findByRole('heading', { name: '接入指南', level: 1 }, SLOW)).toBeInTheDocument()
    expect(locations.at(-1)?.pathname).toBe('/more/api-console/guide')
    expect(screen.getByRole('heading', { name: '开放 API 接入指南' })).toBeInTheDocument()
    expect(screen.getByText(/仅凭 cookie 的请求只允许/)).toBeInTheDocument()
    const routeGuideLink = screen.getAllByRole('link', { name: /05-API规范与路由清单/ })[0]
    expect(routeGuideLink).toHaveAttribute('href', '/docs')
    expect(routeGuideLink).toHaveTextContent('/docs')
  })

  it('K2 opens the guide at its direct route and goes back to the console', async () => {
    const network = installNetwork()
    const { locations } = renderAt('/more/api-console/guide')

    expect(await screen.findByRole('heading', { name: '接入指南', level: 1 }, SLOW)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '开放 API 接入指南' })).toBeInTheDocument()
    expect(screen.getByText(/仅凭 cookie 的请求只允许/)).toBeInTheDocument()
    expect(screen.queryByTestId('api-console-grid')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '返回' }))

    expect(await screen.findByRole('heading', { name: 'API 调试台', level: 1 }, SLOW)).toBeInTheDocument()
    expect(locations.at(-1)?.pathname).toBe('/more/api-console')
    expect(await screen.findByRole('button', { name: /回显一条消息/ }, SLOW)).toBeInTheDocument()
    expect(network.requests.some((request) => new URL(request.url).pathname === '/openapi.json')).toBe(true)
  })

  it('K3 walks the narrow workflow: search, request step, a warned body still sends with the temporary token, and the response drawer', async () => {
    setViewport(375)
    const network = installNetwork()
    renderAt('/more/api-console')

    await screen.findByRole('button', { name: /回显一条消息/ }, SLOW)
    expect(screen.getByTestId('api-console-grid')).toHaveAttribute('data-layout', 'mobile')
    const search = screen.getByRole('searchbox', { name: '搜索接口' })
    fireEvent.change(search, { target: { value: 'no-such-endpoint' } })
    expect(screen.getByText('还没有匹配的接口')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /回显一条消息/ })).not.toBeInTheDocument()
    fireEvent.change(search, { target: { value: 'echo' } })
    fireEvent.click(screen.getByRole('button', { name: /回显一条消息/ }))

    expect(screen.getByRole('button', { name: '返回接口列表' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '请求构造器' })).toBeInTheDocument()
    expect(screen.queryByRole('complementary', { name: '接口列表' })).not.toBeInTheDocument()
    expect(screen.getByText('请求头').closest('details')).not.toHaveAttribute('open')
    expect(await screen.findByText(/请求体校验警告；仍可发送/, undefined, SLOW)).toBeInTheDocument()

    const environment = await openEnvironment()
    fireEvent.change(within(environment).getByLabelText('临时 token（当前页）'), { target: { value: 'one-page-secret' } })
    await closeEnvironment(environment)
    expect(screen.getByRole('button', { name: '请求环境' })).toHaveAccessibleDescription(/临时 token/)

    fireEvent.click(screen.getByRole('button', { name: '发送请求' }))
    await waitFor(() => expect(sentTo(network, 'POST', '/api/echo')).toHaveLength(1), SLOW)
    const sent = sentTo(network, 'POST', '/api/echo')[0]
    expect(sent.headers.get('Authorization')).toBe('Bearer one-page-secret')
    expect(sent.headers.get('X-Request-Id')).toBeTruthy()
    expect(await sent.clone().text()).toContain('"message"')

    const drawer = await screen.findByRole('region', { name: '响应抽屉' }, SLOW)
    expect(drawer).toHaveTextContent('200 OK')
    await waitFor(() => expect(drawer).toHaveTextContent('echoed'), SLOW)
    expect(storedValues()).not.toContain('one-page-secret')

    fireEvent.click(within(drawer).getByRole('radio', { name: /日志/ }))
    expect(within(drawer).getByText(/专用 WebSocket/)).toBeInTheDocument()
    fireEvent.click(within(drawer).getByRole('radio', { name: '响应' }))

    const handle = within(drawer).getByTestId('response-drawer-handle')
    swipe(handle, 500, 300)
    expect(within(drawer).getByRole('button', { name: '退出全屏' })).toBeInTheDocument()
    swipe(handle, 300, 500)
    expect(within(drawer).getByRole('button', { name: '全屏显示响应' })).toBeInTheDocument()
    fireEvent.click(within(drawer).getByRole('button', { name: '收起响应' }))
    expect(within(drawer).getByRole('button', { name: '展开响应' })).toBeInTheDocument()
    expect(drawer).toHaveTextContent('200 OK')
    expect(within(drawer).queryByRole('radiogroup')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '发送请求' })).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: '返回接口列表' }))
    expect(screen.getByRole('complementary', { name: '接口列表' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /回显一条消息/ })).toBeInTheDocument()
  })

  it('K3 fills path parameters and sends only the enabled query parameters', async () => {
    setViewport(375)
    const network = installNetwork()
    renderAt('/more/api-console')
    await openOperation(/读取一项/)

    expect(screen.getByLabelText('item_id')).toHaveValue('item-1')
    const verbose = screen.getByRole('switch', { name: '启用 query 参数 verbose' })
    expect(verbose).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(screen.getByRole('button', { name: '发送请求' }))
    await waitFor(() => expect(sentTo(network, 'GET', '/api/items/item-1')).toHaveLength(1), SLOW)
    expect(new URL(sentTo(network, 'GET', '/api/items/item-1')[0].url).search).toBe('')

    fireEvent.click(verbose)
    expect(screen.getByLabelText('verbose')).toHaveValue('true')
    fireEvent.change(screen.getByLabelText('item_id'), { target: { value: 'item 2' } })
    fireEvent.click(screen.getByRole('button', { name: '发送请求' }))
    await waitFor(() => expect(sentTo(network, 'GET', '/api/items/item%202')).toHaveLength(1), SLOW)
    expect(new URL(sentTo(network, 'GET', '/api/items/item%202')[0].url).searchParams.get('verbose')).toBe('true')

    // 历史里存的是填好参数的实际路径，仍能找回带路径模板的接口并恢复参数。
    fireEvent.click(screen.getByRole('button', { name: '返回接口列表' }))
    fireEvent.click(screen.getByRole('radio', { name: '历史' }))
    fireEvent.click(within(screen.getByRole('list', { name: '本机请求历史' })).getByRole('button', { name: /\/api\/items\/item%202/ }))
    expect(screen.getByLabelText('item_id')).toHaveValue('item 2')
    expect(screen.getByRole('switch', { name: '启用 query 参数 verbose' })).toHaveAttribute('aria-checked', 'true')
  })

  it('K3 formats the body from the button above the editor and exports cURL with a token placeholder by default', async () => {
    setViewport(375)
    const network = installNetwork()
    useAuth.getState().setToken('curl-session-secret')
    localStorage.setItem('maa.api-console.history', JSON.stringify([{
      id: 'history-1', method: 'POST', path: '/api/echo', path_params: {}, query: {}, headers: {},
      body: '{"message":"hello world"}', status: 200, elapsed_ms: 4, timestamp: Date.now(), request_id: 'request-1',
    }]))
    renderAt('/more/api-console')

    fireEvent.click(await screen.findByRole('radio', { name: '历史' }, SLOW))
    fireEvent.click(within(screen.getByRole('list', { name: '本机请求历史' })).getByRole('button', { name: /\/api\/echo/ }))
    fireEvent.click(await screen.findByRole('button', { name: '格式化' }, SLOW))

    const curlPanel = screen.getByText('cURL 导出').closest('details')!
    fireEvent.click(screen.getByText('cURL 导出'))
    const preview = within(curlPanel).getByTestId('curl-preview')
    expect(preview).toHaveTextContent('Authorization: Bearer $MAA_TOKEN')
    expect(preview).toHaveTextContent('"message": "hello world"')
    expect(preview).not.toHaveTextContent('curl-session-secret')
    fireEvent.click(within(curlPanel).getByRole('button', { name: '复制 cURL' }))
    await waitFor(() => expect(copyText).toHaveBeenCalledTimes(1), SLOW)
    expect(vi.mocked(copyText).mock.calls[0][0]).toContain('$MAA_TOKEN')
    expect(vi.mocked(copyText).mock.calls[0][0]).not.toContain('curl-session-secret')

    fireEvent.click(within(curlPanel).getByRole('switch', { name: '包含真实 token（仅供自用）' }))
    expect(preview).toHaveTextContent('Bearer curl-session-secret')

    fireEvent.click(screen.getByRole('button', { name: '发送请求' }))
    await waitFor(() => expect(sentTo(network, 'POST', '/api/echo')).toHaveLength(1), SLOW)
    expect(await sentTo(network, 'POST', '/api/echo')[0].clone().text()).toBe('{\n  "message": "hello world"\n}')
  })

  it('K4 uses three columns from 1280px and keeps the stepped workflow below it', async () => {
    installNetwork()
    setViewport(1100)
    renderAt('/more/api-console')
    await screen.findByRole('button', { name: /回显一条消息/ }, SLOW)

    expect(screen.getByTestId('api-console-grid')).toHaveAttribute('data-layout', 'mobile')
    expect(screen.getByRole('complementary', { name: '接口列表' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '请求构造器' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '响应与日志' })).not.toBeInTheDocument()

    act(() => {
      setViewport(1440)
      window.dispatchEvent(new Event('resize'))
    })
    expect(screen.getByTestId('api-console-grid')).toHaveAttribute('data-layout', 'desktop')
    expect(screen.getByRole('complementary', { name: '接口列表' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '请求构造器' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '响应与日志' })).toBeInTheDocument()

    act(() => {
      setViewport(1279)
      window.dispatchEvent(new Event('resize'))
    })
    expect(screen.getByTestId('api-console-grid')).toHaveAttribute('data-layout', 'mobile')
  })

  it('K5 saves, renames, replays with the session token, and deletes a snippet only after confirmation', async () => {
    const network = installNetwork()
    useAuth.getState().setToken('session-auth-secret')
    renderAt('/more/api-console')
    await openOperation(/回显一条消息/)
    fireEvent.change(screen.getByLabelText('收藏名称'), { target: { value: '回显调试' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(sentTo(network, 'POST', '/api/snippets')).toHaveLength(1), SLOW)
    const create = sentTo(network, 'POST', '/api/snippets')[0]
    const savedPayload = JSON.parse(await create.clone().text()) as Record<string, unknown>
    expect(savedPayload).toMatchObject({ name: '回显调试', method: 'POST', path: '/api/echo', body: { message: 'hi' } })
    expect(JSON.stringify(savedPayload)).not.toContain('session-auth-secret')
    expect(create.headers.get('X-Token')).toBe('session-auth-secret')

    fireEvent.change(screen.getByLabelText('收藏名称'), { target: { value: '回显调试改名' } })
    fireEvent.click(await screen.findByRole('button', { name: '更新' }, SLOW))
    await waitFor(() => expect(sentTo(network, 'PUT', '/api/snippets/favorite-1')).toHaveLength(1), SLOW)

    fireEvent.click(screen.getByRole('radio', { name: '收藏' }))
    fireEvent.click(await screen.findByRole('button', { name: /^回显调试改名/ }, SLOW))
    fireEvent.click(screen.getByRole('button', { name: '发送请求' }))
    await waitFor(() => expect(sentTo(network, 'POST', '/api/echo')).toHaveLength(1), SLOW)
    expect(sentTo(network, 'POST', '/api/echo')[0].headers.get('Authorization')).toBe('Bearer session-auth-secret')

    const cancelled = await confirmFavoriteDeletion('回显调试改名')
    fireEvent.click(within(cancelled).getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument(), SLOW)
    expect(sentTo(network, 'DELETE', '/api/snippets/favorite-1')).toHaveLength(0)

    const confirmDialog = await confirmFavoriteDeletion('回显调试改名')
    expect(sentTo(network, 'DELETE', '/api/snippets/favorite-1')).toHaveLength(0)
    fireEvent.click(within(confirmDialog).getByRole('button', { name: '删除收藏' }))
    await waitFor(() => expect(sentTo(network, 'DELETE', '/api/snippets/favorite-1')).toHaveLength(1), SLOW)
    await waitFor(() => expect(screen.queryByRole('button', { name: /^回显调试改名/ })).not.toBeInTheDocument(), SLOW)
  })

  it('K6 reads and writes snippets on a remote Base URL with the temporary console token', async () => {
    const network = installNetwork()
    const remote = 'http://remote-maa.local:8002'
    renderAt('/more/api-console')
    await screen.findByRole('heading', { name: 'API 调试台', level: 1 }, SLOW)

    const environment = await openEnvironment()
    fireEvent.change(within(environment).getByLabelText('临时 token（当前页）'), { target: { value: 'remote-temp-secret' } })
    fireEvent.change(within(environment).getByLabelText('Base URL'), { target: { value: `${remote}/base` } })
    await waitFor(() => expect(sentTo(network, 'GET', '/api/snippets', remote)).not.toHaveLength(0), SLOW)
    expect(sentTo(network, 'GET', '/api/snippets', remote)[0].headers.get('X-Token')).toBe('remote-temp-secret')
    await closeEnvironment(environment)
    expect(screen.getByRole('button', { name: '请求环境' })).toHaveAccessibleDescription(/remote-maa\.local:8002/)

    await openOperation(/回显一条消息/)
    fireEvent.change(screen.getByLabelText('收藏名称'), { target: { value: '远程收藏' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(sentTo(network, 'POST', '/api/snippets', remote)).toHaveLength(1), SLOW)
    expect(sentTo(network, 'POST', '/api/snippets', remote)[0].headers.get('X-Token')).toBe('remote-temp-secret')

    fireEvent.change(screen.getByLabelText('收藏名称'), { target: { value: '远程收藏已改名' } })
    fireEvent.click(await screen.findByRole('button', { name: '更新' }, SLOW))
    await waitFor(() => expect(sentTo(network, 'PUT', '/api/snippets/favorite-1', remote)).toHaveLength(1), SLOW)
    expect(sentTo(network, 'PUT', '/api/snippets/favorite-1', remote)[0].headers.get('X-Token')).toBe('remote-temp-secret')

    fireEvent.click(screen.getByRole('radio', { name: '收藏' }))
    const confirmDialog = await confirmFavoriteDeletion('远程收藏已改名')
    fireEvent.click(within(confirmDialog).getByRole('button', { name: '删除收藏' }))
    await waitFor(() => expect(sentTo(network, 'DELETE', '/api/snippets/favorite-1', remote)).toHaveLength(1), SLOW)
    expect(sentTo(network, 'DELETE', '/api/snippets/favorite-1', remote)[0].headers.get('X-Token')).toBe('remote-temp-secret')
    expect(network.requests
      .filter((request) => new URL(request.url).pathname.startsWith('/api/snippets') && request.method !== 'GET')
      .every((request) => new URL(request.url).origin === remote)).toBe(true)
  })

  it('K7 shows remote snippet CORS failures instead of hiding them', async () => {
    const network = installNetwork()
    renderAt('/more/api-console')
    await screen.findByRole('heading', { name: 'API 调试台', level: 1 }, SLOW)

    const environment = await openEnvironment()
    fireEvent.change(within(environment).getByLabelText('临时 token（当前页）'), { target: { value: 'remote-temp-secret' } })
    network.failNextRemoteSnippetFetch()
    fireEvent.change(within(environment).getByLabelText('Base URL'), { target: { value: 'http://remote-maa.local:8002/base' } })
    await waitFor(() => expect(sentTo(network, 'GET', '/api/snippets', 'http://remote-maa.local:8002')).not.toHaveLength(0), SLOW)
    await closeEnvironment(environment)

    fireEvent.click(screen.getByRole('radio', { name: '收藏' }))
    const failure = await screen.findByText(/收藏读取失败/, undefined, SLOW)
    expect(failure.closest('[role="alert"]')).toHaveTextContent('CORS blocked')
  })

  it('K8 opens an isolated console WebSocket for a remote Base URL without touching shared realtime state or logs', async () => {
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
    await screen.findByRole('heading', { name: 'API 调试台', level: 1 }, SLOW)

    const environment = await openEnvironment()
    fireEvent.change(within(environment).getByLabelText('Base URL'), { target: { value: 'http://maa-box.local:8002/base' } })
    await closeEnvironment(environment)

    await waitFor(() => expect(FakeSocket.instances.length).toBeGreaterThan(1), SLOW)
    const remote = FakeSocket.instances.at(-1)!
    expect(remote.url).toBe('ws://maa-box.local:8002/api/ws?token=socket-secret')
    act(() => remote.onopen?.(new Event('open')))
    expect(JSON.parse(remote.sent[0])).toMatchObject({ type: 'subscribe', data: { channels: expect.arrayContaining(['log', 'pipeline_status']) } })
    act(() => {
      remote.onmessage?.({ data: JSON.stringify({ type: 'log', data: { id: 900, source: 'service', content: 'isolated' } }) } as MessageEvent<string>)
      remote.onmessage?.({ data: JSON.stringify({ type: 'log_batch', data: { records: [], truncated: true } }) } as MessageEvent<string>)
    })
    expect(await screen.findByText('日志历史不完整，较早的部分可能缺失', undefined, SLOW)).toBeInTheDocument()
    expect(useRealtimeStatusStore.getState().status).toBe(previousStatus)
    expect(useLogBuffer.getState().bySource).toEqual({})
    expect(network.requests.some((request) => new URL(request.url).origin === 'http://maa-box.local:8002')).toBe(true)
  })

  it('K9 keeps network failures in local history without the active token', async () => {
    const network = installNetwork()
    useAuth.getState().setToken('current-session-secret')
    renderAt('/more/api-console')
    await openOperation(/回显一条消息/)
    network.failNextEcho()
    fireEvent.click(screen.getByRole('button', { name: '发送请求' }))

    await waitFor(() => expect(sentTo(network, 'POST', '/api/echo')).toHaveLength(1), SLOW)
    await waitFor(() => expect(JSON.parse(localStorage.getItem('maa.api-console.history') ?? '[]')).toHaveLength(1), SLOW)
    const saved = JSON.parse(localStorage.getItem('maa.api-console.history') ?? '[]') as Array<Record<string, unknown>>
    expect(saved[0]).toMatchObject({ status: 0, method: 'POST', path: '/api/echo' })
    expect(JSON.stringify(saved[0])).not.toContain('current-session-secret')
    expect(await screen.findByText('请求失败：network offline', undefined, SLOW)).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: '响应与日志' })).getAllByText('NETWORK_ERROR').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('radio', { name: '历史' }))
    const historyList = screen.getByRole('list', { name: '本机请求历史' })
    expect(within(historyList).getByRole('button', { name: /\/api\/echo/ })).toHaveTextContent('失败')
  })

  it('K10 hands a saved pipeline snippet to the new-schedule editor through navigation state', async () => {
    const pipelineFavorite = {
      id: 'favorite-pipeline', name: '每日刷图', method: 'POST', path: '/api/pipelines',
      path_params: {}, query: {}, headers: {}, body: { tasks: [{ name: 'Fight', stage: '1-7' }] },
      created_at: '2026-09-25T00:00:00Z', updated_at: '2026-09-25T00:00:00Z',
    }
    installNetwork([pipelineFavorite])
    const { locations } = renderAt('/more/api-console')
    fireEvent.click(await screen.findByRole('radio', { name: '收藏' }, SLOW))
    fireEvent.click(await screen.findByRole('button', { name: '收藏 每日刷图 的更多操作' }, SLOW))
    const sheet = await screen.findByRole('dialog', { name: '每日刷图' }, SLOW)
    fireEvent.click(within(sheet).getByRole('button', { name: '转为定时任务' }))

    // 直接进入新建定时任务，而不是经旧的 /more/schedules 重定向过去。
    await waitFor(() => expect(locations.some((location) => location.pathname !== '/more/api-console')).toBe(true), SLOW)
    const handoff = locations.find((location) => location.pathname !== '/more/api-console')!
    expect(handoff.pathname).toBe('/tasks/schedules/new')
    expect(handoff.state).toEqual({ apiConsolePrefill: { template: [{ name: 'Fight', stage: '1-7' }] } })
    expect(localStorage.getItem('maa.api-console.schedule-template')).toBeNull()
  })
})
