import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { orchestratorEndpoints, useConversation } from './use-conversation'

const encoder = new TextEncoder()

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

/** 保持打开的 SSE 流：测试自己决定何时推送后续事件与结束。 */
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

function page(items: unknown[], total = items.length): Response {
  return new Response(JSON.stringify({ items, total, page: 1, size: 200 }), {
    headers: { 'Content-Type': 'application/json' },
  })
}

function message(overrides: Record<string, unknown>): Record<string, unknown> {
  return { session_id: 'main', seq: 1, role: 'user', content: '', ...overrides }
}

function setup() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return { wrapper }
}

function requests(fetchMock: ReturnType<typeof vi.fn<typeof fetch>>): Request[] {
  return fetchMock.mock.calls.map(([input, init]) => (input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)))
}

describe('M72 useConversation', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('sends a message, streams the live turn and drops it after the history refresh', async () => {
    let messages = [message({ id: 1, content: '你好' })]
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
      const url = new URL(request.url)
      if (request.method === 'GET') return page(messages)
      if (url.pathname === '/api/agent/orchestrator/messages') {
        messages = [...messages, message({ id: 2, content: '把日常做了' })]
        return sse([
          event('run_start', { run_id: 'r1', parent_run_id: null, agent: 'orchestrator', kind: 'orchestrator_turn', ticket_id: null }),
          event('text_delta', { run_id: 'r1', text: '好，开始。' }),
          event('run_end', { run_id: 'r1', finish_reason: 'stop' }),
        ])
      }
      return page(messages)
    })
    vi.stubGlobal('fetch', fetchMock)
    const { wrapper } = setup()
    const { result } = renderHook(() => useConversation(orchestratorEndpoints(), { turnRunning: false, enabled: true }), { wrapper })

    await waitFor(() => expect(result.current.query.isSuccess).toBe(true))
    expect(result.current.history.map((item) => item.id)).toEqual([1])

    let sent = false
    await act(async () => {
      sent = await result.current.send({ content: '把日常做了', mentions: [] })
    })
    expect(sent).toBe(true)
    const posts = requests(fetchMock).filter((request) => request.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(posts[0]!.url).toContain('/api/agent/orchestrator/messages')
    expect(await posts[0]!.clone().json()).toEqual({ content: '把日常做了', mentions: [] })
    await waitFor(() => expect(result.current.live).toBeNull())
    expect(result.current.history.map((item) => item.id)).toEqual([1, 2])
    expect(result.current.showOptimisticUser).toBe(false)
  })

  it('queues a second message while the first stream is still running', async () => {
    const stream = heldStream([
      event('run_start', { run_id: 'r1', parent_run_id: null, agent: 'orchestrator', kind: 'orchestrator_turn', ticket_id: null }),
      event('text_delta', { run_id: 'r1', text: '处理中' }),
    ])
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
      if (request.method === 'GET') return page([])
      if (fetchMock.mock.calls.length > 1) return new Response(null, { status: 200 })
      return stream.response
    })
    vi.stubGlobal('fetch', fetchMock)
    const { wrapper } = setup()
    const { result } = renderHook(() => useConversation(orchestratorEndpoints(), { turnRunning: false, enabled: true }), { wrapper })
    await waitFor(() => expect(result.current.query.isSuccess).toBe(true))

    let first: Promise<boolean> | null = null
    act(() => { first = result.current.send({ content: '第一条', mentions: [] }) })
    await waitFor(() => expect(result.current.live?.running).toBe(true))

    let queued = false
    await act(async () => {
      queued = await result.current.send({ content: '第二条', mentions: [] })
    })
    expect(queued).toBe(true)
    expect(result.current.queued).toEqual([expect.objectContaining({ text: '第二条' })])

    await act(async () => {
      stream.push([event('run_end', { run_id: 'r1', finish_reason: 'stop' })])
      stream.close()
      await first
    })
  })

  it('stops the running turn through the interrupt endpoint', async () => {
    const stream = heldStream([
      event('run_start', { run_id: 'r1', parent_run_id: null, agent: 'orchestrator', kind: 'orchestrator_turn', ticket_id: null }),
      event('text_delta', { run_id: 'r1', text: '处理中' }),
    ])
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
      const url = new URL(request.url)
      if (request.method === 'GET') return page([])
      if (url.pathname === '/api/agent/orchestrator/interrupt') {
        return new Response(JSON.stringify({ session_id: 'main', interrupted: true, turn_running: false }), {
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return stream.response
    })
    vi.stubGlobal('fetch', fetchMock)
    const { wrapper } = setup()
    const { result } = renderHook(() => useConversation(orchestratorEndpoints(), { turnRunning: false, enabled: true }), { wrapper })
    await waitFor(() => expect(result.current.query.isSuccess).toBe(true))

    let first: Promise<boolean> | null = null
    act(() => { first = result.current.send({ content: '停一下', mentions: [] }) })
    await waitFor(() => expect(result.current.canStop).toBe(true))

    act(() => { result.current.stop() })
    await waitFor(() => expect(result.current.stopping).toBe(false))
    expect(requests(fetchMock).some((request) => request.url.endsWith('/api/agent/orchestrator/interrupt'))).toBe(true)

    await act(async () => {
      stream.close()
      await first
    })
    expect(result.current.live).toBeNull()
  })
})
