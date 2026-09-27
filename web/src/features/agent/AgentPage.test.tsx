import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import AgentPage from './AgentPage'

type Json = Record<string, unknown>

const encoder = new TextEncoder()
const MINUTE = 60_000

function makeSession(overrides: Json = {}): Json {
  return {
    session_id: 'session-1',
    title: '自动诊断 · 关卡失败',
    status: 'active',
    incident_key: null,
    model: 'deepseek-v4.1-flash',
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

function event(name: string, data: unknown): string {
  return `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`
}

function sse(frames: string[]): Response {
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(frames.join('')))
      controller.close()
    },
  }), { headers: { 'Content-Type': 'text/event-stream' } })
}

/** 一条保持打开的 SSE 流，测试自己决定何时推送后续事件与结束。 */
function heldStream(frames: string[]) {
  let controller: ReadableStreamDefaultController<Uint8Array> | null = null
  const response = new Response(new ReadableStream<Uint8Array>({
    start(streamController) {
      controller = streamController
      if (frames.length) streamController.enqueue(encoder.encode(frames.join('')))
    },
  }), { headers: { 'Content-Type': 'text/event-stream' } })
  return {
    response,
    push: (more: string[]) => controller?.enqueue(encoder.encode(more.join(''))),
    close: () => controller?.close(),
  }
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
  pending?: Json[]
  confirmations?: Record<string, Json>
  capability?: Json
  createSession?: () => Json
  stream?: (content: string) => Response
  onResolve?: (confirmationId: string, approved: boolean) => void
  /** 返回 Promise 时，消息读取要等它完成才响应。 */
  holdMessages?: () => Promise<void> | undefined
}

/** 按服务端语义模拟 Agent 相关接口：消息按 seq 升序分页，size 取请求值。 */
function createAgentApi(options: ApiOptions = {}) {
  const state = {
    sessions: options.sessions ?? [makeSession()],
    messages: options.messages ?? { 'session-1': toolHistory },
    pending: options.pending ?? [],
    confirmations: options.confirmations ?? {},
  }
  const requests: RecordedRequest[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    const text = request.method === 'GET' || request.method === 'HEAD' ? '' : await request.clone().text()
    const body = text ? JSON.parse(text) as unknown : undefined
    requests.push({ method: request.method, path: url.pathname, query: url.searchParams, body })

    if (url.pathname === '/api/agent/sessions') {
      if (request.method === 'GET') return json({ items: state.sessions, total: state.sessions.length, page: 1, size: 50 })
      if (request.method === 'POST') {
        const created = options.createSession?.() ?? makeSession({ session_id: 'session-new', title: null, message_count: 0, total_tokens: 0 })
        state.sessions = [created, ...state.sessions]
        state.messages[String(created.session_id)] = []
        return json(created, 201)
      }
    }
    const sessionMatch = /^\/api\/agent\/sessions\/([^/]+)(?:\/(messages|atomic-grant))?$/.exec(url.pathname)
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
      if (sub === 'atomic-grant' && request.method === 'DELETE') return new Response(null, { status: 204 })
      if (sub === 'messages' && request.method === 'GET') {
        await options.holdMessages?.()
        const all = state.messages[id!] ?? []
        const page = Number(url.searchParams.get('page') ?? '1')
        const size = Number(url.searchParams.get('size') ?? '20')
        return json({ items: all.slice((page - 1) * size, page * size), total: all.length, page, size })
      }
      if (sub === 'messages' && request.method === 'POST') {
        const content = String((body as Json | undefined)?.content ?? '')
        return options.stream?.(content) ?? sse([event('message_end', { session_id: id, finish_reason: 'stop' })])
      }
    }
    if (url.pathname === '/api/confirmations' && request.method === 'GET') {
      return json({ items: state.pending, total: state.pending.length, page: 1, size: 100 })
    }
    const confirmationMatch = /^\/api\/confirmations\/([^/]+)$/.exec(url.pathname)
    if (confirmationMatch) {
      const id = confirmationMatch[1]!
      if (request.method === 'GET') {
        const found = state.confirmations[id]
        return found ? json(found) : json({ error: { code: 'NOT_FOUND', message: '确认不存在' } }, 404)
      }
      if (request.method === 'POST') {
        const approved = Boolean((body as Json | undefined)?.approved)
        options.onResolve?.(id, approved)
        return json({ confirmation_id: id, status: approved ? 'approved' : 'rejected' })
      }
    }
    if (url.pathname === '/api/agent/capabilities/check' && request.method === 'POST') {
      return json(options.capability ?? { model: 'deepseek-v4.1-flash', ready: true, tool_calling: true, vision: false, status: 'vision_degraded', degraded_reason: '模型暂不支持图片输入' })
    }
    return json({ error: { message: `Unexpected request ${request.method} ${url.pathname}` } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return {
    state,
    requests,
    sentMessages: () => requests.filter((item) => item.method === 'POST' && item.path.endsWith('/messages')),
    messagePages: () => requests
      .filter((item) => item.method === 'GET' && item.path.endsWith('/messages'))
      .map((item) => Number(item.query.get('page'))),
  }
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

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.search}</output>
}

function renderPage(path = '/agent?session_id=session-1') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/agent" element={<><AgentPage /><LocationProbe /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...view, queryClient }
}

const composerName = '发送给 Agent'

describe('Agent conversation page', () => {
  beforeEach(() => {
    document.documentElement.scrollTop = 0
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    const root = document.documentElement as unknown as Record<string, unknown>
    delete root.scrollHeight
    delete root.clientHeight
  })

  it('shows the history with collapsed tool evidence, revokes the session grant and reports the model capability (AG1)', async () => {
    const api = createAgentApi({
      sessions: [makeSession({ atomic_grant: { granted: true, expires_at: new Date(Date.now() + 12 * MINUTE).toISOString(), grant_id: 'grant-1' } })],
    })
    renderPage()

    expect(await screen.findByRole('heading', { level: 1, name: '自动诊断 · 关卡失败' })).toBeInTheDocument()
    expect(await screen.findByText('分析刚才的失败')).toBeInTheDocument()
    expect(screen.getByText('我正在检查关联日志。')).toBeInTheDocument()
    expect(screen.getByText('已授权屏幕操作')).toBeInTheDocument()
    expect(screen.getByText('剩余 12 分钟')).toBeInTheDocument()

    // 工具调用与它的结果合成一条折叠条，默认收起，展开后能看到参数与结果 JSON。
    const toolBar = screen.getByText('get_logs').closest('details')!
    expect(toolBar).not.toHaveAttribute('open')
    expect(within(toolBar).getByText('已完成')).toBeInTheDocument()
    expect(within(toolBar).getByText('审计 #22')).toBeInTheDocument()
    fireEvent.click(within(toolBar).getByText('get_logs'))
    expect(toolBar).toHaveAttribute('open')
    expect(within(toolBar).getByText(/"source": "maa"/)).toBeInTheDocument()
    expect(within(toolBar).getByText(/"items": \[\]/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '撤销屏幕操作授权' }))
    await waitFor(() => expect(api.requests.some((item) => item.method === 'DELETE' && item.path === '/api/agent/sessions/session-1/atomic-grant')).toBe(true))

    fireEvent.click(screen.getByRole('button', { name: 'Agent 操作' }))
    const menu = await screen.findByRole('dialog', { name: 'Agent 操作' })
    fireEvent.click(within(menu).getByRole('button', { name: '检查模型能力' }))
    expect(await screen.findByText('模型暂不支持图片输入')).toBeInTheDocument()
    expect(screen.getByText('视觉能力不可用')).toBeInTheDocument()
    expect(api.requests.some((item) => item.method === 'POST' && item.path === '/api/agent/capabilities/check')).toBe(true)
  })

  it('shows a standalone tool result that has no matching call as a collapsed bar (AG1)', async () => {
    createAgentApi({ messages: { 'session-1': [toolHistory[2]!] } })
    renderPage()

    const orphan = await screen.findByText('工具结果 · get_logs')
    const bar = orphan.closest('details')!
    expect(bar).not.toHaveAttribute('open')
    fireEvent.click(orphan)
    expect(bar).toHaveAttribute('open')
    expect(within(bar).getByText(/"total": 0/)).toBeInTheDocument()
  })

  it('waits for an inline confirmation, then shows the reply exactly once after the live copy is dropped (AG2)', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    let streamFinished = false
    const confirmation = {
      confirmation_id: 'confirm-1',
      action: 'consume_resource',
      risk_level: 'consume',
      reason: '此操作会使用一次理智药。',
      payload: { tool_name: 'submit_pipeline', arguments: { tasks: [{ name: 'Fight', stage: '1-7', medicine: 1 }] }, session_id: 'session-1' },
      status: 'pending',
      expires_at: '2026-09-27T10:05:00Z',
    }
    const api = createAgentApi({
      messages: { 'session-1': [] },
      confirmations: { 'confirm-1': confirmation },
      stream: () => {
        held = heldStream([
          event('message_start', { session_id: 'session-1', capability_status: 'ready' }),
          event('tool_start', { tool_call_id: 'call-1', name: 'submit_pipeline', arguments: confirmation.payload.arguments }),
          event('confirmation_required', { tool_call_id: 'call-1', name: 'submit_pipeline', confirmation_id: 'confirm-1', expires_at: confirmation.expires_at }),
        ])
        return held.response
      },
      onResolve: () => {
        held?.push([
          event('tool_result', { tool_call_id: 'call-1', name: 'submit_pipeline', audit_id: 31, result: { pipeline_id: 'pipeline-9' } }),
          event('text_delta', { text: '已提交流水线，当前状态为排队中。' }),
          event('message_end', { session_id: 'session-1', finish_reason: 'stop' }),
        ])
        held?.close()
        // 服务端在这一轮落库后，历史接口就会返回同样的两句话。
        api.state.messages['session-1'] = [
          { id: 11, session_id: 'session-1', seq: 1, role: 'user', content: '刷一次 1-7', created_at: '2026-09-27T10:01:00Z' },
          { id: 12, session_id: 'session-1', seq: 2, role: 'assistant', content: '已提交流水线，当前状态为排队中。', created_at: '2026-09-27T10:01:01Z' },
        ]
        streamFinished = true
      },
    })
    renderPage()

    const composer = await screen.findByRole('textbox', { name: composerName })
    fireEvent.change(composer, { target: { value: '刷一次 1-7' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByRole('heading', { name: '需要确认此操作' })).toBeInTheDocument()
    expect(await screen.findByText('此操作会使用一次理智药。')).toBeInTheDocument()
    expect(screen.getByText(/等待你确认/)).toBeInTheDocument()
    const liveTurn = screen.getByRole('region', { name: '本轮对话' })
    expect(within(liveTurn).getByText('submit_pipeline')).toBeInTheDocument()
    expect(within(liveTurn).getByText('等待确认')).toBeInTheDocument()
    fireEvent.change(composer, { target: { value: '顺便看看队列' } })
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()
    fireEvent.change(composer, { target: { value: '' } })

    fireEvent.click(await screen.findByRole('button', { name: '批准并继续' }))

    expect(await screen.findByText('已提交流水线，当前状态为排队中。')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '需要确认此操作' })).not.toBeInTheDocument()
    const resolution = api.requests.find((item) => item.method === 'POST' && item.path === '/api/confirmations/confirm-1')
    expect(resolution?.body).toEqual({ approved: true })

    // 回归：流结束后本轮内容已回到历史，实时副本必须消失，否则同一句话出现两遍。
    await waitFor(() => expect(streamFinished).toBe(true))
    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮对话' })).not.toBeInTheDocument())
    expect(screen.getAllByText('刷一次 1-7')).toHaveLength(1)
    expect(screen.getAllByText('已提交流水线，当前状态为排队中。')).toHaveLength(1)
  })

  it('keeps the live copy on screen until the refreshed history has arrived (AG2)', async () => {
    let holdHistory = false
    let releaseHistory: () => void = () => {}
    const api = createAgentApi({
      messages: { 'session-1': [] },
      holdMessages: () => (holdHistory ? new Promise<void>((resolve) => { releaseHistory = resolve }) : undefined),
      stream: (content) => {
        api.state.messages['session-1'] = [
          { id: 51, session_id: 'session-1', seq: 0, role: 'user', content, created_at: '2026-09-27T10:04:00Z' },
          { id: 52, session_id: 'session-1', seq: 1, role: 'assistant', content: '设备在线，队列为空。', created_at: '2026-09-27T10:04:01Z' },
        ]
        holdHistory = true
        return sse([event('text_delta', { text: '设备在线，队列为空。' }), event('message_end', { session_id: 'session-1', finish_reason: 'stop' })])
      },
    })
    renderPage()

    const composer = await screen.findByRole('textbox', { name: composerName })
    fireEvent.change(composer, { target: { value: '检查一下' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    // 流已结束、历史还在刷新：这时丢弃实时副本，界面会闪空。
    await waitFor(() => expect(api.messagePages()).toHaveLength(2))
    expect(screen.getByRole('region', { name: '本轮对话' })).toBeInTheDocument()
    expect(screen.getAllByText('检查一下')).toHaveLength(1)
    expect(screen.getAllByText('设备在线，队列为空。')).toHaveLength(1)

    act(() => releaseHistory())
    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮对话' })).not.toBeInTheDocument())
    expect(screen.getAllByText('检查一下')).toHaveLength(1)
    expect(screen.getAllByText('设备在线，队列为空。')).toHaveLength(1)
  })

  it('keeps the turn and its card when the stream ends while the confirmation is still pending (AG2)', async () => {
    const confirmation = {
      confirmation_id: 'confirm-7',
      action: 'grant_atomic_ops',
      risk_level: 'atomic',
      reason: '需要点击游戏画面',
      payload: { tool_name: 'click', arguments: { x: 10, y: 20 }, session_id: 'session-1', window_seconds: 900 },
      status: 'pending',
      expires_at: '2026-09-27T10:20:00Z',
    }
    const api = createAgentApi({
      messages: { 'session-1': [] },
      confirmations: { 'confirm-7': confirmation },
      stream: () => sse([
        event('tool_start', { tool_call_id: 'call-7', name: 'click', arguments: confirmation.payload.arguments }),
        event('confirmation_required', { tool_call_id: 'call-7', name: 'click', confirmation_id: 'confirm-7', expires_at: confirmation.expires_at }),
      ]),
    })
    const { queryClient } = renderPage()

    const composer = await screen.findByRole('textbox', { name: composerName })
    fireEvent.change(composer, { target: { value: '点一下开始' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByText('会话级屏幕操作授权')).toBeInTheDocument()
    expect(screen.getByText(/15 分钟屏幕操作授权/)).toBeInTheDocument()
    // 流结束、收尾刷新全部完成之后，等待中的这一轮仍要留在原位。
    await waitFor(() => expect(screen.queryByText('请先处理上方的确认')).not.toBeInTheDocument())
    await waitFor(() => expect(api.messagePages()).toHaveLength(2))
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)))
    expect(screen.getByRole('region', { name: '本轮对话' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '需要确认此操作' })).toBeInTheDocument()
    expect(screen.getByText(/等待你确认/)).toBeInTheDocument()
  })

  it('keeps a failed turn visible after the live copy is dropped and links LLM errors to the settings (AG2)', async () => {
    const failure = '当前模型未通过 function calling 能力检查，无法进行 Agent 对话'
    const api = createAgentApi({
      messages: { 'session-1': [] },
      stream: (content) => {
        api.state.messages['session-1'] = [
          { id: 21, session_id: 'session-1', seq: 0, role: 'user', content, created_at: '2026-09-27T10:02:00Z' },
        ]
        return sse([event('error', { code: 'LLM_REQUEST_FAILED', message: failure })])
      },
    })
    renderPage()

    const composer = await screen.findByRole('textbox', { name: composerName })
    fireEvent.change(composer, { target: { value: '你好' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByText(failure)).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮对话' })).not.toBeInTheDocument())
    expect(screen.getByText(failure)).toBeInTheDocument()
    expect(screen.getAllByText('你好')).toHaveLength(1)
    expect(screen.getByRole('link', { name: '前往设置检查 LLM 配置' })).toHaveAttribute('href', '/more/settings/agent')
  })

  it('puts the draft back when the message cannot be sent', async () => {
    const api = createAgentApi({
      messages: { 'session-1': [] },
      stream: () => json({ error: { code: 'LLM_NOT_CONFIGURED', message: '请先配置 LLM base_url、api_key 和 model' } }, 400),
    })
    renderPage()

    const composer = await screen.findByRole('textbox', { name: composerName })
    fireEvent.change(composer, { target: { value: '查看设备' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByText('请先配置 LLM base_url、api_key 和 model')).toBeInTheDocument()
    expect(composer).toHaveValue('查看设备')
    expect(screen.getByRole('link', { name: '前往设置检查 LLM 配置' })).toHaveAttribute('href', '/more/settings/agent')
    expect(api.sentMessages()).toHaveLength(1)
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

  it('sends with Enter on a mouse-and-keyboard device and keeps Shift+Enter for a new line (AG4)', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    const api = createAgentApi({
      messages: { 'session-1': [] },
      stream: () => {
        held = heldStream([event('message_start', { session_id: 'session-1', capability_status: 'ready' })])
        return held.response
      },
    })
    renderPage()

    const composer = await screen.findByRole('textbox', { name: composerName })
    fireEvent.change(composer, { target: { value: '查看队列' } })
    expect(fireEvent.keyDown(composer, { key: 'Enter', shiftKey: true })).toBe(true)
    expect(api.sentMessages()).toHaveLength(0)

    expect(fireEvent.keyDown(composer, { key: 'Enter' })).toBe(false)
    await waitFor(() => expect(api.sentMessages()).toHaveLength(1))
    expect(api.sentMessages()[0]!.body).toEqual({ content: '查看队列' })
    expect(await screen.findByText('Agent 正在处理…')).toBeInTheDocument()

    // 流式进行中：发送按钮禁用，Enter 也不会再发一条。
    fireEvent.change(composer, { target: { value: '再来一条' } })
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()
    fireEvent.keyDown(composer, { key: 'Enter' })
    expect(api.sentMessages()).toHaveLength(1)

    api.state.messages['session-1'] = [
      { id: 31, session_id: 'session-1', seq: 0, role: 'user', content: '查看队列', created_at: '2026-09-27T10:03:00Z' },
      { id: 32, session_id: 'session-1', seq: 1, role: 'assistant', content: '队列为空。', created_at: '2026-09-27T10:03:01Z' },
    ]
    act(() => {
      held?.push([event('text_delta', { text: '队列为空。' }), event('message_end', { session_id: 'session-1', finish_reason: 'stop' })])
      held?.close()
    })
    await waitFor(() => expect(screen.queryByText('Agent 正在处理…')).not.toBeInTheDocument())
    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮对话' })).not.toBeInTheDocument())
    expect(screen.getAllByText('队列为空。')).toHaveLength(1)
  })

  it('treats Enter as a new line on touch devices and sends only from the button (AG4)', async () => {
    stubMedia((query) => query === '(pointer: coarse)')
    const api = createAgentApi({
      messages: { 'session-1': [] },
      stream: () => sse([event('text_delta', { text: '好的。' }), event('message_end', { session_id: 'session-1', finish_reason: 'stop' })]),
    })
    renderPage()

    const composer = await screen.findByRole('textbox', { name: composerName })
    fireEvent.change(composer, { target: { value: '第一行' } })
    // 没有 preventDefault：浏览器照常插入换行。
    expect(fireEvent.keyDown(composer, { key: 'Enter' })).toBe(true)
    expect(api.sentMessages()).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(api.sentMessages()).toHaveLength(1))
    expect(api.sentMessages()[0]!.body).toEqual({ content: '第一行' })
  })

  it('shows an empty state without sessions and creates one into the URL (AG5)', async () => {
    const api = createAgentApi({ sessions: [], messages: {} })
    renderPage('/agent')

    expect(await screen.findByText('还没有会话')).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: composerName })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '新建会话' }))

    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('?session_id=session-new'))
    expect(api.requests.some((item) => item.method === 'POST' && item.path === '/api/agent/sessions')).toBe(true)
    expect(await screen.findByRole('heading', { level: 1, name: '新会话' })).toBeInTheDocument()
    expect(await screen.findByRole('textbox', { name: composerName })).toBeInTheDocument()
  })

  it('creates a session from the page menu (AG5)', async () => {
    createAgentApi()
    renderPage()

    await screen.findByText('分析刚才的失败')
    fireEvent.click(screen.getByRole('button', { name: 'Agent 操作' }))
    const menu = await screen.findByRole('dialog', { name: 'Agent 操作' })
    fireEvent.click(within(menu).getByRole('button', { name: '新建会话' }))

    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('?session_id=session-new'))
    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
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

  it('fills the composer from a quick prompt without sending it (AG5)', async () => {
    const api = createAgentApi({ messages: { 'session-1': [] } })
    renderPage()

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    const prompts = ['查看设备状态和队列', '分析最近一次失败的流水线', '帮我保存一个 Copilot 作业']
    for (const prompt of prompts) expect(screen.getByRole('button', { name: prompt })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '分析最近一次失败的流水线' }))

    expect(screen.getByRole('textbox', { name: composerName })).toHaveValue('分析最近一次失败的流水线')
    expect(api.sentMessages()).toHaveLength(0)
  })
})
