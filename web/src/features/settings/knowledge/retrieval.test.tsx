import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import SettingsPage from '@/routes/settings'
import { Toaster } from '@/components/ui/toaster'
import { useAuth } from '@/stores/auth'

interface RecordedRequest {
  method: string
  path: string
  query: URLSearchParams
  body: unknown
}

type Json = Record<string, unknown>
type SourceStatus = 'idle' | 'indexing' | 'error'

interface SourceState {
  id: string
  kind: string
  title: string
  enabled: boolean
  setting_enabled: boolean | null
  searchable_by_default: boolean
  trusted: boolean
  license: string | null
  status: SourceStatus
  error: string | null
  doc_count: number
  chunk_count: number
  vector_count: number
  last_indexed_at: string | null
  config: Json
}

function defaultSources(): SourceState[] {
  return [
    {
      id: 'builtin',
      kind: 'builtin',
      title: '内置知识',
      enabled: true,
      setting_enabled: null,
      searchable_by_default: true,
      trusted: true,
      license: null,
      status: 'idle',
      error: null,
      doc_count: 12,
      chunk_count: 34,
      vector_count: 7,
      last_indexed_at: '2026-11-02T08:00:00Z',
      config: {},
    },
    {
      id: 'user',
      kind: 'user',
      title: '自定义知识',
      enabled: true,
      setting_enabled: null,
      searchable_by_default: true,
      trusted: true,
      license: null,
      status: 'idle',
      error: null,
      doc_count: 3,
      chunk_count: 8,
      vector_count: 0,
      last_indexed_at: '2026-11-02T08:05:00Z',
      config: {},
    },
  ]
}

function defaultEmbedding(): Json {
  return {
    provider: 'none',
    model: null,
    status: 'disabled',
    error: null,
    total_chunks: 0,
    embedded_chunks: 0,
  }
}

function hit(): Json {
  return {
    chunk_id: 31,
    document_id: '0b0d9b0d9b0d9b0d',
    source_id: 'builtin',
    source_title: '内置知识',
    trusted: true,
    title: '掉落与刷图效率',
    heading: '常驻最优图的判断',
    url: 'https://example.com/knowledge/drop-rate',
    version: '9f2c41d07a6e2b13',
    fetched_at: '2026-11-02T08:00:00Z',
    license: null,
    snippet: '…以 1-7 刷固源岩为推荐做法…',
    text: '…以 1-7 刷固源岩为推荐做法…',
    score: 0.016393,
    fts_rank: 1,
    vector_rank: null,
  }
}

function searchResult(q: string): Json {
  const matched = q.includes('固源岩')
  return {
    query: q,
    top_k: 8,
    enabled: true,
    vector: 'disabled',
    vector_model: null,
    sources: ['builtin', 'user'],
    skipped_sources: [],
    took_ms: matched ? 4 : 2,
    hits: matched ? [hit()] : [],
  }
}

interface ApiOptions {
  sources?: SourceState[]
  embedding?: Json
}

function installApi(options: ApiOptions = {}) {
  const sources = (options.sources ?? defaultSources()).map((source) => ({ ...source }))
  const embedding: Json = { ...defaultEmbedding(), ...(options.embedding ?? {}) }
  const requests: RecordedRequest[] = []
  /** 重建后的 GET 次数：第一次仍回 indexing，第二次才恢复 idle。 */
  let reindexing: string | null = null
  let indexingReads = 0

  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    const text = request.method === 'GET' || request.method === 'HEAD' ? '' : await request.clone().text()
    const body = text ? (JSON.parse(text) as Json) : undefined
    requests.push({ method: request.method, path: url.pathname, query: url.searchParams, body })

    if (url.pathname === '/api/agent/retrieval/sources' && request.method === 'GET') {
      if (reindexing) {
        const source = sources.find((item) => item.id === reindexing)
        if (source?.status === 'indexing') {
          if (indexingReads === 0) {
            indexingReads += 1
          } else {
            source.status = 'idle'
            source.last_indexed_at = '2026-11-02T09:00:00Z'
            reindexing = null
          }
        }
      }
      return json({ items: sources, embedding })
    }

    if (url.pathname === '/api/agent/retrieval/search' && request.method === 'GET') {
      return json(searchResult(url.searchParams.get('q') ?? ''))
    }

    if (url.pathname === '/api/agent/knowledge' && request.method === 'GET') {
      return json({ items: [], total: 0 })
    }

    const reindex = /^\/api\/agent\/retrieval\/sources\/([^/]+)\/reindex$/.exec(url.pathname)
    if (reindex && request.method === 'POST') {
      const source = sources.find((item) => item.id === decodeURIComponent(reindex[1]!))
      if (source) {
        source.status = 'indexing'
        source.error = null
        reindexing = source.id
        indexingReads = 0
      }
      return json({ source_id: source?.id ?? '', status: 'indexing' }, 202)
    }

    const match = /^\/api\/agent\/retrieval\/sources\/([^/]+)$/.exec(url.pathname)
    if (match && request.method === 'PUT') {
      const source = sources.find((item) => item.id === decodeURIComponent(match[1]!))
      if (!source) return json({ error: { code: 'KNOWLEDGE_SOURCE_NOT_FOUND', message: '来源不存在' } }, 404)
      if (body) Object.assign(source, body)
      return json(source)
    }

    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)

  return {
    requests,
    puts: () => requests.filter((request) => request.method === 'PUT'),
    reindexes: () => requests.filter((request) => request.method === 'POST'),
    searches: () => requests.filter((request) => request.path === '/api/agent/retrieval/search'),
    source: (id: string) => sources.find((item) => item.id === id),
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function renderRetrieval(initialEntry: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(
    [
      { path: '/more/settings', element: <SettingsPage /> },
      { path: '/more/settings/:section', element: <SettingsPage /> },
    ],
    { initialEntries: [initialEntry] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster />
    </QueryClientProvider>,
  )
  return { router, queryClient }
}

/** jsdom 没有 ResizeObserver；Radix 开关会用它量尺寸。 */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  localStorage.clear()
  useAuth.getState().clearToken()
  Element.prototype.scrollIntoView = vi.fn()
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const SOURCES_PATH = '/more/settings/knowledge?tab=sources'

describe('retrieval settings section', () => {
  it('renders sources with counts and trust badges', async () => {
    installApi()
    renderRetrieval(SOURCES_PATH)

    expect(await screen.findByText('内置知识')).toBeInTheDocument()
    expect(screen.getByText('自定义知识')).toBeInTheDocument()
    expect(screen.getByText('2 个来源')).toBeInTheDocument()
    expect(screen.getByText('12 篇 · 34 段 · 向量 7 段')).toBeInTheDocument()
    expect(screen.getByText('3 篇 · 8 段 · 向量 0 段')).toBeInTheDocument()
    expect(screen.getAllByText('可信')).toHaveLength(2)
    expect(screen.getAllByText('已建索引')).toHaveLength(2)
    expect(screen.getByText('向量检索未启用，只用全文检索')).toBeInTheDocument()
  })

  it('toggling a source puts enabled and toasts', async () => {
    const network = installApi()
    renderRetrieval(SOURCES_PATH)

    const row = within(await screen.findByTestId('source-builtin'))
    fireEvent.click(row.getByRole('switch', { name: '启用「内置知识」' }))

    await waitFor(() => expect(network.puts()).toHaveLength(1))
    expect(network.puts()[0]!.path).toBe('/api/agent/retrieval/sources/builtin')
    expect(network.puts()[0]!.body).toEqual({ enabled: false })
    expect(await screen.findByText('已停用「内置知识」')).toBeInTheDocument()
    await waitFor(() => expect(network.source('builtin')?.enabled).toBe(false))
  })

  it('reindex posts and polls while indexing', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const network = installApi()
    renderRetrieval(SOURCES_PATH)

    const row = within(await screen.findByTestId('source-builtin'))
    fireEvent.click(row.getByRole('button', { name: '重建索引' }))

    await waitFor(() => expect(network.reindexes()).toHaveLength(1))
    expect(network.reindexes()[0]!.path).toBe('/api/agent/retrieval/sources/builtin/reindex')
    expect(await screen.findByText('已开始重建索引')).toBeInTheDocument()
    expect(within(screen.getByTestId('source-builtin')).getByText('正在建索引…')).toBeInTheDocument()

    // 第一次轮询仍返回 indexing：继续轮询。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000)
    })
    expect(within(screen.getByTestId('source-builtin')).getByText('正在建索引…')).toBeInTheDocument()

    // 第二次轮询读到 idle：状态恢复。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000)
    })
    await waitFor(() =>
      expect(within(screen.getByTestId('source-builtin')).getByText('已建索引')).toBeInTheDocument(),
    )
  })

  it('test search shows hits and the empty state', async () => {
    const network = installApi()
    renderRetrieval(SOURCES_PATH)
    await screen.findByText('内置知识')
    expect(network.searches()).toHaveLength(0)

    const input = screen.getByLabelText('检索词')
    fireEvent.change(input, { target: { value: '固源岩' } })
    fireEvent.click(screen.getByRole('button', { name: '检索' }))

    expect(await screen.findByText('掉落与刷图效率')).toBeInTheDocument()
    expect(screen.getByText('常驻最优图的判断')).toBeInTheDocument()
    expect(screen.getByText('…以 1-7 刷固源岩为推荐做法…')).toBeInTheDocument()
    expect(screen.getByText('共 1 条 · 4 毫秒 · 向量未启用')).toBeInTheDocument()
    expect(screen.getByText('分数 0.0164')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: '打开原文' })
    expect(link).toHaveAttribute('href', 'https://example.com/knowledge/drop-rate')
    expect(link).toHaveAttribute('target', '_blank')
    expect(network.searches()).toHaveLength(1)
    expect(network.searches()[0]!.query.get('q')).toBe('固源岩')

    fireEvent.change(input, { target: { value: '不存在的词' } })
    fireEvent.click(screen.getByRole('button', { name: '检索' }))

    expect(await screen.findByText('还没有匹配的片段')).toBeInTheDocument()
    expect(screen.getByText('换个说法，或等索引建好再试')).toBeInTheDocument()
    expect(screen.getByText('共 0 条 · 2 毫秒 · 向量未启用')).toBeInTheDocument()
    expect(screen.queryByText('掉落与刷图效率')).not.toBeInTheDocument()
    expect(network.searches()).toHaveLength(2)
  })

  it('tab state lives in the url', async () => {
    installApi()
    const { router } = renderRetrieval('/more/settings/knowledge')

    expect(await screen.findByRole('heading', { name: '知识条目' })).toBeInTheDocument()
    expect(router.state.location.search).toBe('')

    fireEvent.click(screen.getByRole('radio', { name: '检索来源' }))
    await waitFor(() => expect(router.state.location.search).toBe('?tab=sources'))
    expect(await screen.findByText('内置知识')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '检索来源' })).toHaveAttribute('aria-checked', 'true')
  })
})
