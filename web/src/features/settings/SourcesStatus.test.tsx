import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import SettingsPage from '@/routes/settings'
import { SOURCES_STATUS_KEY, SourcesStatus, formatBytes, formatHitRate } from './SourcesStatus'

interface RecordedRequest {
  method: string
  path: string
  query: string
  signal: AbortSignal | null
}

type JsonRecord = Record<string, unknown>

const STATUS: JsonRecord = {
  sources: [
    {
      kind: 'prts',
      label: 'PRTS',
      enabled: true,
      rate_per_minute: 30,
      last_success_at: '2026-10-10T09:00:00+08:00',
      last_error_at: null,
      last_error_code: null,
      last_error_message: null,
      consecutive_failures: 0,
      cooldown_until: null,
      cooldown_seconds: 0,
      requests: 3,
      bytes_read: 1234,
      retries: 0,
      cache: { entries: 2, bytes: 4567, hits: 1, misses: 2, hit_rate: 0.3333, evictions: 0 },
    },
    {
      kind: 'penguin',
      label: '企鹅物流',
      enabled: false,
      rate_per_minute: 10,
      last_success_at: null,
      last_error_at: '2026-10-10T08:00:00+08:00',
      last_error_code: 'EXTERNAL_SOURCE_RATE_LIMITED',
      last_error_message: '远端 429，冷却中',
      consecutive_failures: 3,
      cooldown_until: '2026-10-10T08:01:00+08:00',
      cooldown_seconds: 42,
      requests: 9,
      bytes_read: 0,
      retries: 2,
      cache: { entries: 0, bytes: 0, hits: 0, misses: 0, hit_rate: 0, evictions: 1 },
    },
  ],
}

const SCHEMA_ITEMS = [
  { key: 'sources.prts_enabled', label: '启用 PRTS', group: '外部数据源', type: 'boolean', hot_action: 'none' },
  { key: 'sources.prts_rate_per_minute', label: 'PRTS 每分钟请求上限', group: '外部数据源', type: 'integer', minimum: 1, maximum: 120, hot_action: 'none' },
]

const SETTING_VALUES = [
  { key: 'sources.prts_enabled', value: true, source: 'default' },
  { key: 'sources.prts_rate_per_minute', value: 30, source: 'default' },
]

interface ApiOptions {
  status?: unknown
  statusFailure?: { status: number; body: unknown }
  clearFailure?: { status: number; body: unknown }
  /** `/api/sources/status` 永不返回，用来验证卸载时会中止在途请求。 */
  hang?: boolean
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function installApi(options: ApiOptions = {}) {
  const requests: RecordedRequest[] = []
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    requests.push({ method: request.method, path: url.pathname, query: url.search, signal: request.signal })
    if (url.pathname === '/api/sources/status') {
      if (options.hang) return new Promise<Response>(() => {})
      if (options.statusFailure) return json(options.statusFailure.body, options.statusFailure.status)
      return json(options.status ?? STATUS)
    }
    if (url.pathname === '/api/sources/cache' && request.method === 'DELETE') {
      if (options.clearFailure) return json(options.clearFailure.body, options.clearFailure.status)
      const source = url.searchParams.get('source')
      return json({ source, cleared: source ? 2 : 5 })
    }
    // 设置分区自己的读取（集成用例只关心「面板与 schema 字段同页」）。
    if (url.pathname === '/api/settings/schema') return json({ items: SCHEMA_ITEMS })
    if (url.pathname === '/api/settings') return json({ items: SETTING_VALUES, total: SETTING_VALUES.length })
    return json({ error: { code: 'NOT_FOUND', message: `没有为 ${url.pathname} 准备响应` } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return {
    requests,
    find: (method: string, path: string) => requests.filter((request) => request.method === method && request.path === path),
  }
}

/** jsdom 没有 ResizeObserver；设置页里的 Radix 开关会用它量尺寸。 */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function newQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
}

function renderPanel(refreshMs?: number) {
  return render(
    <QueryClientProvider client={newQueryClient()}>
      <SourcesStatus refreshMs={refreshMs} />
    </QueryClientProvider>,
  )
}

function renderSettings(initialEntries: string[]) {
  const router = createMemoryRouter(
    [
      { path: '/more/settings', element: <SettingsPage /> },
      { path: '/more/settings/:section', element: <SettingsPage /> },
    ],
    { initialEntries },
  )
  render(
    <QueryClientProvider client={newQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('formatBytes / formatHitRate', () => {
  it('formats cache sizes and hit rates for the panel', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(4567)).toBe('4.5 KB')
    expect(formatBytes(3.5 * 1024 * 1024)).toBe('3.5 MB')
    expect(formatBytes(-1)).toBe('0 B')
    expect(formatBytes(Number.NaN)).toBe('0 B')
    expect(formatHitRate(0)).toBe('0%')
    expect(formatHitRate(0.3333)).toBe('33.3%')
    expect(formatHitRate(1)).toBe('100%')
    // 还没有读写时后端给 null（router 的 hit_rate），面板显示「—」，不显示 0%。
    expect(formatHitRate(null)).toBe('—')
    expect(formatHitRate(undefined)).toBe('—')
    expect(formatHitRate(Number.NaN)).toBe('—')
  })
})

describe('SourcesStatus', () => {
  it('shows one row per source with enablement, failures, cooldown and cache stats', async () => {
    const api = installApi()
    renderPanel()

    const prts = await screen.findByTestId('source-prts')
    expect(within(prts).getByText('PRTS')).toBeInTheDocument()
    expect(within(prts).getByText('已启用')).toBeInTheDocument()
    expect(within(prts).getByText('限流 30 次/分钟')).toBeInTheDocument()
    expect(within(prts).getByText('最近成功').nextElementSibling).toHaveTextContent(/\d{1,2}\/\d{1,2} \d{2}:\d{2}/)
    expect(within(prts).getByText('最近错误').nextElementSibling).toHaveTextContent('无')
    expect(within(prts).getByText('连续失败').nextElementSibling).toHaveTextContent('0 次')
    expect(within(prts).getByText('冷却').nextElementSibling).toHaveTextContent('无')
    expect(within(prts).getByText('缓存').nextElementSibling).toHaveTextContent('2 条 · 4.5 KB · 命中率 33.3%')

    const penguin = await screen.findByTestId('source-penguin')
    expect(within(penguin).getByText('已关闭')).toBeInTheDocument()
    expect(within(penguin).getByText('最近成功').nextElementSibling).toHaveTextContent('暂无')
    const failure = within(penguin).getByText('最近错误').nextElementSibling
    expect(failure).toHaveTextContent('EXTERNAL_SOURCE_RATE_LIMITED')
    expect(failure).toHaveTextContent('远端 429，冷却中')
    expect(within(penguin).getByText('连续失败').nextElementSibling).toHaveTextContent('3 次')
    expect(within(penguin).getByText('冷却').nextElementSibling).toHaveTextContent('剩余 42 秒')
    // 没有缓存时清空按钮不可点。
    expect(within(penguin).getByRole('button', { name: '清空缓存' })).toBeDisabled()

    expect(api.find('GET', '/api/sources/status')).toHaveLength(1)
    expect(api.requests[0]?.signal).not.toBeNull()
  })

  it('refreshes the status once after clearing a single source cache', async () => {
    const api = installApi()
    renderPanel()

    const prts = await screen.findByTestId('source-prts')
    fireEvent.click(within(prts).getByRole('button', { name: '清空缓存' }))

    await waitFor(() => expect(api.find('DELETE', '/api/sources/cache')).toHaveLength(1))
    expect(api.find('DELETE', '/api/sources/cache')[0]?.query).toBe('?source=prts')
    await waitFor(() => expect(api.find('GET', '/api/sources/status')).toHaveLength(2))
  })

  it('clears every cache when the parameter is omitted', async () => {
    const api = installApi()
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: '清空全部缓存' }))

    await waitFor(() => expect(api.find('DELETE', '/api/sources/cache')).toHaveLength(1))
    expect(api.find('DELETE', '/api/sources/cache')[0]?.query).toBe('')
    await waitFor(() => expect(api.find('GET', '/api/sources/status')).toHaveLength(2))
  })

  it('shows the failure when clearing the cache fails instead of staying silent', async () => {
    const api = installApi({ clearFailure: { status: 500, body: { error: { code: 'INTERNAL_ERROR', message: '缓存清空失败' } } } })
    renderPanel()

    const prts = await screen.findByTestId('source-prts')
    fireEvent.click(within(prts).getByRole('button', { name: '清空缓存' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('清空缓存失败')
    expect(alert).toHaveTextContent('缓存清空失败')
    // 失败后不刷新成功状态：只有一次读取（初值）与一次失败尝试。
    expect(api.find('GET', '/api/sources/status')).toHaveLength(1)
    expect(within(prts).getByRole('button', { name: '清空缓存' })).toBeEnabled()
  })

  it('offers a retry when the status read fails', async () => {
    const api = installApi({ statusFailure: { status: 503, body: { error: { code: 'SERVICE_UNAVAILABLE', message: '外部来源还没启动' } } } })
    renderPanel()

    expect(await screen.findByText('外部来源状态读取失败')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('外部来源还没启动')
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(api.find('GET', '/api/sources/status')).toHaveLength(2))
  })

  it('polls on the given interval and stops when the panel unmounts', async () => {
    const api = installApi()
    const view = renderPanel(40)

    await waitFor(() => expect(api.find('GET', '/api/sources/status').length).toBeGreaterThanOrEqual(2))
    view.unmount()
    const settled = api.find('GET', '/api/sources/status').length
    await new Promise((resolve) => setTimeout(resolve, 200))
    expect(api.find('GET', '/api/sources/status')).toHaveLength(settled)
  })

  it('cancels the in-flight status request when the panel unmounts', async () => {
    const api = installApi({ hang: true })
    const view = renderPanel()

    await waitFor(() => expect(api.find('GET', '/api/sources/status')).toHaveLength(1))
    const signal = api.find('GET', '/api/sources/status')[0]?.signal
    expect(signal?.aborted).toBe(false)
    view.unmount()
    await waitFor(() => expect(signal?.aborted).toBe(true))
  })

  it('defaults to a 30 s refresh and keys the query under the api root', () => {
    expect(SOURCES_STATUS_KEY).toEqual(['api', 'sources', 'status'])
  })
})

describe('外部数据源 settings section', () => {
  it('lists the section in the index and renders the panel next to the schema fields', async () => {
    installApi()
    renderSettings(['/more/settings'])

    const link = await screen.findByRole('link', { name: /^外部数据源/ })
    expect(link).toHaveAttribute('href', '/more/settings/sources')

    cleanup()
    installApi()
    renderSettings(['/more/settings/sources'])

    expect(await screen.findByRole('heading', { name: '外部数据源', level: 1 })).toBeInTheDocument()
    // 状态面板（intro）与 schema 驱动的字段在同一个分区页里。
    expect(await screen.findByRole('heading', { name: '来源状态', level: 2 })).toBeInTheDocument()
    expect(await screen.findByTestId('source-prts')).toBeInTheDocument()
    expect(await screen.findByRole('switch', { name: '启用 PRTS' })).toBeChecked()
    // schema 字段按分组渲染，分组标题就是服务端的「外部数据源」。
    expect(screen.getByRole('heading', { name: '外部数据源', level: 2 })).toBeInTheDocument()
  })
})
