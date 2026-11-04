import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { RunsPage } from '@/features/runs/RunsPage'
import { RunDetailPage } from '@/features/runs/RunDetailPage'
import { useAuth } from '@/stores/auth'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function toRequest(input: RequestInfo | URL, init?: RequestInit): Request {
  return input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
}

const specs = {
  items: [
    { name: 'orchestrator', title: '总控', description: '总控代理', kind: 'orchestrator', model_role: 'orchestrator', spec_version: 'spec-orchestrator', version: 1, tools: [], delegates: ['worker'] },
    { name: 'worker', title: '执行代理', description: '执行代理', kind: 'worker', model_role: 'worker', spec_version: 'spec-worker', version: 1, tools: [], delegates: [] },
  ],
}

function runSummary(overrides: Record<string, unknown> = {}) {
  return {
    run_id: 'run-1',
    root_run_id: 'run-1',
    parent_run_id: null,
    session_id: 'session-1',
    agent_name: 'orchestrator',
    kind: 'orchestrator_turn',
    status: 'succeeded',
    finish_reason: 'stop',
    model: 'deepseek-chat',
    model_profile: 'default',
    spec_version: 'abc123def4567890',
    steps: 3,
    prompt_tokens: 1000,
    cached_tokens: 400,
    completion_tokens: 200,
    reasoning_tokens: 0,
    cost_micros: 12345,
    currency: 'CNY',
    started_at: '2026-11-02T08:15:30Z',
    finished_at: '2026-11-02T08:15:34Z',
    heartbeat_at: null,
    error_code: null,
    ticket_id: null,
    trigger_seq: null,
    ...overrides,
  }
}

function runDetail(overrides: Record<string, unknown> = {}, children: unknown[] = []) {
  return {
    ...runSummary(overrides),
    error_message: null,
    meta: null,
    children,
  }
}

function span(overrides: Record<string, unknown>) {
  return {
    span_id: 'span-1',
    parent_span_id: null,
    run_id: 'run-1',
    name: 'agent.run',
    kind: 'other',
    status: 'ok',
    attributes: {},
    started_at: '2026-11-02T08:15:30Z',
    ended_at: '2026-11-02T08:15:34Z',
    duration_ms: 4000,
    ...overrides,
  }
}

const rootSpans = {
  trace_id: 'run-1',
  truncated: false,
  items: [
    span({ span_id: 'span-root', parent_span_id: null, run_id: 'run-1', name: 'agent.run', kind: 'other', attributes: { 'maa.agent.name': 'orchestrator', 'maa.run.status': 'succeeded' }, duration_ms: 4000 }),
    span({ span_id: 'span-llm', parent_span_id: 'span-root', name: 'llm.chat', kind: 'llm', attributes: { 'gen_ai.request.model': 'deepseek-chat' }, started_at: '2026-11-02T08:15:30.500Z', ended_at: '2026-11-02T08:15:31Z', duration_ms: 500 }),
    span({ span_id: 'span-tool', parent_span_id: 'span-root', name: 'tool.execute', kind: 'tool', attributes: { 'gen_ai.tool.name': 'submit_pipeline', 'maa.audit.id': 91 }, started_at: '2026-11-02T08:15:31Z', ended_at: '2026-11-02T08:15:33Z', duration_ms: 2000 }),
    span({ span_id: 'span-child', parent_span_id: 'span-tool', run_id: 'run-2', name: 'agent.run', kind: 'other', attributes: { 'maa.agent.name': 'worker' }, started_at: '2026-11-02T08:15:31.500Z', ended_at: '2026-11-02T08:15:32.500Z', duration_ms: 1000 }),
  ],
}

function renderRuns(fetchMock: typeof fetch, initialEntry = '/more/runs') {
  vi.stubGlobal('fetch', fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const router = createMemoryRouter([
    { path: '/more/runs', element: <RunsPage /> },
    { path: '/more/runs/:runId', element: <RunDetailPage /> },
    { path: '/agent', element: <div>Agent destination</div> },
  ], { initialEntries: [initialEntry] })
  render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>)
  return router
}

function baseMock(options: {
  runs?: unknown[]
  total?: number
  detail?: unknown
  spans?: unknown
  onDetail?: () => unknown
} = {}) {
  const urls: URL[] = []
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = new URL(toRequest(input, init).url)
    urls.push(url)
    if (url.pathname === '/api/agent/specs') return json(specs)
    if (url.pathname === '/api/agent/runs') {
      const items = options.runs ?? [runSummary(), runSummary({ run_id: 'run-9', agent_name: 'ghost', status: 'failed', finish_reason: 'error', started_at: '2026-11-02T09:00:00Z', finished_at: '2026-11-02T09:00:05Z' })]
      return json({ items, total: options.total ?? items.length, page: Number(url.searchParams.get('page') ?? 1), size: 20 })
    }
    if (url.pathname === '/api/agent/runs/run-1/spans') return json(options.spans ?? rootSpans)
    if (url.pathname === '/api/agent/runs/run-2/spans') return json({ trace_id: 'run-1', truncated: false, items: rootSpans.items })
    if (url.pathname === '/api/agent/runs/run-1') {
      return json(options.onDetail ? options.onDetail() : options.detail ?? runDetail({}, [
        runSummary({ run_id: 'run-2', parent_run_id: 'run-1', agent_name: 'worker', kind: 'worker_turn', status: 'running', finish_reason: null, started_at: '2026-11-02T08:15:31Z', finished_at: null }),
      ]))
    }
    if (url.pathname === '/api/agent/runs/run-2') {
      return json(runDetail({ run_id: 'run-2', parent_run_id: 'run-1', agent_name: 'worker', kind: 'worker_turn', status: 'running', finish_reason: null, started_at: '2026-11-02T08:15:31Z', finished_at: null }))
    }
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  return { fetchMock, urls }
}

describe('Runs page', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuth.getState().clearToken()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('lists root runs with agent titles', async () => {
    const { fetchMock, urls } = baseMock()
    renderRuns(fetchMock)

    expect(await screen.findByRole('heading', { name: '运行追踪', level: 1 })).toBeInTheDocument()
    const list = await screen.findByRole('list', { name: '运行记录' })
    expect(within(list).getByText('总控')).toBeInTheDocument()
    expect(within(list).getByText('未知代理')).toBeInTheDocument()
    expect(within(list).getAllByText(/总控一轮/).length).toBeGreaterThan(0)
    expect(within(list).getByText('已完成')).toBeInTheDocument()
    expect(within(list).getByText('失败')).toBeInTheDocument()
    expect(within(list).getAllByText(/3 步/).length).toBeGreaterThan(0)
    expect(within(list).getAllByText(/1,200 token/).length).toBeGreaterThan(0)
    expect(within(list).getAllByRole('link')[0]).toHaveAttribute('href', '/more/runs/run-1')

    const query = urls.find((url) => url.pathname === '/api/agent/runs')
    expect(query?.searchParams.get('root_only')).toBe('true')
    expect(query?.searchParams.get('size')).toBe('20')
    const text = list.textContent ?? ''
    expect(text).not.toContain('orchestrator_turn')
    expect(text).not.toContain('succeeded')
    expect(text).not.toContain('ghost')
  })

  it('passes ticket_id filter', async () => {
    const { fetchMock, urls } = baseMock({ runs: [runSummary({ ticket_id: 'ticket-9' })] })
    renderRuns(fetchMock, '/more/runs?ticket_id=ticket-9')

    expect(await screen.findByText('总控')).toBeInTheDocument()
    await waitFor(() => expect(urls.some((url) => url.pathname === '/api/agent/runs' && url.searchParams.get('ticket_id') === 'ticket-9')).toBe(true))
    const query = urls.find((url) => url.pathname === '/api/agent/runs' && url.searchParams.get('ticket_id') === 'ticket-9')
    expect(query?.searchParams.get('root_only')).toBe('true')
    expect(query?.searchParams.get('page')).toBe('1')
  })

  it('shows empty state', async () => {
    const { fetchMock } = baseMock({ runs: [], total: 0 })
    renderRuns(fetchMock)

    expect(await screen.findByText('还没有运行记录')).toBeInTheDocument()
    expect(screen.getByText('去 Agent 页发一条消息')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '去 Agent 页' })).toHaveAttribute('href', '/agent')
  })

  it('detail renders tree and waterfall', async () => {
    const { fetchMock, urls } = baseMock()
    renderRuns(fetchMock, '/more/runs/run-1')

    const summary = await screen.findByRole('region', { name: '运行概要' })
    expect(within(summary).getByText('总控')).toBeInTheDocument()
    expect(within(summary).getAllByText('已完成').length).toBeGreaterThan(0)
    expect(within(summary).getByText('deepseek-chat')).toBeInTheDocument()
    expect(within(summary).getByText('abc123de')).toBeInTheDocument()

    const tree = screen.getByRole('list', { name: '运行树' })
    expect(within(tree).getByRole('link', { name: /执行代理/ })).toHaveAttribute('href', '/more/runs/run-2')

    const waterfall = await screen.findByRole('list', { name: '运行追踪' })
    expect(within(waterfall).getByText('模型 · deepseek-chat')).toBeInTheDocument()
    expect(within(waterfall).getByText('工具 · submit_pipeline')).toBeInTheDocument()
    expect(within(waterfall).getByText('运行 · 执行代理')).toBeInTheDocument()
    expect(urls.some((url) => url.pathname === '/api/agent/runs/run-1/spans')).toBe(true)

    const text = document.body.textContent ?? ''
    expect(text).not.toContain('orchestrator_turn')
    expect(text).not.toContain('worker_turn')
    expect(text).not.toContain('succeeded')
  })

  it('polls while running and stops after finish', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let detailCalls = 0
    const { fetchMock } = baseMock({
      onDetail: () => {
        detailCalls += 1
        const finished = detailCalls > 1
        return runDetail({
          status: finished ? 'succeeded' : 'running',
          finish_reason: finished ? 'stop' : null,
          finished_at: finished ? '2026-11-02T08:15:34Z' : null,
        })
      },
    })
    renderRuns(fetchMock, '/more/runs/run-1')

    const summary = await screen.findByRole('region', { name: '运行概要' })
    expect(within(summary).getAllByText('运行中').length).toBeGreaterThan(0)
    expect(detailCalls).toBe(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(3_000) })
    await waitFor(() => expect(detailCalls).toBeGreaterThan(1))
    expect((await within(summary).findAllByText('已完成')).length).toBeGreaterThan(0)

    const callsAfterFinish = detailCalls
    await act(async () => { await vi.advanceTimersByTimeAsync(6_000) })
    expect(detailCalls).toBe(callsAfterFinish)
  })

  it('opens span sheet', async () => {
    const { fetchMock } = baseMock()
    const router = renderRuns(fetchMock, '/more/runs/run-1')

    const waterfall = await screen.findByRole('list', { name: '运行追踪' })
    fireEvent.click(within(waterfall).getByRole('button', { name: /工具 · submit_pipeline/ }))
    let sheet = await screen.findByRole('dialog', { name: '工具 · submit_pipeline' })
    expect(within(sheet).getByText('gen_ai.tool.name')).toBeInTheDocument()
    expect(within(sheet).getByText('submit_pipeline')).toBeInTheDocument()
    expect(within(sheet).getByText('正常')).toBeInTheDocument()
    fireEvent.click(within(sheet).getByRole('button', { name: '关闭' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    fireEvent.click(within(waterfall).getByRole('button', { name: /运行 · 执行代理/ }))
    sheet = await screen.findByRole('dialog', { name: '运行 · 执行代理' })
    const openRun = within(sheet).getByRole('link', { name: '打开这次运行' })
    expect(openRun).toHaveAttribute('href', '/more/runs/run-2')
    fireEvent.click(openRun)
    await waitFor(() => expect(router.state.location.pathname).toBe('/more/runs/run-2'))
  })
})
