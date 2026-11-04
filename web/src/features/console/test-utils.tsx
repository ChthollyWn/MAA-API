import { vi } from 'vitest'
import { render } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'
import type { ReactNode } from 'react'
import { Toaster } from '@/components/ui/toaster'

export type Json = Record<string, unknown>

const encoder = new TextEncoder()

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

export function event(name: string, data: unknown): string {
  return `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`
}

export function sse(frames: string[]): Response {
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(frames.join('')))
      controller.close()
    },
  }), { headers: { 'Content-Type': 'text/event-stream' } })
}

/** 一条保持打开的 SSE 流，测试自己决定何时推送后续事件与结束。 */
export function heldStream(frames: string[]) {
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
    /** 读流途中以连接层错误结束：手机切网、锁屏冻结后恢复时浏览器就是这样失败的。 */
    fail: (error: unknown = new TypeError('network error')) => controller?.error(error),
  }
}

/** jsdom 没有 matchMedia；按查询决定是否命中桌面/触屏布局。 */
export function stubMedia(matches: (query: string) => boolean) {
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

export interface RecordedRequest {
  method: string
  path: string
  query: URLSearchParams
  body: unknown
}

export interface ConsoleApiState {
  orchestrator: Json
  messages: Json[]
  tickets: Json[]
  ticketDetails: Record<string, Json>
  ticketMessages: Record<string, Json[]>
  reports: Record<string, Json[]>
  runs: Record<string, Json[]>
  runDetails: Record<string, Json>
  questions: Json[]
  confirmations: Json[]
  confirmationDetails: Record<string, Json>
  specs: Json[]
  specDetails: Record<string, Json>
  lease: Json
  modelRoles: Json
  /** 默认计划；`plans` 里按会话覆盖。 */
  plan: Json | null
  plans: Record<string, Json | null>
  artifacts: Json[]
  artifactDetails: Record<string, Json>
}

export interface OverrideContext {
  method: string
  path: string
  query: URLSearchParams
  body: unknown
  state: ConsoleApiState
  requests: RecordedRequest[]
}

export type ConsoleApiOverride = (context: OverrideContext) => Response | undefined

export interface ConsoleApiOptions {
  orchestrator?: Json
  messages?: Json[]
  /** 全部工单；不传时由 `activeTickets` 与 `attentionTickets` 拼起来。 */
  tickets?: Json[]
  activeTickets?: Json[]
  attentionTickets?: Json[]
  ticketDetails?: Record<string, Json>
  ticketMessages?: Record<string, Json[]>
  reports?: Record<string, Json[]>
  runs?: Record<string, Json[]>
  runDetails?: Record<string, Json>
  questions?: Json[]
  confirmations?: Json[]
  confirmationDetails?: Record<string, Json>
  specs?: Json[]
  specDetails?: Record<string, Json>
  lease?: Json
  modelRoles?: Json
  /** 计划（M75）：默认 `null`；多会话用 `plans` 按会话覆盖。 */
  plan?: Json | null
  plans?: Record<string, Json | null>
  /** 产物列表（M75）；`artifactDetails` 提供抽屉正文，缺省时回落到列表项。 */
  artifacts?: Json[]
  artifactDetails?: Record<string, Json>
  /** 返回 Promise 时，计划批准/退回要等它完成才响应（锁「请求中禁用」）。 */
  holdPlanApproval?: () => Promise<void> | undefined
  knowledge?: Json[]
  pipelines?: Json[]
  operators?: Json[]
  /** 消息发送端点；默认回一条空流。第二条消息（排队）可以在这里立即断开。 */
  stream?: (body: Json, request: RecordedRequest) => Response
  onResolve?: (confirmationId: string, approved: boolean) => void
  onQuestion?: (questionId: string, action: 'answer' | 'skip', selected: string[]) => void
  onInterrupt?: () => Json | Response | undefined
  onReset?: () => Json | Response | undefined
  /** 返回 Promise 时，总控消息读取要等它完成才响应。 */
  holdMessages?: () => Promise<void> | undefined
  /** 任意端点先经过这里；返回 Response 即接管。 */
  overrides?: ConsoleApiOverride[]
}

const DEFAULT_SPECS: Json[] = [
  { name: 'orchestrator', title: '总控', kind: 'orchestrator', description: '总控代理', version: 1 },
  { name: 'general', title: '通用 Worker', kind: 'worker', description: '通用工单代理', version: 1 },
]

function makeState(options: ConsoleApiOptions): ConsoleApiState {
  return {
    orchestrator: options.orchestrator ?? {
      session_id: 'main',
      status: 'active',
      turn_running: false,
      pending_inbox: 0,
      archived_count: 0,
    },
    messages: options.messages ?? [],
    tickets: options.tickets ?? [...(options.activeTickets ?? []), ...(options.attentionTickets ?? [])],
    ticketDetails: options.ticketDetails ?? {},
    ticketMessages: options.ticketMessages ?? {},
    reports: options.reports ?? {},
    runs: options.runs ?? {},
    runDetails: options.runDetails ?? {},
    questions: options.questions ?? [],
    confirmations: options.confirmations ?? [],
    confirmationDetails: options.confirmationDetails ?? {},
    specs: options.specs ?? DEFAULT_SPECS,
    specDetails: options.specDetails ?? {},
    lease: options.lease ?? { held: false, lease: null, waiters: 0 },
    modelRoles: options.modelRoles ?? { items: [] },
    plan: options.plan ?? null,
    plans: { ...(options.plans ?? {}) },
    artifacts: options.artifacts ?? [],
    artifactDetails: { ...(options.artifactDetails ?? {}) },
  }
}

/** 按会话取计划；`plans` 没写这个会话时回落到默认 `plan`。 */
function planOf(state: ConsoleApiState, sessionId: string): Json | null {
  return sessionId in state.plans ? state.plans[sessionId] ?? null : state.plan
}

function setPlanOf(state: ConsoleApiState, sessionId: string, plan: Json | null): void {
  if (sessionId in state.plans) state.plans[sessionId] = plan
  else state.plan = plan
}

function pageOf(items: unknown[], page: number, size: number): Response {
  return json({
    items: items.slice((page - 1) * size, page * size),
    total: items.length,
    page,
    size,
  })
}

function findById(items: Json[], id: string): Json | undefined {
  return items.find((item) => item.id === id || item.ticket_id === id || item.confirmation_id === id || item.question_id === id)
}

/**
 * 按 §4.6.2 的端点表模拟指挥台所需的全部接口。
 *
 * 记录每个请求的 `method`/`path`/`query`/`body`；`state` 可被用例直接改写（服务端
 * 在流结束前落库的场景）。任何端点都能通过 `overrides` 接管。
 */
export function createConsoleApi(options: ConsoleApiOptions = {}) {
  const state = makeState(options)
  const requests: RecordedRequest[] = []

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    const text = request.method === 'GET' || request.method === 'HEAD' ? '' : await request.clone().text()
    let body: unknown
    try {
      body = text ? JSON.parse(text) as unknown : undefined
    } catch {
      body = text
    }
    const recorded: RecordedRequest = { method: request.method, path: url.pathname, query: url.searchParams, body }
    requests.push(recorded)

    for (const override of options.overrides ?? []) {
      const response = override({ ...recorded, state, requests })
      if (response) return response
    }

    const page = Number(url.searchParams.get('page') ?? '1')
    const size = Number(url.searchParams.get('size') ?? '20')

    if (url.pathname === '/api/agent/orchestrator' && request.method === 'GET') {
      return json(state.orchestrator)
    }

    if (url.pathname === '/api/agent/orchestrator/messages' && request.method === 'GET') {
      await options.holdMessages?.()
      const messageSize = Number(url.searchParams.get('size') ?? '200')
      return pageOf(state.messages, page, messageSize)
    }

    if (url.pathname === '/api/agent/orchestrator/messages' && request.method === 'POST') {
      return options.stream?.(body as Json, recorded)
        ?? sse([event('run_end', { run_id: 'run-1', finish_reason: 'stop' })])
    }

    if (url.pathname === '/api/agent/orchestrator/interrupt' && request.method === 'POST') {
      const custom = options.onInterrupt?.()
      if (custom instanceof Response) return custom
      state.orchestrator = { ...state.orchestrator, turn_running: false }
      return json(custom ?? { session_id: state.orchestrator.session_id, interrupted: true, turn_running: false })
    }

    if (url.pathname === '/api/agent/orchestrator/reset' && request.method === 'POST') {
      const custom = options.onReset?.()
      if (custom instanceof Response) return custom
      return json(custom ?? state.orchestrator)
    }

    if (url.pathname === '/api/agent/tickets' && request.method === 'GET') {
      const statuses = url.searchParams.getAll('status')
      let items = state.tickets
      if (options.activeTickets && statuses.length === 1 && statuses[0] === 'open') {
        items = options.activeTickets
      } else if (options.attentionTickets && statuses.length > 0
        && statuses.every((status) => status === 'waiting' || status === 'blocked')) {
        items = options.attentionTickets
      } else if (statuses.length > 0) {
        items = state.tickets.filter((ticket) => statuses.includes(String(ticket.status)))
      }
      return pageOf(items, page, size)
    }

    const ticketMatch = /^\/api\/agent\/tickets\/([^/]+)(?:\/(messages|reports|interrupt|cancel))?$/.exec(url.pathname)
    if (ticketMatch) {
      const [, ticketId, sub] = ticketMatch
      const ticket = findById(state.tickets, ticketId!) ?? state.ticketDetails[ticketId!]?.ticket as Json | undefined
      if (sub === 'messages' && request.method === 'GET') {
        return pageOf(state.ticketMessages[ticketId!] ?? [], page, size)
      }
      if (sub === 'messages' && request.method === 'POST') {
        return options.stream?.(body as Json, recorded)
          ?? sse([event('run_end', { run_id: 'run-1', finish_reason: 'stop' })])
      }
      if (sub === 'reports' && request.method === 'GET') {
        return pageOf(state.reports[ticketId!] ?? [], page, size)
      }
      if (sub === 'interrupt' && request.method === 'POST') {
        return json({ ticket_id: ticketId, interrupted: true, turn_running: false })
      }
      if (sub === 'cancel' && request.method === 'POST') {
        return json({ ticket_id: ticketId, status: 'cancelled' })
      }
      if (!sub && request.method === 'GET') {
        const detailed = state.ticketDetails[ticketId!]
        if (detailed) return json(detailed)
        if (!ticket) {
          return json({ error: { code: 'TICKET_NOT_FOUND', message: '工单不存在' } }, 404)
        }
        return json({
          ticket,
          mandate: null,
          recent_reports: state.reports[ticketId!] ?? [],
          worker_session_id: null,
          turn_running: false,
        })
      }
    }

    if (url.pathname === '/api/agent/runs' && request.method === 'GET') {
      const ticketId = url.searchParams.get('ticket_id') ?? ''
      return pageOf(state.runs[ticketId] ?? [], page, Number(url.searchParams.get('size') ?? '50'))
    }

    const runMatch = /^\/api\/agent\/runs\/([^/]+)$/.exec(url.pathname)
    if (runMatch && request.method === 'GET') {
      const run = state.runDetails[runMatch[1]!]
      if (!run) return json({ error: { code: 'RUN_NOT_FOUND', message: '运行不存在' } }, 404)
      return json(run)
    }

    if (url.pathname === '/api/agent/specs' && request.method === 'GET') {
      return json({ items: state.specs })
    }

    const specMatch = /^\/api\/agent\/specs\/([^/]+)$/.exec(url.pathname)
    if (specMatch && request.method === 'GET') {
      const spec = state.specDetails[specMatch[1]!] ?? state.specs.find((item) => item.name === specMatch[1])
      if (!spec) return json({ error: { code: 'SPEC_NOT_FOUND', message: '代理定义不存在' } }, 404)
      return json(spec)
    }

    if (url.pathname === '/api/agent/device-lease' && request.method === 'GET') {
      return json(state.lease)
    }
    if (url.pathname === '/api/agent/device-lease' && request.method === 'DELETE') {
      state.lease = { held: false, lease: null, waiters: 0 }
      return new Response(null, { status: 204 })
    }

    if (url.pathname === '/api/agent/model-roles' && request.method === 'GET') {
      return json(state.modelRoles)
    }

    if (url.pathname === '/api/agent/questions' && request.method === 'GET') {
      const status = url.searchParams.get('status')
      const sessionId = url.searchParams.get('session_id')
      const items = state.questions
        .filter((question) => !status || question.status === status)
        .filter((question) => !sessionId || question.session_id === sessionId)
      return pageOf(items, page, size)
    }

    const questionMatch = /^\/api\/agent\/questions\/([^/]+?)(\/skip)?$/.exec(url.pathname)
    if (questionMatch) {
      const [, questionId, skip] = questionMatch
      if (request.method === 'GET' && !skip) {
        const question = findById(state.questions, questionId!)
        return question
          ? json(question)
          : json({ error: { code: 'QUESTION_NOT_FOUND', message: '提问不存在' } }, 404)
      }
      if (request.method === 'POST') {
        const selected = skip ? [] : ((body as Json | undefined)?.selected as string[] | undefined) ?? []
        state.questions = state.questions.filter((question) => question.question_id !== questionId)
        options.onQuestion?.(questionId!, skip ? 'skip' : 'answer', selected)
        return json({
          question_id: questionId,
          session_id: state.orchestrator.session_id,
          status: skip ? 'skipped' : 'answered',
          selection: selected,
        })
      }
    }

    if (url.pathname === '/api/confirmations' && request.method === 'GET') {
      const status = url.searchParams.get('status')
      const items = state.confirmations.filter((item) => !status || item.status === status)
      return pageOf(items, page, size)
    }

    const confirmationMatch = /^\/api\/confirmations\/([^/]+)$/.exec(url.pathname)
    if (confirmationMatch) {
      const id = confirmationMatch[1]!
      if (request.method === 'GET') {
        const detail = state.confirmationDetails[id] ?? findById(state.confirmations, id)
        return detail
          ? json(detail)
          : json({ error: { code: 'NOT_FOUND', message: '确认不存在' } }, 404)
      }
      if (request.method === 'POST') {
        const approved = Boolean((body as Json | undefined)?.approved)
        state.confirmations = state.confirmations.filter((item) => item.confirmation_id !== id)
        const detail = state.confirmationDetails[id]
        if (detail) detail.status = approved ? 'approved' : 'rejected'
        options.onResolve?.(id, approved)
        return json({ confirmation_id: id, status: approved ? 'approved' : 'rejected' })
      }
    }

    const planMatch = /^\/api\/agent\/sessions\/([^/]+)\/plan(\/approval)?$/.exec(url.pathname)
    if (planMatch) {
      const sessionId = decodeURIComponent(planMatch[1]!)
      if (!planMatch[2] && request.method === 'GET') {
        return json(planOf(state, sessionId))
      }
      if (planMatch[2] && request.method === 'POST') {
        await options.holdPlanApproval?.()
        const input = (body ?? {}) as Json
        const current = planOf(state, sessionId)
        if (!current) {
          return json({ error: { code: 'PLAN_NOT_FOUND', message: '计划不存在' } }, 404)
        }
        const approved = Boolean(input.approved)
        const next = {
          ...current,
          status: approved ? 'active' : 'draft',
          approved_by: approved ? 'console' : null,
          approved_at: approved ? '2026-10-01T10:00:00Z' : null,
        }
        setPlanOf(state, sessionId, next)
        return json(next)
      }
    }

    if (url.pathname === '/api/agent/artifacts' && request.method === 'GET') {
      const ticketId = url.searchParams.get('ticket_id')
      const sessionId = url.searchParams.get('session_id')
      const kind = url.searchParams.get('kind')
      let items = state.artifacts
      if (ticketId) items = items.filter((item) => item.ticket_id === ticketId)
      if (sessionId) items = items.filter((item) => item.session_id === sessionId)
      if (kind) items = items.filter((item) => item.kind === kind)
      return pageOf(items, page, size)
    }

    const artifactMatch = /^\/api\/agent\/artifacts\/([^/]+?)(\/content|\/pin)?$/.exec(url.pathname)
    if (artifactMatch) {
      const artifactId = decodeURIComponent(artifactMatch[1]!)
      const sub = artifactMatch[2]
      const detail = state.artifactDetails[artifactId]
        ?? state.artifacts.find((item) => item.id === artifactId)
      if (!detail) {
        return json({ error: { code: 'ARTIFACT_NOT_FOUND', message: '产物不存在' } }, 404)
      }
      if (!sub && request.method === 'GET') return json(detail)
      if (sub === '/content' && request.method === 'GET') {
        return new Response(String(detail.content ?? ''), {
          status: 200,
          headers: { 'Content-Type': String(detail.mime ?? 'text/plain') },
        })
      }
      if (sub === '/pin' && request.method === 'PUT') {
        const pinned = Boolean((body as Json | undefined)?.pinned)
        const next = { ...detail, pinned }
        state.artifacts = state.artifacts.map((item) => (item.id === artifactId ? next : item))
        state.artifactDetails[artifactId] = next
        return json(next)
      }
    }

    if (url.pathname === '/api/agent/knowledge' && request.method === 'GET') {
      return json({ items: options.knowledge ?? [] })
    }

    if (url.pathname === '/api/pipelines' && request.method === 'GET') {
      const items = options.pipelines ?? []
      return json({ items, total: items.length, page: 1, size: 10 })
    }

    if (url.pathname === '/api/account/operators' && request.method === 'GET') {
      const items = options.operators ?? []
      return json({ items, total: items.length })
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
    sentMessages: () => requests.filter((item) => item.method === 'POST' && item.path === '/api/agent/orchestrator/messages'),
    messagePages: () => requests
      .filter((item) => item.method === 'GET' && item.path === '/api/agent/orchestrator/messages')
      .map((item) => Number(item.query.get('page'))),
    resetCalls: () => requests.filter((item) => item.method === 'POST' && item.path === '/api/agent/orchestrator/reset'),
    interruptCalls: () => requests.filter((item) => item.method === 'POST' && item.path === '/api/agent/orchestrator/interrupt'),
    confirmationPosts: () => requests.filter((item) => item.method === 'POST' && item.path.startsWith('/api/confirmations/')),
    questionPosts: () => requests.filter((item) => item.method === 'POST' && item.path.startsWith('/api/agent/questions/')),
    ticketListQueries: () => requests.filter((item) => item.method === 'GET' && item.path === '/api/agent/tickets'),
  }
}

/** 一条 v3 wire 消息；四个新字段默认 null，可直接被 v2 形状复用。 */
export function makeV3Message(overrides: Json = {}): Json {
  const id = typeof overrides.id === 'number' ? overrides.id : 1
  return {
    id,
    session_id: 'main',
    seq: id,
    role: 'user',
    content: '',
    tool_calls: null,
    tool_name: null,
    tool_call_id: null,
    audit_id: null,
    finish_reason: null,
    created_at: '2026-09-27T10:00:00Z',
    run_id: null,
    inbox_id: null,
    inbox_kind: null,
    inbox_thread_key: null,
    ...overrides,
  }
}

export function makeTicket(overrides: Json = {}): Json {
  return {
    id: 't1',
    title: '日常',
    kind: 'general',
    status: 'open',
    origin: 'user',
    summary: null,
    last_report_status: null,
    human_action: null,
    created_at: '2026-09-27T10:00:00Z',
    updated_at: '2026-09-27T10:01:00Z',
    ended_at: null,
    ...overrides,
  }
}

export function makeTicketDetail(overrides: Json = {}): Json {
  return {
    ticket: makeTicket(),
    mandate: null,
    recent_reports: [],
    worker_session_id: null,
    turn_running: false,
    ...overrides,
  }
}

export function makeRun(overrides: Json = {}): Json {
  return {
    run_id: 'run-1',
    agent_name: 'orchestrator',
    agent: 'orchestrator',
    kind: 'orchestrator_turn',
    status: 'succeeded',
    finish_reason: 'stop',
    steps: 3,
    prompt_tokens: 20,
    cached_tokens: 0,
    completion_tokens: 12,
    cost_micros: 0,
    currency: null,
    started_at: '2026-09-27T10:00:00Z',
    finished_at: '2026-09-27T10:00:10Z',
    children: [],
    ...overrides,
  }
}

export function makeConfirmation(overrides: Json = {}): Json {
  return {
    confirmation_id: 'confirm-1',
    action: 'consume_resource',
    risk_level: 'consume',
    reason: '此操作会使用一次理智药。',
    payload: {
      tool_name: 'submit_pipeline',
      arguments: { tasks: [{ name: 'Fight', stage: '1-7', medicine: 1 }] },
      session_id: 'main',
    },
    status: 'pending',
    caller: { kind: 'internal', name: 'orchestrator', session_id: 'main' },
    origin: null,
    expires_at: new Date(Date.now() + 10 * 60_000).toISOString(),
    ...overrides,
  }
}

export function makeQuestion(overrides: Json = {}): Json {
  return {
    question_id: 'question-1',
    session_id: 'main',
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
    expires_at: new Date(Date.now() + 10 * 60_000).toISOString(),
    answered_at: null,
    answered_by: null,
    ...overrides,
  }
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

export interface ConsoleTestRoute {
  path: string
  element: ReactNode
}

/** 在 MemoryRouter 里渲染页面；未知路径落到位置探针，便于断言跳转。 */
export function renderAt(path: string, routes: ConsoleTestRoute[]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(
    [...routes, { path: '*', element: <LocationProbe /> }],
    { initialEntries: [path] },
  )
  const view = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster />
    </QueryClientProvider>,
  )
  return { ...view, router, queryClient }
}
