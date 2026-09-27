import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import AgentPage from './AgentPage'

const encoder = new TextEncoder()
const session = {
  session_id: 'session-1',
  title: '自动诊断 · 关卡失败',
  status: 'active',
  model: 'deepseek-v4.1-flash',
  message_count: 3,
  prompt_tokens: 20,
  completion_tokens: 12,
  total_tokens: 32,
  atomic_grant: { granted: true, expires_at: '2026-09-27T10:15:00Z', grant_id: 'grant-1' },
  created_at: '2026-09-27T10:00:00Z',
  updated_at: '2026-09-27T10:01:00Z',
  last_message_at: '2026-09-27T10:01:00Z',
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function event(name: string, data: unknown): string {
  return `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/agent?session_id=session-1']}>
        <AgentPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Agent conversation page', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (input, init) => {
      const request = input instanceof Request
        ? input
        : new Request(new URL(String(input), window.location.origin), init)
      const url = new URL(request.url)
      if (url.pathname === '/api/agent/sessions' && request.method === 'GET') {
        return json({ items: [session], total: 1, page: 1, size: 20 })
      }
      if (url.pathname === '/api/agent/sessions/session-1' && request.method === 'GET') {
        return json(session)
      }
      if (url.pathname === '/api/agent/sessions/session-1/messages' && request.method === 'GET') {
        return json({ items: [
          { id: 1, session_id: 'session-1', seq: 1, role: 'user', content: '分析刚才的失败', created_at: '2026-09-27T10:00:00Z' },
          { id: 2, session_id: 'session-1', seq: 2, role: 'assistant', content: '我正在检查关联日志。', tool_calls: [{ id: 'call-log', function: { name: 'get_logs', arguments: '{"source":"maa"}' } }], created_at: '2026-09-27T10:00:01Z' },
          { id: 3, session_id: 'session-1', seq: 3, role: 'tool', tool_name: 'get_logs', tool_call_id: 'call-log', audit_id: 22, content: '{"items":[],"total":0}', created_at: '2026-09-27T10:00:02Z' },
        ], total: 3, page: 1, size: 200 })
      }
      if (url.pathname === '/api/confirmations' && request.method === 'GET') {
        return json({ items: [], total: 0, page: 1, size: 20 })
      }
      if (url.pathname === '/api/agent/sessions/session-1/atomic-grant' && request.method === 'DELETE') {
        return new Response(null, { status: 204 })
      }
      if (url.pathname === '/api/agent/capabilities/check' && request.method === 'POST') {
        return json({ model: 'deepseek-v4.1-flash', ready: true, tool_calling: true, vision: false, status: 'vision_degraded', degraded_reason: '模型暂不支持图片输入' })
      }
      return json({ error: { message: `Unexpected request ${request.method} ${url.pathname}` } }, 404)
    }))
  })

  it('shows conversation history, collapsible tool evidence, and revokes session authorization', async () => {
    const fetchMock = vi.mocked(globalThis.fetch)
    renderPage()

    expect(await screen.findByRole('heading', { name: 'Agent' })).toBeInTheDocument()
    expect(await screen.findByText('分析刚才的失败')).toBeInTheDocument()
    expect(screen.getByText('我正在检查关联日志。')).toBeInTheDocument()
    expect(screen.getByText('原子操作已授权')).toBeInTheDocument()
    const toolTrace = screen.getByText('工具结果 · get_logs')
    expect(toolTrace.closest('details')).not.toHaveAttribute('open')
    fireEvent.click(toolTrace)
    expect(await screen.findByText(/"items": \[\]/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '撤销授权' }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => {
      const request = input instanceof Request ? input : null
      return request?.method === 'DELETE' && request.url.includes('/api/agent/sessions/session-1/atomic-grant')
    })).toBe(true))

    fireEvent.click(screen.getByRole('button', { name: '检查模型能力' }))
    expect(await screen.findByText('模型暂不支持图片输入')).toBeInTheDocument()
    expect(screen.getByText(/视觉能力不可用/)).toBeInTheDocument()
  })

  it('keeps the assistant turn waiting until an inline confirmation is resolved', async () => {
    let streamController: ReadableStreamDefaultController<Uint8Array> | null = null
    let streamFinished = false
    let persisted: unknown[] = []
    const confirmation = {
      confirmation_id: 'confirm-1',
      action: 'consume_resource',
      risk_level: 'consume',
      reason: '此操作会使用一次理智药。',
      payload: { tool_name: 'submit_pipeline', arguments: { tasks: [{ name: 'Fight', stage: '1-7', medicine: 1 }] }, session_id: 'session-1' },
      status: 'pending',
      expires_at: '2026-09-27T10:05:00Z',
    }
    const fetchMock = vi.mocked(globalThis.fetch)
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request
        ? input
        : new Request(new URL(String(input), window.location.origin), init)
      const url = new URL(request.url)
      if (url.pathname === '/api/agent/sessions/session-1/messages' && request.method === 'POST') {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            streamController = controller
            controller.enqueue(encoder.encode([
              event('message_start', { session_id: 'session-1', capability_status: 'ready' }),
              event('tool_start', { tool_call_id: 'call-1', name: 'submit_pipeline', arguments: confirmation.payload.arguments }),
              event('confirmation_required', { tool_call_id: 'call-1', name: 'submit_pipeline', confirmation_id: 'confirm-1', expires_at: confirmation.expires_at }),
            ].join('')))
          },
        })
        return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (url.pathname === '/api/confirmations/confirm-1' && request.method === 'GET') return json(confirmation)
      if (url.pathname === '/api/confirmations/confirm-1' && request.method === 'POST') {
        streamController?.enqueue(encoder.encode([
          event('tool_result', { tool_call_id: 'call-1', name: 'submit_pipeline', status: 'success' }),
          event('text_delta', { text: '已提交流水线，当前状态为排队中。' }),
          event('message_end', { session_id: 'session-1', finish_reason: 'stop' }),
        ].join('')))
        streamController?.close()
        // 服务端在这一轮落库后，历史接口就会返回同样的两句话。
        persisted = [
          { id: 11, session_id: 'session-1', seq: 1, role: 'user', content: '刷一次 1-7', created_at: '2026-09-27T10:01:00Z' },
          { id: 12, session_id: 'session-1', seq: 2, role: 'assistant', content: '已提交流水线，当前状态为排队中。', created_at: '2026-09-27T10:01:01Z' },
        ]
        streamFinished = true
        return json({ confirmation_id: 'confirm-1', status: 'approved' })
      }
      if (url.pathname === '/api/agent/sessions' && request.method === 'GET') return json({ items: [session], total: 1, page: 1, size: 20 })
      if (url.pathname === '/api/agent/sessions/session-1' && request.method === 'GET') return json(session)
      if (url.pathname === '/api/agent/sessions/session-1/messages' && request.method === 'GET') {
        return json({ items: persisted, total: persisted.length, page: 1, size: 200 })
      }
      if (url.pathname === '/api/confirmations' && request.method === 'GET') return json({ items: [], total: 0, page: 1, size: 20 })
      return json({ error: { message: `Unexpected request ${request.method} ${url.pathname}` } }, 404)
    })

    renderPage()
    const composer = await screen.findByRole('textbox', { name: '发送给 Agent' })
    fireEvent.change(composer, { target: { value: '刷一次 1-7' } })
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))

    expect(await screen.findByRole('heading', { name: '需要确认此操作' })).toBeInTheDocument()
    expect(await screen.findByText('此操作会使用一次理智药。')).toBeInTheDocument()
    expect(screen.getByText(/等待你确认/)).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: '批准并继续' }))

    expect(await screen.findByText('已提交流水线，当前状态为排队中。')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '需要确认此操作' })).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([input]) => {
      const request = input instanceof Request ? input : null
      return request?.method === 'POST' && request.url.includes('/api/confirmations/confirm-1')
    })).toBe(true)

    // 回归：流结束后本轮内容已回到历史，实时副本必须消失，否则同一句话出现两遍。
    await waitFor(() => expect(streamFinished).toBe(true))
    await waitFor(() => expect(screen.queryByText('你 · 本轮')).not.toBeInTheDocument())
    expect(screen.getAllByText('刷一次 1-7')).toHaveLength(1)
    expect(screen.getAllByText('已提交流水线，当前状态为排队中。')).toHaveLength(1)
  })
})
