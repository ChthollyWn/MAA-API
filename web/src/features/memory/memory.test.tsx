import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { Toaster } from '@/components/ui/toaster'
import MemoryPage from '@/routes/memory'

interface RecordedRequest {
  method: string
  path: string
  body: unknown
}

type Json = Record<string, unknown>

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function pageOf(items: unknown[]): Response {
  return json({ items, total: items.length, page: 1, size: 200 })
}

/** 一条记忆的全部列；测试只覆盖界面用得到的几个字段。 */
function memory(overrides: Json & { id: string }): Json {
  return {
    kind: 'semantic',
    subject: 'preference',
    content: `记忆 ${overrides.id}`,
    status: 'proposed',
    confidence: 0.8,
    source_ticket_id: null,
    source_session_id: 'main',
    source_run_id: null,
    supersedes_id: null,
    expires_at: null,
    last_used_at: null,
    use_count: 0,
    reviewed_by: null,
    reviewed_at: null,
    created_at: '2026-11-05T04:00:00Z',
    updated_at: '2026-11-05T04:00:00Z',
    ...overrides,
  }
}

function defaultMemories(): Json[] {
  return [
    memory({ id: 'pending-1', content: '刷图不要用源石恢复理智' }),
    memory({
      id: 'pending-2',
      content: '新偏好：自动换班',
      supersedes_id: 'old-active',
    }),
    memory({
      id: 'old-active',
      content: '旧偏好：手动换班',
      status: 'active',
      created_at: '2026-10-01T04:00:00Z',
    }),
    memory({
      id: 'active-1',
      kind: 'procedural',
      subject: 'device',
      content: 'ADB 掉线先重连一次',
      status: 'active',
      expires_at: '2027-01-01T00:00:00Z',
      use_count: 2,
    }),
    memory({ id: 'rejected-1', content: '被拒绝的记忆', status: 'rejected' }),
    memory({ id: 'superseded-1', content: '被取代的记忆', status: 'superseded' }),
    memory({ id: 'expired-1', content: '过期的记忆', status: 'expired' }),
  ]
}

interface ApiOptions {
  memories?: Json[]
  listStatus?: number
}

function installApi(options: ApiOptions = {}) {
  let memories = (options.memories ?? defaultMemories()).map((item) => ({ ...item }))
  const requests: RecordedRequest[] = []

  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    const text = request.method === 'GET' || request.method === 'HEAD' ? '' : await request.clone().text()
    let body: unknown
    try {
      body = text ? (JSON.parse(text) as unknown) : undefined
    } catch {
      body = text
    }
    requests.push({ method: request.method, path: url.pathname, body })

    if (url.pathname === '/api/agent/memories' && request.method === 'GET') {
      if (options.listStatus) {
        return json({ error: { code: 'INTERNAL_ERROR', message: '记忆服务出错了' } }, options.listStatus)
      }
      const status = url.searchParams.get('status')
      return pageOf(memories.filter((item) => !status || item.status === status))
    }

    const review = /^\/api\/agent\/memories\/([^/]+)\/review$/.exec(url.pathname)
    if (review && request.method === 'POST') {
      const item = memories.find((entry) => entry.id === review[1])
      if (!item) return json({ error: { code: 'MEMORY_NOT_FOUND', message: '没有这条记忆' } }, 404)
      if (item.status !== 'proposed') {
        return json(
          {
            error: {
              code: 'MEMORY_NOT_PROPOSED',
              message: '这条记忆不在待审核状态',
              details: { memory_id: item.id, status: item.status },
            },
          },
          409,
        )
      }
      const approved = Boolean((body as Json | undefined)?.approved)
      const content = (body as Json | undefined)?.content
      item.status = approved ? 'active' : 'rejected'
      if (approved && typeof content === 'string') item.content = content
      item.reviewed_by = 'user'
      item.reviewed_at = '2026-11-05T05:00:00Z'
      item.updated_at = '2026-11-05T05:00:00Z'
      return json(item)
    }

    const detail = /^\/api\/agent\/memories\/([^/]+)$/.exec(url.pathname)
    if (detail) {
      const id = detail[1]
      const item = memories.find((entry) => entry.id === id)
      if (!item) return json({ error: { code: 'MEMORY_NOT_FOUND', message: '没有这条记忆' } }, 404)
      if (request.method === 'PUT') {
        const input = (body ?? {}) as Json
        if (typeof input.content === 'string') item.content = input.content
        if ('expires_at' in input) item.expires_at = input.expires_at ?? null
        if (typeof input.subject === 'string') item.subject = input.subject
        item.updated_at = '2026-11-05T06:00:00Z'
        return json(item)
      }
      if (request.method === 'DELETE') {
        memories = memories.filter((entry) => entry.id !== id)
        return new Response(null, { status: 204 })
      }
    }

    return json({ error: { message: `Unexpected request ${request.method} ${url.pathname}` } }, 404)
  })

  vi.stubGlobal('fetch', fetchMock)

  return {
    requests,
    mutationRequests: () => requests.filter((item) => item.method !== 'GET'),
    reviewRequests: () => requests.filter((item) => item.method === 'POST'),
    putRequests: () => requests.filter((item) => item.method === 'PUT'),
    deleteRequests: () => requests.filter((item) => item.method === 'DELETE'),
    memory: (id: string) => memories.find((item) => item.id === id),
  }
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  const router = createMemoryRouter([{ path: '/more/memories', element: <MemoryPage /> }], {
    initialEntries: ['/more/memories'],
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster />
    </QueryClientProvider>,
  )
}

/** 等列表读完再取一行；三组顺序由数据决定，不依赖渲染顺序。 */
async function row(id: string): Promise<HTMLElement> {
  await waitFor(() => expect(document.querySelector(`[data-memory-id="${id}"]`)).not.toBeNull())
  return document.querySelector<HTMLElement>(`[data-memory-id="${id}"]`)!
}

describe('M79 记忆页', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('renders the three groups and marks the superseded content', async () => {
    installApi()
    renderPage()

    expect(await screen.findByRole('heading', { level: 1, name: '记忆' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: '待审核' })).toBeInTheDocument()
    for (const heading of ['生效中', '已归档']) {
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
    }
    expect(await row('pending-1')).toHaveAttribute('data-memory-status', 'proposed')
    expect(within(await row('active-1')).getByText('ADB 掉线先重连一次')).toBeInTheDocument()
    expect(within(await row('active-1')).getByText(/用过 2 次/)).toBeInTheDocument()
    expect(within(await row('rejected-1')).getByText('已拒绝')).toBeInTheDocument()
    expect(within(await row('superseded-1')).getByText('已取代')).toBeInTheDocument()
    expect(within(await row('expired-1')).getByText('已过期')).toBeInTheDocument()
    // 有 supersedes_id 时旧内容加删除线（规格 §4.5）：旧条目自己在生效中组里也显示一次。
    const oldTexts = screen.getAllByText('旧偏好：手动换班')
    expect(oldTexts.filter((node) => node.classList.contains('line-through'))).toHaveLength(1)
  })

  it('shows an empty state for each group', async () => {
    installApi({ memories: [] })
    renderPage()

    expect(await screen.findByText('没有待审核的记忆')).toBeInTheDocument()
    expect(screen.getByText('还没有生效的记忆')).toBeInTheDocument()
    expect(screen.getByText('没有归档的记忆')).toBeInTheDocument()
  })

  it('approves and rejects proposals from the pending group', async () => {
    const api = installApi()
    renderPage()

    fireEvent.click(within(await row('pending-1')).getByRole('button', { name: '批准' }))
    expect(await screen.findByText('已批准这条记忆')).toBeInTheDocument()
    await waitFor(() => expect(api.reviewRequests()).toHaveLength(1))
    expect(api.reviewRequests()[0]!.body).toEqual({ approved: true })
    await waitFor(() => expect(document.querySelector('[data-memory-id="pending-1"]')).toHaveAttribute('data-memory-status', 'active'))

    fireEvent.click(within(await row('pending-2')).getByRole('button', { name: '拒绝' }))
    expect(await screen.findByText('已拒绝这条记忆')).toBeInTheDocument()
    await waitFor(() => expect(api.reviewRequests()).toHaveLength(2))
    expect(api.reviewRequests()[1]!.body).toEqual({ approved: false })
  })

  it('lets the user edit the content and approve it', async () => {
    const api = installApi()
    renderPage()

    fireEvent.click(await within(await row('pending-2')).findByRole('button', { name: '改后批准' }))
    const editor = within(await row('pending-2')).getByRole('textbox', { name: '改写后的记忆内容' })
    fireEvent.change(editor, { target: { value: '改后的偏好：自动换班' } })
    fireEvent.click(within(await row('pending-2')).getByRole('button', { name: '确认批准' }))

    expect(await screen.findByText('已批准这条记忆')).toBeInTheDocument()
    await waitFor(() => expect(api.reviewRequests()).toHaveLength(1))
    expect(api.reviewRequests()[0]!.body).toEqual({
      approved: true,
      content: '改后的偏好：自动换班',
    })
  })

  it('edits the content and expiry of an active memory and deletes it', async () => {
    const api = installApi()
    renderPage()

    fireEvent.click(within(await row('active-1')).getByRole('button', { name: '编辑' }))
    fireEvent.change(within(await row('active-1')).getByRole('textbox', { name: '记忆内容' }), {
      target: { value: 'ADB 掉线先断电再重连' },
    })
    fireEvent.change(within(await row('active-1')).getByLabelText('过期时间'), {
      target: { value: '2027-02-01T08:30' },
    })
    fireEvent.click(within(await row('active-1')).getByRole('button', { name: '保存' }))

    expect(await screen.findByText('已保存记忆')).toBeInTheDocument()
    await waitFor(() => expect(api.putRequests()).toHaveLength(1))
    const body = api.putRequests()[0]!.body as Json
    expect(body.content).toBe('ADB 掉线先断电再重连')
    // 本地时间转 UTC ISO；具体时区随运行环境，只断言是带 Z 的 ISO。
    expect(String(body.expires_at)).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/)

    fireEvent.click(within(await row('active-1')).getByRole('button', { name: '删除' }))
    const dialog = await screen.findByRole('alertdialog', { name: '删除这条记忆？' })
    fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))
    expect(await screen.findByText('已删除这条记忆')).toBeInTheDocument()
    await waitFor(() => expect(api.deleteRequests()).toHaveLength(1))
    expect(api.memory('active-1')).toBeUndefined()
  })

  it('keeps the page usable when a stale review conflicts', async () => {
    const api = installApi()
    renderPage()

    const button = within(await row('pending-1')).getByRole('button', { name: '批准' })
    // 列表读完之后服务端把它改成了 active：点批准收到 409，前端要显示可读文案。
    api.memory('pending-1')!.status = 'active'
    fireEvent.click(button)

    expect(await screen.findByText(/不在待审核状态/)).toBeInTheDocument()
  })

  it('shows a retryable error when the list cannot be read', async () => {
    installApi({ listStatus: 500 })
    renderPage()

    expect(await screen.findByText('记忆读取失败')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })
})
