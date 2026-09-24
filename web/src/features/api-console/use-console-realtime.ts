import { useEffect, useMemo, useRef, useState } from 'react'
import { consoleWebSocketUrl, selectLogs } from './utils'
import type { LogRecord } from '@/realtime/events'

export type ConsoleRealtimeState = 'CONNECTING' | 'CONNECTED' | 'OFFLINE'

const CHANNELS = ['log', 'core_status', 'device_status', 'pipeline_status']
const INITIAL_RECONNECT_MS = 500
const MAX_RECONNECT_MS = 8_000
const MAX_RECONNECT_ATTEMPTS = 5
const STABLE_CONNECTION_MS = 30_000

function randomId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `console-${Date.now()}`
}

function subscribe(socket: WebSocket, pipelineId: string | null, lastSeenId: number | null): void {
  if (socket.readyState !== WebSocket.OPEN) return
  const hasPipeline = Boolean(pipelineId)
  socket.send(JSON.stringify({
    type: 'subscribe',
    req_id: randomId(),
    data: {
      channels: CHANNELS,
      log_filter: {
        sources: hasPipeline ? ['task', 'service', 'core'] : ['service'],
        min_level: hasPipeline ? 'INFO' : 'DEBUG',
        pipeline_id: pipelineId,
      },
      ...(lastSeenId !== null ? { last_seen_id: lastSeenId } : {}),
    },
  }))
}

type LogContinuation = {
  scope: string
  lastSeenId: number | null
  seenIds: Set<number>
  seenIdOrder: number[]
}

function isPermanentClose(code: number | undefined): boolean {
  return code === 1000 || code === 1002 || code === 1003 || code === 1008 ||
    code === 4001 || code === 4003 || code === 4401 || code === 4403
}

function objectOf(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

export function useConsoleRealtime(baseUrl: string, token: string | null, requestId: string | null, pipelineId: string | null) {
  const [connection, setConnection] = useState<ConsoleRealtimeState>('CONNECTING')
  const [logs, setLogs] = useState<Record<string, unknown>[]>([])
  const [timeline, setTimeline] = useState<Array<{ type: string; data: Record<string, unknown>; at: number }>>([])
  const [historyTruncated, setHistoryTruncated] = useState(false)
  const socketUrl = useMemo(() => consoleWebSocketUrl(baseUrl, token), [baseUrl, token])
  const continuationScope = useMemo(() => {
    const url = new URL(socketUrl)
    url.searchParams.delete('token')
    return url.toString()
  }, [socketUrl])
  const currentPipelineId = useRef(pipelineId)
  const activeSocket = useRef<WebSocket | null>(null)
  const logContinuation = useRef<LogContinuation>({ scope: continuationScope, lastSeenId: null, seenIds: new Set(), seenIdOrder: [] })
  currentPipelineId.current = pipelineId

  useEffect(() => {
    if (typeof WebSocket === 'undefined') {
      setConnection('OFFLINE')
      return
    }

    let stopped = false
    let socket: WebSocket | null = null
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    let stableTimer: ReturnType<typeof setTimeout> | null = null
    let attempts = 0
    if (logContinuation.current.scope !== continuationScope) {
      logContinuation.current = { scope: continuationScope, lastSeenId: null, seenIds: new Set(), seenIdOrder: [] }
      setHistoryTruncated(false)
    }
    const continuation = logContinuation.current

    const appendLogs = (entries: unknown[]) => {
      const accepted = entries.filter((entry): entry is Record<string, unknown> => Boolean(entry && typeof entry === 'object' && !Array.isArray(entry)))
        .filter((entry) => {
          const id = entry.id
          if (typeof id !== 'number' || !Number.isSafeInteger(id) || id < 0) return true
          if (continuation.seenIds.has(id)) return false
          continuation.seenIds.add(id)
          continuation.seenIdOrder.push(id)
          if (continuation.lastSeenId === null || id > continuation.lastSeenId) continuation.lastSeenId = id
          if (continuation.seenIdOrder.length > 600) continuation.seenIds.delete(continuation.seenIdOrder.shift()!)
          return true
        })
      if (accepted.length > 0) setLogs((current) => [...current, ...accepted as LogRecord[]].slice(-300))
    }

    const clearRetryTimer = () => {
      if (retryTimer !== null) clearTimeout(retryTimer)
      retryTimer = null
    }
    const clearStableTimer = () => {
      if (stableTimer !== null) clearTimeout(stableTimer)
      stableTimer = null
    }

    const connect = () => {
      if (stopped) return
      setConnection('CONNECTING')
      try {
        socket = new WebSocket(socketUrl)
        activeSocket.current = socket
      } catch {
        scheduleReconnect()
        return
      }

      const thisSocket = socket
      thisSocket.onopen = () => {
        if (stopped || socket !== thisSocket) return
        setConnection('CONNECTED')
        subscribe(thisSocket, currentPipelineId.current, continuation.lastSeenId)
        clearStableTimer()
        stableTimer = setTimeout(() => {
          attempts = 0
          stableTimer = null
        }, STABLE_CONNECTION_MS)
      }
      thisSocket.onmessage = (message) => {
        try {
          const event = JSON.parse(String(message.data)) as Record<string, unknown> & { type?: string; req_id?: unknown; data?: unknown }
          if (event.type === 'server_ping') {
            const ping = objectOf(event.data)
            thisSocket.send(JSON.stringify({
              type: 'pong',
              ...(event.req_id !== undefined ? { req_id: event.req_id } : {}),
              data: { t: ping.t },
            }))
          } else if (event.type === 'log' && event.data && typeof event.data === 'object') {
            appendLogs([event.data])
          } else if (event.type === 'log_batch' && event.data && typeof event.data === 'object') {
            const batch = event.data as { records?: unknown[]; truncated?: unknown }
            if (batch.truncated === true) setHistoryTruncated(true)
            appendLogs(Array.isArray(batch.records) ? batch.records : [])
          } else if (event.type === 'subscribed' && objectOf(event.data).truncated === true) {
            setHistoryTruncated(true)
          } else if (event.type === 'core_status' || event.type === 'device_status' || event.type === 'pipeline_status') {
            const data = event.data && typeof event.data === 'object' ? event.data as Record<string, unknown> : {}
            setTimeline((current) => [...current, { type: event.type!, data, at: Date.now() }].slice(-8))
          }
        } catch { /* Ignore malformed frames without changing shared realtime state. */ }
      }
      thisSocket.onerror = () => {
        if (socket === thisSocket) setConnection('OFFLINE')
      }
      thisSocket.onclose = (event) => {
        if (socket !== thisSocket) return
        activeSocket.current = null
        clearStableTimer()
        setConnection('OFFLINE')
        scheduleReconnect(event.code)
      }
    }

    function scheduleReconnect(code?: number) {
      if (stopped || retryTimer !== null) return
      if (isPermanentClose(code) || attempts >= MAX_RECONNECT_ATTEMPTS) {
        setConnection('OFFLINE')
        return
      }
      attempts += 1
      const delay = Math.min(INITIAL_RECONNECT_MS * (2 ** (attempts - 1)), MAX_RECONNECT_MS)
      retryTimer = setTimeout(() => {
        retryTimer = null
        connect()
      }, delay)
      setConnection('OFFLINE')
    }

    connect()

    return () => {
      stopped = true
      clearRetryTimer()
      clearStableTimer()
      if (socket && activeSocket.current === socket) activeSocket.current = null
      if (socket) {
        socket.onclose = null
        socket.onerror = null
        socket.onmessage = null
        socket.onopen = null
        socket.close(1000, 'api_console_unmount')
      }
    }
  }, [socketUrl, continuationScope])

  useEffect(() => {
    const socket = activeSocket.current
    if (socket && typeof WebSocket !== 'undefined') subscribe(socket, pipelineId, null)
  }, [requestId, pipelineId])

  return {
    connection,
    logs: requestId ? selectLogs(logs, requestId, pipelineId) : [],
    timeline,
    historyTruncated,
  }
}
