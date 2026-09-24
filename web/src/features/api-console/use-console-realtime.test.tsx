import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { useConsoleRealtime } from './use-console-realtime'

class FakeSocket {
  static instances: FakeSocket[] = []
  static readonly OPEN = 1
  readyState = 0
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  sent: string[] = []
  readonly close = vi.fn(() => { this.readyState = 3 })

  constructor(readonly url: string) { FakeSocket.instances.push(this) }

  open() {
    this.readyState = FakeSocket.OPEN
    this.onopen?.(new Event('open'))
  }

  message(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent<string>)
  }

  closeWith(code: number) {
    this.readyState = 3
    this.onclose?.({ code } as CloseEvent)
  }

  send(value: string) { this.sent.push(value) }
}

function lastMessage(socket: FakeSocket): Record<string, unknown> {
  return JSON.parse(socket.sent.at(-1) ?? '{}') as Record<string, unknown>
}

describe('API console WebSocket lifecycle and subscriptions', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.useRealTimers()
    FakeSocket.instances = []
  })

  it('answers server_ping with pong carrying the same request id and timestamp', () => {
    vi.stubGlobal('WebSocket', FakeSocket)
    renderHook(() => useConsoleRealtime('http://maa.example:8002', null, null, null))
    const socket = FakeSocket.instances[0]
    act(() => socket.open())
    act(() => socket.message({ type: 'server_ping', req_id: 'ping-1', data: { t: 1758000009.125 } }))

    expect(lastMessage(socket)).toEqual({ type: 'pong', req_id: 'ping-1', data: { t: 1758000009.125 } })
  })

  it('starts with service-only logs and restores a pipeline filter without dropping queued request logs', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeSocket)
    const { result, rerender } = renderHook(
      ({ requestId, pipelineId }: { requestId: string | null; pipelineId: string | null }) => useConsoleRealtime('http://maa.example:8002', null, requestId, pipelineId),
      { initialProps: { requestId: null as string | null, pipelineId: null as string | null } },
    )
    const socket = FakeSocket.instances[0]
    act(() => socket.open())
    expect(lastMessage(socket)).toMatchObject({
      type: 'subscribe',
      data: { log_filter: { sources: ['service'], min_level: 'DEBUG', pipeline_id: null } },
    })

    rerender({ requestId: 'request-7', pipelineId: null })
    act(() => socket.message({ type: 'log', data: { id: 7, source: 'service', request_id: 'request-7', content: 'request received' } }))
    expect(result.current.logs).toHaveLength(1)
    rerender({ requestId: 'request-7', pipelineId: 'pipeline-3' })

    expect(lastMessage(socket)).toMatchObject({
      type: 'subscribe',
      data: {
        channels: ['log', 'core_status', 'device_status', 'pipeline_status'],
        log_filter: { sources: ['task', 'service', 'core'], min_level: 'INFO', pipeline_id: 'pipeline-3' },
      },
    })
    expect(result.current.logs).toHaveLength(1)

    act(() => socket.closeWith(1011))
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    const reconnected = FakeSocket.instances[1]
    act(() => reconnected.open())
    expect(lastMessage(reconnected)).toMatchObject({
      type: 'subscribe',
      data: {
        channels: ['log', 'core_status', 'device_status', 'pipeline_status'],
        log_filter: { sources: ['task', 'service', 'core'], min_level: 'INFO', pipeline_id: 'pipeline-3' },
      },
    })
    expect(result.current.logs).toHaveLength(1)
  })

  it('reconnects transient closes with exponential delays and preserves queued logs', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeSocket)
    const { result } = renderHook(() => useConsoleRealtime('http://maa.example:8002', null, 'request-1', null))
    const first = FakeSocket.instances[0]
    act(() => first.open())
    act(() => first.message({ type: 'log', data: { id: 1, source: 'service', request_id: 'request-1', content: 'queued before disconnect' } }))
    act(() => first.closeWith(1011))
    expect(FakeSocket.instances).toHaveLength(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(FakeSocket.instances).toHaveLength(2)
    const second = FakeSocket.instances[1]
    act(() => second.open())
    expect(lastMessage(second)).toMatchObject({ type: 'subscribe', data: { log_filter: { sources: ['service'], min_level: 'DEBUG' } } })
    expect(result.current.logs).toHaveLength(1)

    act(() => second.closeWith(1011))
    await act(async () => { await vi.advanceTimersByTimeAsync(999) })
    expect(FakeSocket.instances).toHaveLength(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(FakeSocket.instances).toHaveLength(3)

    for (const [socketIndex, delay] of [[2, 2_000], [3, 4_000], [4, 8_000]] as const) {
      const current = FakeSocket.instances[socketIndex]
      act(() => current.closeWith(1011))
      await act(async () => { await vi.advanceTimersByTimeAsync(delay - 1) })
      expect(FakeSocket.instances).toHaveLength(socketIndex + 1)
      await act(async () => { await vi.advanceTimersByTimeAsync(1) })
      expect(FakeSocket.instances).toHaveLength(socketIndex + 2)
      act(() => FakeSocket.instances.at(-1)!.open())
    }

    act(() => FakeSocket.instances.at(-1)!.closeWith(1011))
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000) })
    expect(FakeSocket.instances).toHaveLength(6)
  })

  it('does not retry permanent policy closes and clears retries when credentials change or the hook unmounts', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeSocket)
    const { rerender, unmount } = renderHook(
      ({ baseUrl, token }: { baseUrl: string; token: string | null }) => useConsoleRealtime(baseUrl, token, null, null),
      { initialProps: { baseUrl: 'http://maa.example:8002', token: 'one' } },
    )
    const first = FakeSocket.instances[0]
    act(() => first.closeWith(1008))
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000) })
    expect(FakeSocket.instances).toHaveLength(1)

    act(() => first.closeWith(1011))
    rerender({ baseUrl: 'http://maa-other.example:8002', token: 'two' })
    expect(first.close).toHaveBeenCalled()
    expect(FakeSocket.instances).toHaveLength(2)
    const second = FakeSocket.instances[1]
    act(() => second.closeWith(1011))
    unmount()
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000) })
    expect(FakeSocket.instances).toHaveLength(2)
  })
})
