import { act } from '@testing-library/react'
import { QueryClient } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { keys } from '@/api/keys'
import { RealtimeClient, LAST_SEEN_ID_STORAGE_KEY, useRealtimeStatusStore } from '@/realtime/client'
import { handleServerEvent } from '@/realtime/query-bridge'
import type { SocketLike } from '@/realtime/client'
import { LOG_BUFFER_CAPACITY, useLogBuffer } from '@/stores/log-buffer'
import { UNAUTHORIZED_EVENT, useAuth } from '@/stores/auth'

class FakeSocket implements SocketLike {
  url = ''
  readyState = 0
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  readonly sent: string[] = []
  closeCalls: Array<{ code?: number; reason?: string }> = []

  open(): void {
    this.readyState = 1
    this.onopen?.(new Event('open'))
  }

  send(data: string): void {
    this.sent.push(data)
  }

  close(code = 1000, reason = ''): void {
    this.closeCalls.push({ code, reason })
    this.readyState = 3
    this.onclose?.({ code, reason } as CloseEvent)
  }

  receive(value: unknown): void {
    this.onmessage?.({ data: JSON.stringify(value) } as MessageEvent<string>)
  }

  sentFrames(): Array<Record<string, any>> {
    return this.sent.map((frame) => JSON.parse(frame) as Record<string, any>)
  }
}

function makeClient(options: ConstructorParameters<typeof RealtimeClient>[0] = {}) {
  const sockets: FakeSocket[] = []
  const client = new RealtimeClient({
    createSocket: (url) => {
      const socket = new FakeSocket()
      sockets.push(socket)
      socket.url = url
      return socket
    },
    now: () => Date.now(),
    random: () => 0.5,
    makeRequestId: (() => {
      let id = 0
      return () => `req-${++id}`
    })(),
    ...options,
  })
  return { client, sockets }
}

describe('RealtimeClient', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    localStorage.clear()
    useAuth.getState().clearToken()
    useLogBuffer.getState().clear()
    useRealtimeStatusStore.getState().setStatus({
      status: 'CONNECTING', reconnectAttempt: 0, rtt: null, truncated: false, closeCode: null, error: null,
    })
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
  })

  it('subscribes with stored cursor over cookie-first same-origin URL and calculates heartbeat RTT', () => {
    localStorage.setItem(LAST_SEEN_ID_STORAGE_KEY, '12')
    const { client, sockets } = makeClient()
    client.connect()

    expect(sockets).toHaveLength(1)
    expect(sockets[0].url).toBe(`${window.location.origin.replace(/^http/, 'ws')}/api/ws`)
    expect(sockets[0].url).not.toContain('token=')

    sockets[0].open()
    const subscribe = sockets[0].sentFrames()[0]
    expect(subscribe).toMatchObject({ type: 'subscribe', data: { last_seen_id: 12 } })
    expect(subscribe.data.channels).not.toContain('log')
    expect(subscribe.data.log_filter.sources).toEqual(['task', 'service', 'core'])

    vi.advanceTimersByTime(30_000)
    const ping = sockets[0].sentFrames().find((frame) => frame.type === 'ping')
    expect(ping).toBeDefined()
    expect(ping?.data.t).toBe(Date.now() / 1000)

    sockets[0].receive({ type: 'pong', req_id: ping?.req_id, data: { t: ping?.data.t } })
    expect(useRealtimeStatusStore.getState().rtt).toBe(0)
    client.disconnect()
  })

  it('only retries with a token query after 4401; it never logs that URL and 4403 does not retry', () => {
    useAuth.getState().setToken('secret value')
    const { client, sockets } = makeClient()
    client.connect()
    sockets[0].close(4401, 'unauthorized')

    expect(sockets).toHaveLength(2)
    expect(sockets[0].url).not.toContain('token=')
    expect(sockets[1].url).toContain('token=secret+value')

    const logSpy = vi.spyOn(console, 'error')
    sockets[1].onerror?.(new Event('error'))
    expect(logSpy).not.toHaveBeenCalledWith(expect.stringContaining('secret'))
    sockets[1].close(4403, 'untrusted_origin')
    expect(sockets).toHaveLength(2)
    expect(useRealtimeStatusStore.getState()).toMatchObject({ status: 'OFFLINE', closeCode: 4403 })
    client.disconnect()

    const cookieOnly = makeClient()
    cookieOnly.client.connect()
    cookieOnly.sockets[0].close(4403, 'untrusted_origin')
    expect(cookieOnly.sockets).toHaveLength(1)
    expect(cookieOnly.sockets[0].url).not.toContain('token=')
    cookieOnly.client.disconnect()
  })

  it('dispatches unauthorized through the auth event and does not reconnect after query-token rejection', () => {
    useAuth.getState().setToken('expired')
    const unauthorized = vi.fn()
    window.addEventListener(UNAUTHORIZED_EVENT, unauthorized)
    const { client, sockets } = makeClient()
    client.connect()
    sockets[0].close(4401, 'unauthorized')
    sockets[1].close(4401, 'unauthorized')

    expect(unauthorized).toHaveBeenCalledOnce()
    expect(useAuth.getState().token).toBeNull()
    expect(sockets).toHaveLength(2)
    expect(useRealtimeStatusStore.getState().status).toBe('OFFLINE')
    client.disconnect()
    window.removeEventListener(UNAUTHORIZED_EVENT, unauthorized)
  })

  it('deduplicates single and batched logs by id, persists the cursor, and exposes truncated backfill', () => {
    const { client, sockets } = makeClient()
    client.connect()
    sockets[0].open()
    client.setLogSubscription(true)
    const queryClient = new QueryClient()
    client.setEventHandler((event) => handleServerEvent(event, queryClient))
    sockets[0].receive({ type: 'log', id: 4, data: { id: 4, source: 'task', content: 'four' } })
    sockets[0].receive({
      type: 'log_batch',
      data: {
        records: [
          { id: 4, source: 'task', content: 'duplicate' },
          { id: 5, source: 'task', content: 'five' },
          { id: 6, source: 'core', content: 'six' },
        ],
        truncated: true,
      },
    })
    expect(localStorage.getItem(LAST_SEEN_ID_STORAGE_KEY)).toBe('6')
    expect(useRealtimeStatusStore.getState().truncated).toBe(true)

    act(() => vi.advanceTimersByTime(20))
    expect(useLogBuffer.getState().bySource.task.map(({ id }) => id)).toEqual([4, 5])
    expect(useLogBuffer.getState().bySource.core.map(({ id }) => id)).toEqual([6])
    client.disconnect()
    queryClient.clear()
  })

  it('dispatches the subscription acknowledgement after reconnect for stale query recovery', () => {
    const { client, sockets } = makeClient()
    const queryClient = new QueryClient()
    const received: string[] = []
    client.setEventHandler((event) => {
      received.push(event.type)
      handleServerEvent(event, queryClient)
    })
    client.connect()
    sockets[0].open()
    sockets[0].receive({ type: 'subscribed', data: { channels: ['agent'], backfilled: 0 } })

    expect(received).toContain('subscribed')

    client.disconnect()
    queryClient.clear()
  })

  it('subscribes to logs only on demand, unsubscribes on exit, and retains the filter across reconnects', () => {
    const { client, sockets } = makeClient()
    client.connect()
    sockets[0].open()
    const firstSubscribe = sockets[0].sentFrames()[0]
    expect(firstSubscribe.type).toBe('subscribe')
    expect(firstSubscribe.data.channels).not.toContain('log')
    expect(sockets[0].sentFrames()).toHaveLength(1)

    client.setLogSubscription(true, { sources: ['task'], min_level: 'INFO' })
    const enabled = sockets[0].sentFrames()[1]
    expect(enabled).toMatchObject({
      type: 'subscribe',
      data: { channels: expect.arrayContaining(['log']), log_filter: { sources: ['task'], min_level: 'INFO' } },
    })
    client.setLogSubscription(false)
    expect(sockets[0].sentFrames()[2]).toMatchObject({ type: 'unsubscribe', data: { channels: ['log'] } })

    sockets[0].close(1006, '')
    vi.advanceTimersByTime(1_000)
    sockets[1].open()
    const frames = sockets[1].sentFrames()
    expect(frames[0].data.channels).not.toContain('log')
    expect(frames).toHaveLength(1)

    client.setLogSubscription(true)
    expect(sockets[1].sentFrames()[1].data.log_filter).toMatchObject({
      sources: ['task'], min_level: 'INFO',
    })
    client.disconnect()
  })

  it('puts an enabled log channel and persisted cursor in the sole first subscribe after reconnect', () => {
    localStorage.setItem(LAST_SEEN_ID_STORAGE_KEY, '41')
    const { client, sockets } = makeClient()
    client.setLogSubscription(true, { sources: ['service'], min_level: 'WARNING' })
    client.connect()
    sockets[0].open()

    expect(sockets[0].sentFrames()).toHaveLength(1)
    expect(sockets[0].sentFrames()[0]).toMatchObject({
      type: 'subscribe',
      data: {
        channels: expect.arrayContaining(['log', 'pipeline_status', 'core_status']),
        last_seen_id: 41,
        log_filter: { sources: ['service'], min_level: 'WARNING' },
      },
    })

    sockets[0].close(1006, '')
    vi.advanceTimersByTime(1_000)
    sockets[1].open()
    const reconnectFrames = sockets[1].sentFrames()
    expect(reconnectFrames).toHaveLength(1)
    expect(reconnectFrames[0]).toMatchObject({
      type: 'subscribe',
      data: {
        channels: expect.arrayContaining(['log']),
        last_seen_id: 41,
        log_filter: { sources: ['service'], min_level: 'WARNING' },
      },
    })
    client.disconnect()
  })

  it('reconnects with bounded exponential jitter, honors fast shutdown delay, and goes offline after ten failures', () => {
    const { client, sockets } = makeClient({ random: () => 0.5 })
    client.connect()
    sockets[0].receive({ type: 'server_shutdown', data: {} })
    expect(sockets[0].closeCalls[0]).toMatchObject({ code: 1001, reason: 'server_shutdown' })
    vi.advanceTimersByTime(2_999)
    expect(sockets).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(sockets).toHaveLength(2)

    sockets[1].close(1006, '')
    vi.advanceTimersByTime(2_000)
    expect(sockets).toHaveLength(3)
    for (let failure = 0; failure < 7; failure += 1) {
      const current = sockets[sockets.length - 1]
      current.close(1006, '')
      vi.advanceTimersByTime(30_000)
    }
    expect(sockets).toHaveLength(10)
    sockets.at(-1)?.close(1006, '')
    expect(useRealtimeStatusStore.getState()).toMatchObject({ status: 'OFFLINE', reconnectAttempt: 10 })
    client.disconnect()
  })

  it('applies 20% jitter, detects a silent dead socket, and reconnects immediately when visibility returns', () => {
    const { client, sockets } = makeClient({ random: () => 0 })
    client.connect()
    sockets[0].close(1006, '')
    vi.advanceTimersByTime(799)
    expect(sockets).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(sockets).toHaveLength(2)

    sockets[1].open()
    vi.advanceTimersByTime(95_000)
    expect(sockets[1].closeCalls.length).toBeGreaterThan(0)
    expect(useRealtimeStatusStore.getState().status).toBe('RECONNECTING')
    client.disconnect()

    let visibility: DocumentVisibilityState = 'hidden'
    const resumed = makeClient({ getVisibility: () => visibility })
    resumed.client.connect()
    resumed.sockets[0].open()
    resumed.sockets[0].close(1006, '')
    expect(resumed.sockets).toHaveLength(1)
    visibility = 'visible'
    resumed.client.handleVisibilityChange()
    expect(resumed.sockets).toHaveLength(2)
    resumed.client.disconnect()
  })

  it('keeps a per-source 2000 row ring buffer and flushes a burst in one Zustand update', () => {
    const listener = vi.fn()
    const unsubscribe = useLogBuffer.subscribe(listener)
    useLogBuffer.getState().appendMany(
      Array.from({ length: LOG_BUFFER_CAPACITY + 17 }, (_, index) => ({
        id: index + 1,
        source: 'task',
        content: `line-${index + 1}`,
      })),
    )
    act(() => vi.advanceTimersByTime(20))
    const rows = useLogBuffer.getState().bySource.task
    expect(rows).toHaveLength(LOG_BUFFER_CAPACITY)
    expect(rows[0].id).toBe(18)
    expect(rows.at(-1)?.id).toBe(LOG_BUFFER_CAPACITY + 17)
    expect(listener).toHaveBeenCalledTimes(1)
    unsubscribe()
  })

  it('patches exact query keys for status events and batches queue invalidation', () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    queryClient.setQueryData(keys.system.health(), { status: 'ok' })
    queryClient.setQueryData(keys.pipelines.current(), { pipeline: { pipeline_id: 'p1' } })

    handleServerEvent({ type: 'core_status', data: { state: 'ready' } }, queryClient)
    handleServerEvent({ type: 'device_status', data: { state: 'connected' } }, queryClient)
    expect(queryClient.getQueryData(keys.system.health())).toMatchObject({
      core: { state: 'ready' }, device: { state: 'connected' },
    })

    handleServerEvent({
      type: 'pipeline_status', data: { pipeline_id: 'p1', status: 'completed' },
    }, queryClient)
    expect(queryClient.getQueryData(keys.pipelines.current())).toEqual({ pipeline: null })
    expect(invalidate).not.toHaveBeenCalled()
    act(() => vi.advanceTimersByTime(250))
    expect(invalidate).toHaveBeenCalledTimes(2)
    queryClient.clear()
  })
})
