import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import LegacySessionsPage from './LegacySessionsPage'

type Json = Record<string, unknown>

function makeSession(overrides: Json = {}): Json {
  return {
    session_id: 'session-1',
    title: '自动诊断 · 关卡失败',
    status: 'active',
    incident_key: null,
    model: 'deepseek-flash',
    message_count: 3,
    prompt_tokens: 20,
    completion_tokens: 12,
    total_tokens: 32,
    atomic_grant: null,
    created_at: '2026-09-27T10:00:00Z',
    updated_at: '2026-09-27T10:01:00Z',
    last_message_at: '2026-09-27T10:01:00Z',
    ...overrides,
  }
}

const toolHistory: Json[] = [
  { id: 1, session_id: 'session-1', seq: 1, role: 'user', content: '分析刚才的失败', created_at: '2026-09-27T10:00:00Z' },
  { id: 2, session_id: 'session-1', seq: 2, role: 'assistant', content: '我正在检查关联日志。', tool_calls: [{ id: 'call-log', function: { name: 'get_logs', arguments: '{"source":"maa"}' } }], created_at: '2026-09-27T10:00:01Z' },
  { id: 3, session_id: 'session-1', seq: 3, role: 'tool', tool_name: 'get_logs', tool_call_id: 'call-log', audit_id: 22, content: '{"items":[],"total":0}', created_at: '2026-09-27T10:00:02Z' },
]

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

interface RecordedRequest {
  method: string
  path: string
  query: URLSearchParams
  body: unknown
}

interface ApiOptions {
  sessions?: Json[]
  messages?: Record<string, Json[]>
}

/** 按服务端语义模拟旧会话查看器用到的接口：消息按 seq 升序分页，size 取请求值。 */
function createAgentApi(options: ApiOptions = {}) {
  const state = {
    sessions: options.sessions ?? [makeSession()],
    messages: options.messages ?? { 'session-1': toolHistory },
  }
  const requests: RecordedRequest[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    const text = request.method === 'GET' || request.method === 'HEAD' ? '' : await request.clone().text()
    const body = text ? JSON.parse(text) as unknown : undefined
    requests.push({ method: request.method, path: url.pathname, query: url.searchParams, body })

    if (url.pathname === '/api/agent/specs' && request.method === 'GET') {
      return json({ items: [] })
    }
    if (url.pathname === '/api/agent/sessions' && request.method === 'GET') {
      return json({ items: state.sessions, total: state.sessions.length, page: 1, size: 50 })
    }
    const sessionMatch = /^\/api\/agent\/sessions\/([^/]+)(?:\/(messages))?$/.exec(url.pathname)
    if (sessionMatch) {
      const [, id, sub] = sessionMatch
      const found = state.sessions.find((item) => item.session_id === id)
      if (!sub && request.method === 'GET') {
        return found ? json(found) : json({ error: { code: 'AGENT_SESSION_NOT_FOUND', message: 'Agent 会话不存在' } }, 404)
      }
      if (!sub && request.method === 'DELETE') {
        state.sessions = state.sessions.filter((item) => item.session_id !== id)
        return new Response(null, { status: 204 })
      }
      if (sub === 'messages' && request.method === 'GET') {
        const all = state.messages[id!] ?? []
        const page = Number(url.searchParams.get('page') ?? '1')
        const size = Number(url.searchParams.get('size') ?? '20')
        return json({ items: all.slice((page - 1) * size, page * size), total: all.length, page, size })
      }
    }
    // 媒体卡：三种白名单图片地址各返回一个最小 blob，前端据此拿到 objectURL（M23）。
    if (url.pathname.startsWith('/api/images/') || url.pathname.startsWith('/api/screenshots/') || url.pathname === '/api/resources/items/icon') {
      return new Response(new Blob(['jpeg'], { type: 'image/jpeg' }), { headers: { 'Content-Type': 'image/jpeg' } })
    }
    return json({ error: { message: `Unexpected request ${request.method} ${url.pathname}` } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return {
    state,
    requests,
    messagePages: () => requests
      .filter((item) => item.method === 'GET' && item.path.endsWith('/messages'))
      .map((item) => Number(item.query.get('page'))),
  }
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.search}</output>
}

function renderPage(path = '/more/sessions?session_id=session-1') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/more/sessions" element={<><LegacySessionsPage /><LocationProbe /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...view, queryClient }
}

describe('Legacy sessions page', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.scrollTop = 0
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    const root = document.documentElement as unknown as Record<string, unknown>
    delete root.scrollHeight
    delete root.clientHeight
  })

  it('shows the history with collapsed tool evidence (AG1)', async () => {
    createAgentApi()
    renderPage()

    expect(await screen.findByRole('heading', { level: 1, name: '自动诊断 · 关卡失败' })).toBeInTheDocument()
    expect(await screen.findByText('分析刚才的失败')).toBeInTheDocument()
    expect(screen.getByText('我正在检查关联日志。')).toBeInTheDocument()

    // 工具调用与它的结果合成一条折叠条，默认收起，展开后能看到参数与结果 JSON。
    const toolBar = screen.getByText('读取日志').closest('details')!
    expect(toolBar).not.toHaveAttribute('open')
    expect(within(toolBar).getByText('完成')).toBeInTheDocument()
    expect(within(toolBar).getByText('审计 #22')).toBeInTheDocument()
    // 英文函数名收在展开区：折叠条上只有中文动作名。
    expect(within(toolBar).getByText('get_logs')).toBeInTheDocument()
    fireEvent.click(within(toolBar).getByText('读取日志'))
    expect(toolBar).toHaveAttribute('open')
    expect(within(toolBar).getByText(/"source": "maa"/)).toBeInTheDocument()
    expect(within(toolBar).getByText(/"items": \[\]/)).toBeInTheDocument()
  })

  it('shows a standalone tool result that has no matching call as a collapsed bar (AG1)', async () => {
    createAgentApi({ messages: { 'session-1': [toolHistory[2]!] } })
    renderPage()

    const orphan = await screen.findByText('工具结果 · 读取日志')
    const bar = orphan.closest('details')!
    expect(bar).not.toHaveAttribute('open')
    fireEvent.click(orphan)
    expect(bar).toHaveAttribute('open')
    expect(within(bar).getByText(/"total": 0/)).toBeInTheDocument()
  })

  it('names the knowledge and pipeline tools in Chinese in the transcript (M18)', async () => {
    createAgentApi({
      messages: {
        'session-1': [
          { id: 1, session_id: 'session-1', seq: 1, role: 'assistant', content: '先查知识库。', tool_calls: [{ id: 'call-knowledge', function: { name: 'read_knowledge', arguments: '{"entry_id":"glossary"}' } }], created_at: '2026-09-27T10:00:01Z' },
          { id: 2, session_id: 'session-1', seq: 2, role: 'tool', tool_name: 'read_knowledge', tool_call_id: 'call-knowledge', audit_id: 30, content: '{"id":"glossary"}', created_at: '2026-09-27T10:00:02Z' },
          { id: 3, session_id: 'session-1', seq: 3, role: 'assistant', content: '等流水线结束。', tool_calls: [{ id: 'call-wait', function: { name: 'wait_pipeline', arguments: '{"pipeline_id":"p1"}' } }], created_at: '2026-09-27T10:00:03Z' },
          { id: 4, session_id: 'session-1', seq: 4, role: 'tool', tool_name: 'wait_pipeline', tool_call_id: 'call-wait', audit_id: 31, content: '{"finished":true}', created_at: '2026-09-27T10:00:04Z' },
        ],
      },
    })
    renderPage()

    // 中文名与原始工具名同时显示，认得出也搜得到。
    expect(await screen.findByText('查阅知识库')).toBeInTheDocument()
    expect(screen.getByText('等待流水线结束')).toBeInTheDocument()
    expect(screen.getByText('read_knowledge')).toBeInTheDocument()
    expect(screen.getByText('wait_pipeline')).toBeInTheDocument()
  })

  it('reads the last page of a long conversation first and loads earlier pages above it, keeping the scroll position (AG3)', async () => {
    const long: Json[] = Array.from({ length: 600 }, (_, index) => ({
      id: index + 1,
      session_id: 'session-1',
      seq: index,
      role: index % 2 === 0 ? 'user' : 'assistant',
      content: `消息 ${index}`,
      created_at: '2026-09-27T10:00:00Z',
    }))
    const api = createAgentApi({ sessions: [makeSession({ message_count: 600 })], messages: { 'session-1': long } })
    const root = document.documentElement
    const rowHeight = 50
    Object.defineProperty(root, 'scrollHeight', { configurable: true, get: () => document.querySelectorAll('[data-message-id]').length * rowHeight })
    Object.defineProperty(root, 'clientHeight', { configurable: true, get: () => 800 })
    renderPage()

    expect(await screen.findByText('消息 599')).toBeInTheDocument()
    expect(screen.getByText('消息 400')).toBeInTheDocument()
    expect(screen.queryByText('消息 399')).not.toBeInTheDocument()
    expect(api.messagePages()).toEqual([1, 3])
    await waitFor(() => expect(root.scrollTop).toBe(200 * rowHeight))

    // 用户上滑到顶部：不再自动跟随，出现「回到底部」。
    root.scrollTop = 0
    fireEvent.scroll(window)
    expect(await screen.findByRole('button', { name: '回到底部' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '加载更早的消息' }))
    expect(await screen.findByText('消息 399')).toBeInTheDocument()
    expect(api.messagePages()).toEqual([1, 3, 2])
    expect(screen.getByText('消息 200')).toBeInTheDocument()
    expect(screen.queryByText('消息 199')).not.toBeInTheDocument()
    const earlier = screen.getByText('消息 399')
    const later = screen.getByText('消息 400')
    expect(earlier.compareDocumentPosition(later) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // 插入到上方的 200 条不应把视线推走：滚动位置随新增高度下移。
    expect(root.scrollTop).toBe(200 * rowHeight)
    expect(screen.getByRole('button', { name: '加载更早的消息' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '回到底部' }))
    expect(root.scrollTop).toBe(400 * rowHeight)
  })

  it('switches sessions from the session sheet and writes ?session_id= (AG5)', async () => {
    createAgentApi({
      sessions: [
        makeSession(),
        makeSession({ session_id: 'session-2', title: '日常巡检', incident_key: null, message_count: 1, model: 'qwen-max' }),
        makeSession({ session_id: 'session-3', title: null, incident_key: 'pipeline:9', message_count: 2 }),
      ],
      messages: {
        'session-1': toolHistory,
        'session-2': [{ id: 41, session_id: 'session-2', seq: 0, role: 'user', content: '巡检一下设备', created_at: '2026-09-27T09:00:00Z' }],
      },
    })
    renderPage()

    await screen.findByText('分析刚才的失败')
    fireEvent.click(screen.getByRole('button', { name: '会话列表' }))
    const sheet = await screen.findByRole('dialog', { name: '会话' })
    expect(within(sheet).getByRole('button', { name: /^自动诊断 · 关卡失败/ })).toHaveAttribute('aria-current', 'page')
    expect(within(sheet).getByText('自动诊断会话')).toBeInTheDocument()
    expect(within(sheet).getAllByText('自动诊断').length).toBeGreaterThan(0)
    expect(within(sheet).getByText(/1 条消息 · qwen-max/)).toBeInTheDocument()

    fireEvent.click(within(sheet).getByRole('button', { name: /^日常巡检/ }))

    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('?session_id=session-2'))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '会话' })).not.toBeInTheDocument())
    expect(await screen.findByRole('heading', { level: 1, name: '日常巡检' })).toBeInTheDocument()
    expect(await screen.findByText('巡检一下设备')).toBeInTheDocument()
    expect(screen.queryByText('分析刚才的失败')).not.toBeInTheDocument()
  })

  it('deletes a session only after confirmation (AG5)', async () => {
    const api = createAgentApi({
      sessions: [makeSession(), makeSession({ session_id: 'session-2', title: '日常巡检', message_count: 1 })],
      messages: { 'session-1': toolHistory, 'session-2': [] },
    })
    renderPage()

    await screen.findByText('分析刚才的失败')
    fireEvent.click(screen.getByRole('button', { name: '会话列表' }))
    const sheet = await screen.findByRole('dialog', { name: '会话' })
    fireEvent.click(within(sheet).getByRole('button', { name: '会话「日常巡检」的更多操作' }))
    const actions = await screen.findByRole('dialog', { name: '会话操作' })
    fireEvent.click(within(actions).getByRole('button', { name: '删除会话' }))

    const dialog = await screen.findByRole('alertdialog', { name: '删除会话「日常巡检」？' })
    expect(api.requests.some((item) => item.method === 'DELETE' && item.path === '/api/agent/sessions/session-2')).toBe(false)
    fireEvent.click(within(dialog).getByRole('button', { name: '删除会话' }))

    await waitFor(() => expect(api.requests.some((item) => item.method === 'DELETE' && item.path === '/api/agent/sessions/session-2')).toBe(true))
    expect(screen.getByTestId('location')).toHaveTextContent('?session_id=session-1')
  })

  it('shows a get_screenshot archive from history as an attachment inside its tool card (M23)', async () => {
    const sha = 'e'.repeat(64)
    // 后端归档后的形状：没有 media 键，只有截图 id / 内容哈希 / 两个地址。
    const archived = {
      result: {
        screenshot_id: 'shot-legacy',
        sha256: sha,
        full_url: `/api/images/${sha}/full`,
        thumb_url: `/api/images/${sha}/thumb`,
        width: 1280,
        height: 720,
        format: 'jpeg',
      },
    }
    createAgentApi({
      messages: {
        'session-1': [
          { id: 1, session_id: 'session-1', seq: 0, role: 'user', content: '看看现在什么情况', created_at: '2026-09-25T10:00:00Z' },
          { id: 2, session_id: 'session-1', seq: 1, role: 'assistant', content: '画面如下。', tool_calls: [{ id: 'call-legacy', function: { name: 'get_screenshot', arguments: '{}' } }], created_at: '2026-09-25T10:00:01Z' },
          { id: 3, session_id: 'session-1', seq: 2, role: 'tool', tool_name: 'get_screenshot', tool_call_id: 'call-legacy', audit_id: 7, content: JSON.stringify(archived), created_at: '2026-09-25T10:00:02Z' },
        ],
      },
    })
    const { container } = renderPage()

    // 会话里**没有**图片卡：截图只是工具卡内的附件（用户裁决）。
    expect(await screen.findByText('画面如下。')).toBeInTheDocument()
    expect(container.querySelectorAll('[data-testid="media-cards"]')).toHaveLength(0)

    const details = container.querySelector('details')!
    const attachment = details.querySelector('[data-testid="tool-attachment"]')
    expect(attachment).not.toBeNull()
    expect(within(attachment as HTMLElement).getByText('附件')).toBeInTheDocument()

    // 展开工具卡：附件在里面，JSON 与审计信息也在里面（审计与调试用）。
    fireEvent.click(within(details).getByText('截取设备画面'))
    expect(details).toHaveAttribute('open')
    expect(await within(details).findByRole('img', { name: '游戏截图' })).toBeInTheDocument()
    expect(details).toHaveTextContent('shot-legacy')
    expect(screen.getByText('截取设备画面')).toBeInTheDocument()
  })

  it('rebuilds media cards from history (M23)', async () => {
    const sha = 'd'.repeat(64)
    const imageCard = {
      type: 'image',
      screenshot_id: 'shot-media-1',
      sha256: sha,
      url: `/api/images/${sha}/full`,
      thumb_url: `/api/images/${sha}/thumb`,
      alt: '当前的游戏画面',
      caption: '现在长这样',
      width: 1920,
      height: 1080,
    }
    const chartCard = {
      type: 'chart',
      caption: '最近的掉落',
      chart: {
        chart_type: 'bar',
        title: '1-7 掉落',
        categories: ['固源岩'],
        series: [{ name: '数量', values: [12] }],
        stacked: false,
        x_label: '物品',
        y_label: '个数',
        unit: '个',
        source: 'get_drop_stats(stage=1-7)',
      },
    }
    createAgentApi({
      messages: {
        'session-1': [
          { id: 201, session_id: 'session-1', seq: 0, role: 'user', content: '给我看看画面和掉落', created_at: '2026-09-29T12:00:00Z' },
          {
            id: 202,
            session_id: 'session-1',
            seq: 1,
            role: 'assistant',
            content: null,
            tool_calls: [
              { id: 'call-shot', function: { name: 'send_screenshot', arguments: '{}' } },
              { id: 'call-chart', function: { name: 'send_chart', arguments: '{"chart_type":"bar"}' } },
            ],
            created_at: '2026-09-29T12:00:01Z',
          },
          { id: 203, session_id: 'session-1', seq: 2, role: 'tool', tool_name: 'send_screenshot', tool_call_id: 'call-shot', audit_id: 41, content: JSON.stringify({ result: { media: [imageCard] } }), created_at: '2026-09-29T12:00:02Z' },
          { id: 204, session_id: 'session-1', seq: 3, role: 'tool', tool_name: 'send_chart', tool_call_id: 'call-chart', audit_id: 42, content: JSON.stringify({ result: { media: [chartCard] } }), created_at: '2026-09-29T12:00:03Z' },
          { id: 205, session_id: 'session-1', seq: 4, role: 'assistant', content: '这是当前的画面与掉落统计。', created_at: '2026-09-29T12:00:04Z' },
        ],
      },
    })
    renderPage()

    expect(await screen.findByText('这是当前的画面与掉落统计。')).toBeInTheDocument()
    expect(screen.getAllByTestId('media-cards')).toHaveLength(2)
    expect(screen.getByRole('img', { name: '当前的游戏画面' })).toBeInTheDocument()
    expect(screen.getByText('1-7 掉落')).toBeInTheDocument()
  })

  it('explains that old sessions are read-only and links to the console', async () => {
    createAgentApi()
    renderPage()

    expect(await screen.findByText('这些会话只能查看')).toBeInTheDocument()
    expect(screen.getByText('指挥台上线前的会话，以及重新开始前的总控会话。')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '前往指挥台' })).toHaveAttribute('href', '/')
  })

  it('has no composer, new-session or stop entry', async () => {
    createAgentApi()
    renderPage()

    await screen.findByText('分析刚才的失败')
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '新建会话' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '停止本轮' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()
  })

  it('renders archived orchestrator sessions with inbox event cards', async () => {
    createAgentApi({
      sessions: [makeSession({ title: null, incident_key: null, kind: 'orchestrator', archived_at: '2026-09-30T10:00:00Z', message_count: 2 })],
      messages: {
        'session-1': [
          { id: 1, session_id: 'session-1', seq: 0, role: 'user', content: '【工单汇报】工单「日常」进行中：已刷完 1-7', inbox_id: 3, inbox_kind: 'report', inbox_thread_key: 'ticket:t2', created_at: '2026-09-30T09:00:00Z' },
          { id: 2, session_id: 'session-1', seq: 1, role: 'assistant', content: '收到。', created_at: '2026-09-30T09:00:01Z' },
        ],
      },
    })
    renderPage()

    // 归档总控会话里的收件箱消息是事件卡片，而不是用户气泡。
    expect(await screen.findByText('工单汇报')).toBeInTheDocument()
    expect(screen.getByText('工单「日常」进行中：已刷完 1-7')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '打开工单' })).toHaveAttribute('href', '/tickets/t2')

    fireEvent.click(screen.getByRole('button', { name: '会话列表' }))
    const sheet = await screen.findByRole('dialog', { name: '会话' })
    expect(within(sheet).getByText('已归档的总控')).toBeInTheDocument()
  })
})
