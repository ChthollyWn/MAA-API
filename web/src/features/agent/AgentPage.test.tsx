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

/** 服务端落库的提问 wire（M21）；SSE 的 `question_required` 是它去掉状态字段后的子集。 */
function makeQuestion(overrides: Json = {}): Json {
  return {
    question_id: 'question-1',
    session_id: 'session-1',
    tool_call_id: 'call-ask',
    question: '用哪个渠道刷 1-7？',
    options: [
      { label: '理智', description: '默认渠道，最多 6 次' },
      { label: '理智药', description: null },
    ],
    multiple: false,
    status: 'pending',
    selection: null,
    created_at: '2026-09-29T10:00:00Z',
    expires_at: new Date(Date.now() + 10 * MINUTE).toISOString(),
    answered_at: null,
    answered_by: null,
    ...overrides,
  }
}

/** 与后端 runtime 的 QUESTION_EXPIRED_TEXT 同一句。 */
const QUESTION_EXPIRED_TEXT = '提问超时未得到回答，本轮结束。你可以重新描述需求，我会重新发起提问。'

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
  /** 本会话待答的提问（GET /api/agent/questions）。 */
  questions?: Json[]
  capability?: Json
  createSession?: () => Json
  stream?: (content: string) => Response
  onResolve?: (confirmationId: string, approved: boolean) => void
  /** 提问被提交或跳过时回调，用例在这里推后续事件。 */
  onQuestion?: (questionId: string, action: 'answer' | 'skip', selected: string[]) => void
  /** 中断端点被调用时回调：用例在这里改服务端状态并给出幂等的响应体（M22）。 */
  onInterrupt?: (sessionId: string) => Json
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
    questions: options.questions ?? [],
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
    const sessionMatch = /^\/api\/agent\/sessions\/([^/]+)(?:\/(messages|atomic-grant|interrupt))?$/.exec(url.pathname)
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
      if (sub === 'interrupt' && request.method === 'POST') {
        // 后端幂等：没有活动轮次时也是 200，只是 interrupted=false（M22 规格 §3.4）。
        return json(options.onInterrupt?.(id!) ?? { session_id: id, interrupted: true, turn_running: false })
      }
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
    if (url.pathname === '/api/agent/questions' && request.method === 'GET') {
      const sessionId = url.searchParams.get('session_id')
      const items = state.questions.filter((item) => !sessionId || item.session_id === sessionId)
      return json({ items, total: items.length, page: 1, size: 100 })
    }
    const questionMatch = /^\/api\/agent\/questions\/([^/]+?)(\/skip)?$/.exec(url.pathname)
    if (questionMatch) {
      const [, id, skip] = questionMatch
      if (request.method === 'POST') {
        const selected = skip ? [] : ((body as Json | undefined)?.selected as string[] | undefined) ?? []
        // 服务端把这条从 pending 里挪走，之后轮到 tool_result 与后续文本。
        state.questions = state.questions.filter((item) => item.question_id !== id)
        options.onQuestion?.(id!, skip ? 'skip' : 'answer', selected)
        return json({ question_id: id, session_id: 'session-1', status: skip ? 'skipped' : 'answered', selection: selected })
      }
    }
    if (url.pathname === '/api/agent/capabilities/check' && request.method === 'POST') {
      return json(options.capability ?? { model: 'deepseek-flash', ready: true, tool_calling: true, vision: false, status: 'vision_degraded', degraded_reason: '模型暂不支持图片输入' })
    }
    return json({ error: { message: `Unexpected request ${request.method} ${url.pathname}` } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return {
    state,
    requests,
    sentMessages: () => requests.filter((item) => item.method === 'POST' && item.path.endsWith('/messages')),
    questionListCalls: () => requests.filter((item) => item.method === 'GET' && item.path === '/api/agent/questions').length,
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
    // 本轮还在跑（等确认也算）：发送按钮原地是「停止」，草稿留着也发不出去。
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止本轮' })).toBeInTheDocument()
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

  it('asks through a card inside the live turn and keeps the same turn going after the answer (M21)', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    const question = makeQuestion()
    const api = createAgentApi({
      messages: { 'session-1': [] },
      stream: () => {
        // 服务端在发卡之前已经落库：本会话待答列表里查得到同一条。
        api.state.questions = [question]
        held = heldStream([
          event('message_start', { session_id: 'session-1', capability_status: 'ready' }),
          event('tool_start', { tool_call_id: 'call-ask', name: 'ask_user', arguments: { question: question.question, options: question.options } }),
          event('question_required', {
            tool_call_id: 'call-ask',
            question_id: question.question_id,
            question: question.question,
            options: question.options,
            multiple: false,
            expires_at: question.expires_at,
          }),
        ])
        return held.response
      },
      onQuestion: (id, _action, selected) => {
        // 用户的选择作为工具结果回到模型，这一轮接着往下走。
        api.state.messages['session-1'] = [
          { id: 101, session_id: 'session-1', seq: 0, role: 'user', content: '刷一次 1-7', created_at: '2026-09-29T10:00:00Z' },
          { id: 102, session_id: 'session-1', seq: 1, role: 'assistant', content: '按理智刷。', created_at: '2026-09-29T10:00:01Z' },
        ]
        held?.push([
          event('tool_result', { tool_call_id: 'call-ask', name: 'ask_user', audit_id: 51, result: { status: 'answered', selected, question_id: id } }),
          event('text_delta', { text: '按理智刷。' }),
          event('message_end', { session_id: 'session-1', finish_reason: 'stop' }),
        ])
        held?.close()
      },
    })
    renderPage()

    const composer = await screen.findByRole('textbox', { name: composerName })
    fireEvent.change(composer, { target: { value: '刷一次 1-7' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    // 卡片挂在本轮工具条下方，输入栏提示先回答；本轮还在跑，发送按钮是「停止」。
    expect(await screen.findByRole('heading', { name: '需要你选择' })).toBeInTheDocument()
    expect(screen.getByText('用哪个渠道刷 1-7？')).toBeInTheDocument()
    expect(screen.getByText('请先回答上方的问题')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止本轮' })).toBeInTheDocument()
    const liveTurn = screen.getByRole('region', { name: '本轮对话' })
    expect(within(liveTurn).getByText('向你提问')).toBeInTheDocument()
    expect(within(liveTurn).getByText('ask_user')).toBeInTheDocument()

    // 待答列表也拿到同一条，但本轮已经展示过：只出现一张卡。
    await waitFor(() => expect(api.questionListCalls()).toBeGreaterThan(1))
    expect(screen.getAllByRole('heading', { name: '需要你选择' })).toHaveLength(1)

    const submit = screen.getByRole('button', { name: '提交' })
    expect(submit).toBeDisabled()
    fireEvent.click(screen.getByRole('radio', { name: /理智药/ }))
    expect(submit).toBeEnabled()
    fireEvent.click(submit)

    const resolution = await waitFor(() => {
      const posted = api.requests.find((item) => item.method === 'POST' && item.path === '/api/agent/questions/question-1')
      expect(posted).toBeDefined()
      return posted!
    })
    expect(resolution.body).toEqual({ selected: ['理智药'] })
    expect(api.state.questions).toHaveLength(0)

    // 提交成功后清卡，同一轮继续追加后续文本。
    await waitFor(() => expect(screen.queryByRole('heading', { name: '需要你选择' })).not.toBeInTheDocument())
    expect(await screen.findByText('按理智刷。')).toBeInTheDocument()

    // 回合结束、历史回来后实时副本消失，同一句话不重复出现。
    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮对话' })).not.toBeInTheDocument())
    expect(screen.getAllByText('按理智刷。')).toHaveLength(1)
    expect(screen.queryByText('请先回答上方的问题')).not.toBeInTheDocument()
  })

  it('ends the turn with the server closing text when the question times out (M21)', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    // 已过期的提问：本地倒计时先把它判成超时态。
    const question = makeQuestion({ expires_at: new Date(Date.now() - 1000).toISOString() })
    const api = createAgentApi({
      messages: { 'session-1': [] },
      stream: () => {
        api.state.questions = [question]
        held = heldStream([
          event('tool_start', { tool_call_id: 'call-ask', name: 'ask_user', arguments: {} }),
          event('question_required', {
            tool_call_id: 'call-ask',
            question_id: question.question_id,
            question: question.question,
            options: question.options,
            multiple: false,
            expires_at: question.expires_at,
          }),
        ])
        return held.response
      },
    })
    renderPage()

    const composer = await screen.findByRole('textbox', { name: composerName })
    fireEvent.change(composer, { target: { value: '刷一次 1-7' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByRole('heading', { name: '需要你选择' })).toBeInTheDocument()
    expect(await screen.findByText('提问已超时，本轮已结束。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '提交' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '跳过' })).toBeDisabled()

    // 后端把这条置为 expired：工具结果 + 收尾文案 + 本轮结束。
    api.state.questions = []
    api.state.messages['session-1'] = [
      { id: 111, session_id: 'session-1', seq: 0, role: 'user', content: '刷一次 1-7', created_at: '2026-09-29T10:00:00Z' },
      { id: 112, session_id: 'session-1', seq: 1, role: 'assistant', content: QUESTION_EXPIRED_TEXT, created_at: '2026-09-29T10:10:01Z' },
    ]
    act(() => {
      held?.push([
        event('tool_result', { tool_call_id: 'call-ask', name: 'ask_user', result: { status: 'expired', question_id: question.question_id } }),
        event('text_delta', { text: QUESTION_EXPIRED_TEXT }),
        event('message_end', { session_id: 'session-1', finish_reason: 'question_unanswered' }),
      ])
      held?.close()
    })

    // 超时收尾：清卡，留下后端那句收尾文案，实时副本让位给历史。
    await waitFor(() => expect(screen.queryByRole('heading', { name: '需要你选择' })).not.toBeInTheDocument())
    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮对话' })).not.toBeInTheDocument())
    expect(screen.getAllByText(QUESTION_EXPIRED_TEXT)).toHaveLength(1)
    expect(screen.queryByText('请先回答上方的问题')).not.toBeInTheDocument()
    expect(api.requests.some((item) => item.method === 'POST' && item.path.includes('/skip'))).toBe(false)
  })

  it('restores the unanswered question of this session after a refresh and answers it from there (M21)', async () => {
    const question = makeQuestion()
    const api = createAgentApi({ messages: { 'session-1': [] }, questions: [question] })
    renderPage()

    // 刷新后本轮已经没了，卡由本会话待答查询接管；输入栏不说「请先回答」。
    expect(await screen.findByRole('heading', { name: '需要你选择' })).toBeInTheDocument()
    expect(screen.getByText('用哪个渠道刷 1-7？')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '本轮对话' })).not.toBeInTheDocument()
    expect(screen.queryByText('请先回答上方的问题')).not.toBeInTheDocument()

    fireEvent.click(await screen.findByRole('radio', { name: /理智药/ }))
    fireEvent.click(screen.getByRole('button', { name: '提交' }))

    await waitFor(() => expect(api.requests.some((item) => item.method === 'POST' && item.path === '/api/agent/questions/question-1')).toBe(true))
    await waitFor(() => expect(screen.queryByRole('heading', { name: '需要你选择' })).not.toBeInTheDocument())
  })

  it('keeps the confirmation wording when a confirmation and a question are pending at once (M21)', async () => {
    const confirmation = {
      confirmation_id: 'confirm-9',
      action: 'consume_resource',
      risk_level: 'consume',
      reason: '此操作会使用一次理智药。',
      payload: { tool_name: 'submit_pipeline', arguments: {}, session_id: 'session-1' },
      status: 'pending',
      expires_at: '2026-09-29T10:20:00Z',
    }
    const question = makeQuestion()
    let held: ReturnType<typeof heldStream> | null = null
    createAgentApi({
      messages: { 'session-1': [] },
      confirmations: { 'confirm-9': confirmation },
      questions: [question],
      stream: () => {
        // 流要保持打开：状态行只在「本轮还在跑」时显示。
        held = heldStream([
          event('tool_start', { tool_call_id: 'call-9', name: 'submit_pipeline', arguments: {} }),
          event('confirmation_required', { tool_call_id: 'call-9', name: 'submit_pipeline', confirmation_id: 'confirm-9', expires_at: confirmation.expires_at }),
          event('question_required', {
            tool_call_id: 'call-ask',
            question_id: question.question_id,
            question: question.question,
            options: question.options,
            multiple: false,
            expires_at: question.expires_at,
          }),
        ])
        return held.response
      },
    })
    renderPage()

    const composer = await screen.findByRole('textbox', { name: composerName })
    fireEvent.change(composer, { target: { value: '刷一次 1-7' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByRole('heading', { name: '需要确认此操作' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '需要你选择' })).toBeInTheDocument()
    // 确认会决定提问所在那批工具能不能跑，状态行只说更靠前的那个。
    expect(screen.getByText('请先处理上方的确认')).toBeInTheDocument()
    expect(screen.queryByText('请先回答上方的问题')).not.toBeInTheDocument()

    await act(async () => { held?.close() })
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

    // 流式进行中：发送按钮原地变「停止」，Enter 也不会再发一条。
    fireEvent.change(composer, { target: { value: '再来一条' } })
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止本轮' })).toBeInTheDocument()
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
    // 回合结束后发送按钮回来（草稿还在，所以是可用的）。
    expect(screen.getByRole('button', { name: '发送' })).toBeEnabled()
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

  it('lists a brand-new session after the first message without a manual refresh', async () => {
    // 服务端新建会话时 message_count 为 0、last_message_at 为 null，发出第一条消息后
    // 列表必须以刷新后的数据出现，不能要求用户手动刷新。
    const api = createAgentApi({
      sessions: [makeSession()],
      messages: { 'session-1': toolHistory },
      stream: (content) => {
        // 服务端在流结束前把两条消息落库；列表刷新后 message_count 才会变成 2。
        api.state.messages['session-new'] = [
          { id: 91, session_id: 'session-new', seq: 0, role: 'user', content, created_at: '2026-09-28T02:00:00Z' },
          { id: 92, session_id: 'session-new', seq: 1, role: 'assistant', content: '收到。', created_at: '2026-09-28T02:00:01Z' },
        ]
        api.state.sessions = api.state.sessions.map((item) => (
          item.session_id === 'session-new'
            ? { ...item, message_count: 2, last_message_at: '2026-09-28T02:00:01Z' }
            : item
        ))
        return sse([
          event('message_start', { session_id: 'session-new', capability_status: 'ready' }),
          event('text_delta', { text: '收到。' }),
          event('message_end', { session_id: 'session-new', finish_reason: 'stop' }),
        ])
      },
    })
    renderPage()

    await screen.findByText('分析刚才的失败')
    fireEvent.click(screen.getByRole('button', { name: 'Agent 操作' }))
    const menu = await screen.findByRole('dialog', { name: 'Agent 操作' })
    fireEvent.click(within(menu).getByRole('button', { name: '新建会话' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('?session_id=session-new'))

    fireEvent.change(screen.getByRole('textbox', { name: composerName }), { target: { value: '你好' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(api.sentMessages()).toHaveLength(1))
    expect(await screen.findByText('收到。')).toBeInTheDocument()

    const listCalls = () => api.requests.filter(
      (item) => item.method === 'GET' && item.path === '/api/agent/sessions',
    ).length
    await waitFor(() => expect(listCalls()).toBeGreaterThan(1), { timeout: 3000 })

    fireEvent.click(screen.getByRole('button', { name: '会话列表' }))
    const sheet = await screen.findByRole('dialog', { name: '会话' })
    const row = within(sheet).getByRole('button', { name: /^新会话/ })
    expect(row).toBeInTheDocument()
    expect(within(row).getByText(/2 条消息/)).toBeInTheDocument()
  })

  it('fills the agent-generated session title before the stream ends (no manual refresh)', async () => {
    // 新会话在标题生成完成前用「新会话」占位；服务端在收流前把总结好的标题随
    // session_title 推来，标题栏与会话列表要立刻回填。
    //
    // 这里刻意让流停在 session_title 之后不关（也还没到回合结束的 invalidate），
    // 并且列表接口的数据里**始终没有标题**：标题只可能来自事件载荷本身——真去
    // 依赖刷新的话这条用例就会红。
    let held: ReturnType<typeof heldStream> | null = null
    const api = createAgentApi({
      sessions: [makeSession()],
      messages: { 'session-1': toolHistory },
      stream: () => {
        held = heldStream([
          event('message_start', { session_id: 'session-new', capability_status: 'ready' }),
          event('text_delta', { text: '已创建。' }),
          event('message_end', { session_id: 'session-new', finish_reason: 'stop' }),
          event('session_title', { session_id: 'session-new', title: '创建剿灭定时任务' }),
        ])
        return held.response
      },
    })
    renderPage()

    await screen.findByText('分析刚才的失败')
    fireEvent.click(screen.getByRole('button', { name: 'Agent 操作' }))
    const menu = await screen.findByRole('dialog', { name: 'Agent 操作' })
    fireEvent.click(within(menu).getByRole('button', { name: '新建会话' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('?session_id=session-new'))

    // 还没有标题：标题栏先占位。
    expect(await screen.findByRole('heading', { level: 1, name: '新会话' })).toBeInTheDocument()

    fireEvent.change(screen.getByRole('textbox', { name: composerName }), { target: { value: '帮我加一个剿灭任务' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(api.sentMessages()).toHaveLength(1))

    expect(await screen.findByRole('heading', { level: 1, name: '创建剿灭定时任务' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 1, name: '新会话' })).not.toBeInTheDocument()
    // 正在生成的提示已经消失：回合本身在 message_end 就结束了。
    expect(screen.queryByText('正在生成回复')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '会话列表' }))
    const sheet = await screen.findByRole('dialog', { name: '会话' })
    expect(within(sheet).getByRole('button', { name: /^创建剿灭定时任务/ })).toBeInTheDocument()
    expect(within(sheet).queryByRole('button', { name: /^新会话/ })).not.toBeInTheDocument()

    await act(async () => { held?.close() })
  })

  it('refreshes the header token count after a turn without a manual reload', async () => {
    const api = createAgentApi({
      sessions: [makeSession({ message_count: 0, total_tokens: 0 })],
      messages: { 'session-1': [] },
      stream: (content) => {
        // 服务端在流结束前把这一轮的用量累加进会话。
        api.state.sessions = api.state.sessions.map((item) => (
          item.session_id === 'session-1'
            ? { ...item, message_count: 2, total_tokens: 510_195, last_message_at: '2026-09-28T03:00:00Z' }
            : item
        ))
        api.state.messages['session-1'] = [
          { id: 61, session_id: 'session-1', seq: 0, role: 'user', content, created_at: '2026-09-28T03:00:00Z' },
          { id: 62, session_id: 'session-1', seq: 1, role: 'assistant', content: '收到。', created_at: '2026-09-28T03:00:01Z' },
        ]
        return sse([
          event('message_start', { session_id: 'session-1', capability_status: 'ready' }),
          event('text_delta', { text: '收到。' }),
          event('message_end', { session_id: 'session-1', finish_reason: 'stop' }),
        ])
      },
    })
    renderPage()

    // 初始是 0，且按用户要求换成百万单位。
    expect(await screen.findByText(/deepseek-flash · 0 Token/)).toBeInTheDocument()

    fireEvent.change(screen.getByRole('textbox', { name: composerName }), { target: { value: '你好' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    // 不需要刷新：回合结束后标题应显示 0.5M，原始数字不再出现。
    expect(await screen.findByText(/deepseek-flash · 0\.5M Token/, {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.queryByText(/510195 Token/)).not.toBeInTheDocument()
  })

  it('still refreshes the list and the token count when the turn fails', async () => {
    // 失败的回合同样会落库（服务端把错误写成 assistant 消息），所以刷新不能只在
    // 成功路径上做 —— 否则标题的累计 token 与会话列表会停在旧数据，用户必须手动刷新。
    const api = createAgentApi({
      sessions: [makeSession({ message_count: 0, total_tokens: 0 })],
      messages: { 'session-1': [] },
      stream: (content) => {
        api.state.sessions = api.state.sessions.map((item) => (
          item.session_id === 'session-1'
            ? { ...item, message_count: 2, total_tokens: 128_000, last_message_at: '2026-09-28T04:00:00Z' }
            : item
        ))
        api.state.messages['session-1'] = [
          { id: 71, session_id: 'session-1', seq: 0, role: 'user', content, created_at: '2026-09-28T04:00:00Z' },
          { id: 72, session_id: 'session-1', seq: 1, role: 'assistant', content: 'LLM 上游请求失败', finish_reason: 'error', created_at: '2026-09-28T04:00:01Z' },
        ]
        return sse([
          event('message_start', { session_id: 'session-1', capability_status: 'ready' }),
          event('error', { code: 'LLM_REQUEST_FAILED', message: 'LLM 上游请求失败' }),
        ])
      },
    })
    const listCalls = () => api.requests.filter(
      (item) => item.method === 'GET' && item.path === '/api/agent/sessions',
    ).length
    renderPage()

    expect(await screen.findByText(/deepseek-flash · 0 Token/)).toBeInTheDocument()
    const before = listCalls()

    fireEvent.change(screen.getByRole('textbox', { name: composerName }), { target: { value: '你好' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    // 错误横幅与落库的 assistant 消息都会出现这句话，断言存在即可。
    await waitFor(() => expect(screen.getAllByText('LLM 上游请求失败').length).toBeGreaterThan(0))
    await waitFor(() => expect(listCalls()).toBeGreaterThan(before), { timeout: 3000 })
    const detailCalls = () => api.requests.filter(
      (item) => item.method === 'GET' && item.path === '/api/agent/sessions/session-1',
    ).length
    await waitFor(() => expect(detailCalls()).toBeGreaterThan(1), { timeout: 3000 })
    expect(await screen.findByText(/deepseek-flash · 0\.1M Token/)).toBeInTheDocument()
  })

  it('turns the send button into a stop button during a turn and wraps the turn up after stopping (M22)', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    let holdHistory = false
    const releases: Array<() => void> = []
    const releaseHistory = () => {
      holdHistory = false
      for (const release of releases.splice(0)) release()
    }
    const api = createAgentApi({
      messages: { 'session-1': [] },
      // 停止后的历史刷新被挂住：这中间的界面状态才看得见。
      holdMessages: () => (holdHistory ? new Promise<void>((resolve) => { releases.push(resolve) }) : undefined),
      stream: () => {
        held = heldStream([
          event('message_start', { session_id: 'session-1', capability_status: 'ready' }),
          event('text_delta', { text: '正在查询…' }),
          // 这一轮正挂在等流水线：中断后它不该继续显示「执行中」。
          event('tool_start', { tool_call_id: 'call-wait', name: 'wait_pipeline', arguments: { pipeline_id: 'p1' } }),
        ])
        return held.response
      },
      onInterrupt: (sessionId) => {
        // 服务端的中断收尾：已经流出去的部分文本、tool 桩消息（结构硬要求）与中断标记
        // 一起落库，SSE 随后也收尾。本地已经 abort，收不到那些事件 —— 这正是要测的：
        // 界面不能因此卡住。
        api.state.messages['session-1'] = [
          { id: 201, session_id: sessionId, seq: 0, role: 'user', content: '查一下队列', created_at: '2026-09-29T11:00:00Z' },
          { id: 202, session_id: sessionId, seq: 1, role: 'assistant', content: '正在查询…', tool_calls: [{ id: 'call-wait', function: { name: 'wait_pipeline', arguments: '{"pipeline_id":"p1"}' } }], created_at: '2026-09-29T11:00:01Z' },
          { id: 203, session_id: sessionId, seq: 2, role: 'tool', tool_name: 'wait_pipeline', tool_call_id: 'call-wait', content: '{"error":{"code":"AGENT_TURN_INTERRUPTED","message":"本轮已被中断，该调用未完成"}}', created_at: '2026-09-29T11:00:02Z' },
          { id: 204, session_id: sessionId, seq: 3, role: 'assistant', content: '本轮已被你中断。', finish_reason: 'interrupted', created_at: '2026-09-29T11:00:03Z' },
        ]
        holdHistory = true
        return { session_id: sessionId, interrupted: true, turn_running: false }
      },
    })
    renderPage()

    const composer = await screen.findByRole('textbox', { name: composerName })
    fireEvent.change(composer, { target: { value: '查一下队列' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    // 执行期间发送按钮原地变「停止」，不新增按钮位置。
    expect(await screen.findByText('Agent 正在处理…')).toBeInTheDocument()
    expect(screen.getByText('执行中')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '停止本轮' }))

    await waitFor(() => expect(api.requests.some(
      (item) => item.method === 'POST' && item.path === '/api/agent/sessions/session-1/interrupt',
    )).toBe(true))

    // 历史还没回来也一样：abort 之后 running 必须显式归位，否则「Agent 正在处理…」与
    // 「执行中」会永久挂着（既有 bug）。停止期间按钮禁用，实时副本留到历史到达才让位。
    await waitFor(() => expect(screen.queryByText('Agent 正在处理…')).not.toBeInTheDocument())
    expect(screen.getByText('正在停止本轮…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止本轮' })).toBeDisabled()
    expect(screen.queryByText('执行中')).not.toBeInTheDocument()
    expect(screen.getByText('未返回结果')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '本轮对话' })).toBeInTheDocument()

    act(() => releaseHistory())

    // 本轮收尾：实时副本让位给历史、部分文本只出现一次、不报错。
    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮对话' })).not.toBeInTheDocument())
    expect(await screen.findByText('本轮已被你中断。')).toBeInTheDocument()
    expect(screen.getAllByText('正在查询…')).toHaveLength(1)
    expect(screen.getByText('失败')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    // 输入栏立刻可用：abort 不再把 running 永久留在 true。
    fireEvent.change(composer, { target: { value: '再看一眼' } })
    expect(screen.getByRole('button', { name: '发送' })).toBeEnabled()
  })

  it('restores the stop entry from turn_running when the page is reopened (M22)', async () => {
    const api = createAgentApi({
      sessions: [makeSession({ turn_running: true })],
      messages: { 'session-1': [] },
      onInterrupt: (sessionId) => {
        api.state.sessions = api.state.sessions.map((item) => (
          item.session_id === sessionId ? { ...item, turn_running: false } : item
        ))
        return { session_id: sessionId, interrupted: true, turn_running: false }
      },
    })
    renderPage()

    // 本地没有那条流，但后端说这一轮还在跑：状态行与停止入口都要回来，也发不出消息。
    expect(await screen.findByText('本轮仍在后台执行')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止本轮' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '停止本轮' }))

    await waitFor(() => expect(api.requests.some(
      (item) => item.method === 'POST' && item.path === '/api/agent/sessions/session-1/interrupt',
    )).toBe(true))
    // 停止后重新取回 turn_running=false，输入栏回到可发送。
    await waitFor(() => expect(screen.queryByText('本轮仍在后台执行')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: '发送' })).toBeInTheDocument()
  })

  it('points at the stop entry when the session is busy (409 AGENT_SESSION_BUSY, M22)', async () => {
    const api = createAgentApi({
      messages: { 'session-1': [] },
      stream: () => {
        // 本地以为空闲，服务端说还有一轮在跑；会话详情刷新后就会带回 turn_running。
        api.state.sessions = api.state.sessions.map((item) => (
          item.session_id === 'session-1' ? { ...item, turn_running: true } : item
        ))
        return json({ error: { code: 'AGENT_SESSION_BUSY', message: '该会话上一轮还在执行' } }, 409)
      },
    })
    renderPage()

    const composer = await screen.findByRole('textbox', { name: composerName })
    fireEvent.change(composer, { target: { value: '查一下队列' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByText('该会话仍有一轮在跑，可先停止它')).toBeInTheDocument()
    // 草稿还给用户，停完就能直接发。
    expect(composer).toHaveValue('查一下队列')
    expect(await screen.findByText('本轮仍在后台执行')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止本轮' })).toBeInTheDocument()
    expect(api.sentMessages()).toHaveLength(1)
  })

  it('ends the turn without an error when another client interrupts it (SSE interrupted, M22)', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    const api = createAgentApi({
      messages: { 'session-1': [] },
      stream: () => {
        held = heldStream([
          event('message_start', { session_id: 'session-1', capability_status: 'ready' }),
          event('text_delta', { text: '正在查询…' }),
        ])
        return held.response
      },
    })
    renderPage()

    const composer = await screen.findByRole('textbox', { name: composerName })
    fireEvent.change(composer, { target: { value: '查一下队列' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(await screen.findByText('Agent 正在处理…')).toBeInTheDocument()

    // 别的标签页/设备点了停止：服务端推 interrupted，随后是 message_end 与关流。
    act(() => {
      held?.push([
        event('interrupted', { session_id: 'session-1', finish_reason: 'interrupted' }),
        event('message_end', { session_id: 'session-1', finish_reason: 'interrupted' }),
      ])
    })

    // 不当错误，也不再挂着「正在处理」。
    await waitFor(() => expect(screen.queryByText('Agent 正在处理…')).not.toBeInTheDocument())
    expect(screen.getByText('本轮已停止')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(api.sentMessages()).toHaveLength(1)

    api.state.messages['session-1'] = [
      { id: 301, session_id: 'session-1', seq: 0, role: 'user', content: '查一下队列', created_at: '2026-09-29T11:10:00Z' },
      { id: 302, session_id: 'session-1', seq: 1, role: 'assistant', content: '正在查询…\n\n本轮已被你中断。', finish_reason: 'interrupted', created_at: '2026-09-29T11:10:01Z' },
    ]
    await act(async () => { held?.close() })

    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮对话' })).not.toBeInTheDocument())
    expect(await screen.findByText('本轮已被你中断。')).toBeInTheDocument()
    expect(screen.getAllByText('正在查询…')).toHaveLength(1)
  })
})

