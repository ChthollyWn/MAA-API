import { useEffect, useMemo, useState } from 'react'
import { consoleWebSocketUrl, selectLogs } from './utils'
import type { LogRecord, ServerEvent } from '@/realtime/events'

export type ConsoleRealtimeState = 'CONNECTING' | 'CONNECTED' | 'OFFLINE'

export function useConsoleRealtime(baseUrl: string, token: string | null, requestId: string | null, pipelineId: string | null) {
  const [connection, setConnection] = useState<ConsoleRealtimeState>('CONNECTING')
  const [logs, setLogs] = useState<Record<string, unknown>[]>([])
  const [timeline, setTimeline] = useState<Array<{ type: string; data: Record<string, unknown>; at: number }>>([])
  const socketUrl = useMemo(() => consoleWebSocketUrl(baseUrl, token), [baseUrl, token])

  useEffect(() => {
    if (typeof WebSocket === 'undefined') {
      setConnection('OFFLINE')
      return
    }
    setConnection('CONNECTING')
    let socket: WebSocket
    try {
      socket = new WebSocket(socketUrl)
    } catch {
      setConnection('OFFLINE')
      return
    }
    socket.onopen = () => {
      setConnection('CONNECTED')
      socket.send(JSON.stringify({
        type: 'subscribe',
        req_id: globalThis.crypto?.randomUUID?.() ?? `console-${Date.now()}`,
        data: {
          channels: ['log', 'core_status', 'device_status', 'pipeline_status'],
          log_filter: { sources: ['task', 'service', 'core'], min_level: 'DEBUG', pipeline_id: null },
        },
      }))
    }
    socket.onmessage = (message) => {
      try {
        const event = JSON.parse(String(message.data)) as Partial<ServerEvent>
        if (event.type === 'log' && event.data && typeof event.data === 'object') {
          setLogs((current) => [...current, event.data as LogRecord].slice(-300))
        } else if (event.type === 'log_batch' && event.data && typeof event.data === 'object') {
          const batch = event.data as { records?: unknown[] }
          setLogs((current) => [...current, ...(batch.records ?? []).filter((entry): entry is Record<string, unknown> => Boolean(entry && typeof entry === 'object'))].slice(-300))
        } else if (event.type === 'core_status' || event.type === 'device_status' || event.type === 'pipeline_status') {
          const data = event.data && typeof event.data === 'object' ? event.data as Record<string, unknown> : {}
          setTimeline((current) => [...current, { type: event.type!, data, at: Date.now() }].slice(-8))
        }
      } catch { /* Ignore malformed frames without changing shared realtime state. */ }
    }
    socket.onerror = () => setConnection('OFFLINE')
    socket.onclose = () => setConnection('OFFLINE')
    return () => socket.close(1000, 'api_console_unmount')
  }, [socketUrl])

  return {
    connection,
    logs: requestId ? selectLogs(logs, requestId, pipelineId) : [],
    timeline,
  }
}
