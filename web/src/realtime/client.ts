import { toApiError } from '@/api/errors'
import { create } from 'zustand'
import { UNAUTHORIZED_EVENT, useAuth } from '@/stores/auth'
import {
  REALTIME_STATE_CHANNELS,
  type LogFilter,
  type LogRecord,
  type ServerEvent,
} from '@/realtime/events'

export type RealtimeConnectionState = 'CONNECTING' | 'CONNECTED' | 'RECONNECTING' | 'OFFLINE'

export interface RealtimeStatus {
  status: RealtimeConnectionState
  reconnectAttempt: number
  rtt: number | null
  truncated: boolean
  closeCode: number | null
  error: string | null
}

interface RealtimeStatusStore extends RealtimeStatus {
  setStatus: (patch: Partial<RealtimeStatus>) => void
}

export const useRealtimeStatusStore = create<RealtimeStatusStore>((set) => ({
  status: 'CONNECTING',
  reconnectAttempt: 0,
  rtt: null,
  truncated: false,
  closeCode: null,
  error: null,
  setStatus: (patch) => set(patch),
}))

export const LAST_SEEN_ID_STORAGE_KEY = 'maa-api-last-seen-id'
export const HEARTBEAT_INTERVAL_MS = 30_000
export const DEAD_CONNECTION_TIMEOUT_MS = 90_000
export const DEAD_CONNECTION_CHECK_INTERVAL_MS = 5_000
export const MAX_RECONNECT_ATTEMPTS = 10

const MAX_RECONNECT_DELAY_MS = 30_000
const DEFAULT_LOG_FILTER: LogFilter = {
  sources: ['task', 'service', 'core'],
  min_level: 'DEBUG',
  pipeline_id: null,
}

export interface SocketLike {
  readonly readyState: number
  onopen: ((event: Event) => void) | null
  onmessage: ((event: MessageEvent<string>) => void) | null
  onerror: ((event: Event) => void) | null
  onclose: ((event: CloseEvent) => void) | null
  send(data: string): void
  close(code?: number, reason?: string): void
}

export interface RealtimeClientOptions {
  createSocket?: (url: string) => SocketLike
  now?: () => number
  random?: () => number
  setTimeout?: typeof globalThis.setTimeout
  clearTimeout?: typeof globalThis.clearTimeout
  setInterval?: typeof globalThis.setInterval
  clearInterval?: typeof globalThis.clearInterval
  getToken?: () => string | null
  getStorage?: () => Pick<Storage, 'getItem' | 'setItem'> | null
  getVisibility?: () => DocumentVisibilityState
  makeRequestId?: () => string
}

function browserSocket(url: string): SocketLike {
  return new WebSocket(url)
}

function websocketUrl(): string {
  const url = new URL('/api/ws', window.location.href)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

function withToken(url: string, token: string): string {
  const parsed = new URL(url)
  parsed.searchParams.set('token', token)
  return parsed.toString()
}

function defaultRequestId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID()
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

function parseEvent(data: unknown): ServerEvent | null {
  if (typeof data !== 'string') return null
  try {
    const value: unknown = JSON.parse(data)
    if (!value || typeof value !== 'object') return null
    const event = value as Partial<ServerEvent>
    if (typeof event.type !== 'string' || !('data' in event)) return null
    return event as ServerEvent
  } catch {
    return null
  }
}

function asLogRecord(value: unknown): value is LogRecord {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<LogRecord>
  return Number.isSafeInteger(record.id) && typeof record.source === 'string'
}

export class RealtimeClient {
  private readonly createSocket: (url: string) => SocketLike
  private readonly now: () => number
  private readonly random: () => number
  private readonly setTimer: typeof globalThis.setTimeout
  private readonly clearTimer: typeof globalThis.clearTimeout
  private readonly setRepeater: typeof globalThis.setInterval
  private readonly clearRepeater: typeof globalThis.clearInterval
  private readonly getToken: () => string | null
  private readonly getStorage: () => Pick<Storage, 'getItem' | 'setItem'> | null
  private readonly getVisibility: () => DocumentVisibilityState
  private readonly makeRequestId: () => string
  private socket: SocketLike | null = null
  private retryTimer: ReturnType<typeof setTimeout> | undefined
  private heartbeatTimer: ReturnType<typeof setInterval> | undefined
  private deadCheckTimer: ReturnType<typeof setInterval> | undefined
  private eventHandler: ((event: ServerEvent) => void) | undefined
  private baseUrl: string | undefined
  private started = false
  private stopped = false
  private attempts = 0
  private lastFrameAt = 0
  private useQueryToken = false
  private rateLimited = false
  private fastRestart = false
  private cursor = 0
  private logEnabled = false
  private logFilter: LogFilter = { ...DEFAULT_LOG_FILTER, sources: [...DEFAULT_LOG_FILTER.sources] }
  private offlineRetryable = false
  private needsVisibleReconnect = false
  private readonly onUnauthorizedEvent = () => {
    this.stopped = true
    this.offlineRetryable = false
    this.clearRetry()
    this.closeCurrentSocket()
    useRealtimeStatusStore.getState().setStatus({ status: 'OFFLINE', error: 'Authentication failed' })
  }

  constructor(options: RealtimeClientOptions = {}) {
    this.createSocket = options.createSocket ?? browserSocket
    this.now = options.now ?? Date.now
    this.random = options.random ?? Math.random
    this.setTimer = options.setTimeout ?? globalThis.setTimeout.bind(globalThis)
    this.clearTimer = options.clearTimeout ?? globalThis.clearTimeout.bind(globalThis)
    this.setRepeater = options.setInterval ?? globalThis.setInterval.bind(globalThis)
    this.clearRepeater = options.clearInterval ?? globalThis.clearInterval.bind(globalThis)
    this.getToken = options.getToken ?? (() => useAuth.getState().token)
    this.getStorage = options.getStorage ?? (() => {
      try {
        return typeof window === 'undefined' ? null : window.localStorage
      } catch {
        return null
      }
    })
    this.getVisibility = options.getVisibility ?? (() => document.visibilityState)
    this.makeRequestId = options.makeRequestId ?? defaultRequestId
  }

  setEventHandler(handler: ((event: ServerEvent) => void) | undefined): void {
    this.eventHandler = handler
  }

  connect(): void {
    if (this.started && !this.stopped) return
    this.started = true
    this.stopped = false
    this.attempts = 0
    this.fastRestart = false
    this.useQueryToken = false
    this.baseUrl = websocketUrl()
    this.cursor = this.readCursor()
    if (typeof window !== 'undefined') window.addEventListener(UNAUTHORIZED_EVENT, this.onUnauthorizedEvent)
    this.openSocket()
  }

  disconnect(): void {
    this.stopped = true
    this.started = false
    this.offlineRetryable = false
    this.needsVisibleReconnect = false
    this.clearRetry()
    this.clearLivenessTimers()
    const socket = this.socket
    this.socket = null
    if (socket && socket.readyState < 2) socket.close(1000, 'client_disconnect')
    if (typeof window !== 'undefined') window.removeEventListener(UNAUTHORIZED_EVENT, this.onUnauthorizedEvent)
  }

  retryNow(): void {
    if (!this.baseUrl) this.baseUrl = websocketUrl()
    this.started = true
    this.stopped = false
    this.offlineRetryable = false
    this.needsVisibleReconnect = false
    this.attempts = 0
    this.fastRestart = false
    this.useQueryToken = false
    this.rateLimited = false
    this.clearRetry()
    this.closeCurrentSocket()
    this.openSocket()
  }

  setLogSubscription(enabled: boolean, filter?: Partial<LogFilter>): void {
    const wasEnabled = this.logEnabled
    if (filter) {
      this.logFilter = {
        ...this.logFilter,
        ...filter,
        sources: filter.sources ? [...filter.sources] : [...this.logFilter.sources],
      }
    }
    this.logEnabled = enabled

    if (this.socket?.readyState !== 1) return
    if (enabled && (!wasEnabled || filter)) {
      this.subscribeToLogs()
    } else if (!enabled && wasEnabled) {
      this.send({
        type: 'unsubscribe',
        req_id: this.makeRequestId(),
        data: { channels: ['log'] },
      })
    }
  }

  handleVisibilityChange(): void {
    if (this.getVisibility() !== 'visible') return
    if (this.stopped && !this.offlineRetryable) return
    const socket = this.socket
    if (!this.needsVisibleReconnect && socket?.readyState === 1 && this.now() - this.lastFrameAt <= DEAD_CONNECTION_TIMEOUT_MS) return

    this.stopped = false
    this.offlineRetryable = false
    this.needsVisibleReconnect = false
    this.attempts = 0
    this.fastRestart = false
    this.rateLimited = false
    this.clearRetry()
    this.closeCurrentSocket()
    this.openSocket()
  }

  private openSocket(): void {
    if (this.stopped || !this.baseUrl) return
    this.clearRetry()
    this.clearLivenessTimers()
    const url = this.useQueryToken
      ? withToken(this.baseUrl, this.getToken() ?? '')
      : this.baseUrl

    useRealtimeStatusStore.getState().setStatus({
      status: this.attempts === 0 && this.socket === null && !this.useQueryToken
        ? 'CONNECTING'
        : 'RECONNECTING',
      reconnectAttempt: this.attempts,
      error: null,
    })

    let socket: SocketLike
    try {
      socket = this.createSocket(url)
    } catch (error) {
      toApiError(error)
      useRealtimeStatusStore.getState().setStatus({ error: 'Unable to create WebSocket connection' })
      this.scheduleReconnect()
      return
    }
    this.socket = socket

    socket.onopen = () => {
      if (this.socket !== socket || this.stopped) return
      this.attempts = 0
      this.rateLimited = false
      this.lastFrameAt = this.now()
      useRealtimeStatusStore.getState().setStatus({
        status: 'CONNECTED',
        reconnectAttempt: 0,
        rtt: null,
        error: null,
        closeCode: null,
        truncated: false,
      })
      this.send({
        type: 'subscribe',
        req_id: this.makeRequestId(),
        data: {
          channels: this.logEnabled
            ? [...REALTIME_STATE_CHANNELS, 'log']
            : [...REALTIME_STATE_CHANNELS],
          log_filter: this.logFilter,
          last_seen_id: this.cursor,
        },
      })
      this.startLivenessTimers()
    }

    socket.onmessage = (message) => {
      if (this.socket !== socket || this.stopped) return
      this.lastFrameAt = this.now()
      const event = parseEvent(message.data)
      if (!event) return
      if (event.type === 'pong') {
        const sentAt = event.data.t
        if (typeof sentAt === 'number' && Number.isFinite(sentAt)) {
          const rtt = Math.max(0, this.now() / 1000 - sentAt) * 1000
          useRealtimeStatusStore.getState().setStatus({ rtt })
        }
      } else if (event.type === 'subscribed') {
        useRealtimeStatusStore.getState().setStatus({ truncated: event.data.truncated === true })
        this.eventHandler?.(event)
      } else if (event.type === 'server_shutdown') {
        this.fastRestart = true
        useRealtimeStatusStore.getState().setStatus({ status: 'RECONNECTING' })
        socket.close(1001, 'server_shutdown')
      } else if (event.type === 'log') {
        if (!this.logEnabled) return
        const [record] = this.acceptLogs([event.data])
        if (record) this.eventHandler?.({ ...event, data: record })
      } else if (event.type === 'log_batch') {
        if (!this.logEnabled) return
        const records = this.acceptLogs(Array.isArray(event.data.records) ? event.data.records : [])
        if (event.data.truncated) useRealtimeStatusStore.getState().setStatus({ truncated: true })
        this.eventHandler?.({ ...event, data: { ...event.data, records } })
      } else {
        this.eventHandler?.(event)
      }
    }

    socket.onerror = (event) => {
      if (this.socket !== socket || this.stopped) return
      // WebSocket error events contain no useful detail; normalize without retaining the URL.
      useRealtimeStatusStore.getState().setStatus({ error: toApiError(event).message })
    }

    socket.onclose = (event) => {
      if (this.socket !== socket) return
      this.socket = null
      this.clearLivenessTimers()
      if (this.stopped) return

      if (event.code === 4401 && !this.useQueryToken) {
        const token = this.getToken()
        if (token) {
          this.useQueryToken = true
          useRealtimeStatusStore.getState().setStatus({ status: 'RECONNECTING', error: null })
          this.openSocket()
        } else {
          this.unauthorized()
        }
        return
      }
      if (event.code === 4401) {
        this.unauthorized()
        return
      }
      if (event.code === 4403) {
        useRealtimeStatusStore.getState().setStatus({
          status: 'OFFLINE',
          closeCode: 4403,
          error: 'WebSocket origin is not trusted',
        })
        this.stopped = true
        return
      }
      if (event.code === 4429) this.rateLimited = true
      if (event.code === 1001 && event.reason === 'server_shutdown') this.fastRestart = true
      useRealtimeStatusStore.getState().setStatus({ closeCode: event.code })
      this.scheduleReconnect()
    }
  }

  private send(value: unknown): void {
    if (this.socket?.readyState !== 1) return
    try {
      this.socket.send(JSON.stringify(value))
    } catch (error) {
      toApiError(error)
      useRealtimeStatusStore.getState().setStatus({ error: 'WebSocket send failed' })
      this.socket.close()
    }
  }

  private subscribeToLogs(): void {
    this.send({
      type: 'subscribe',
      req_id: this.makeRequestId(),
      data: {
        channels: [...REALTIME_STATE_CHANNELS, 'log'],
        log_filter: this.logFilter,
      },
    })
  }

  private acceptLogs(records: readonly unknown[]): LogRecord[] {
    const accepted = this.filterLogs(records)
    if (accepted.length === 0) return accepted
    this.cursor = accepted.reduce((last, record) => Math.max(last, record.id), this.cursor)
    try {
      this.getStorage()?.setItem(LAST_SEEN_ID_STORAGE_KEY, String(this.cursor))
    } catch {
      // Keep the in-memory cursor if browser storage is unavailable.
    }
    return accepted
  }

  private filterLogs(records: readonly unknown[]): LogRecord[] {
    const valid = records.filter(asLogRecord).sort((a, b) => a.id - b.id)
    const accepted: LogRecord[] = []
    for (const record of valid) {
      if (record.id <= this.cursor) continue
      accepted.push(record)
      this.cursor = record.id
    }
    return accepted
  }

  private readCursor(): number {
    try {
      const value = Number(this.getStorage()?.getItem(LAST_SEEN_ID_STORAGE_KEY) ?? 0)
      return Number.isSafeInteger(value) && value > 0 ? value : 0
    } catch {
      return 0
    }
  }

  private startLivenessTimers(): void {
    this.heartbeatTimer = this.setRepeater(() => {
      if (this.getVisibility() !== 'visible' || this.socket?.readyState !== 1) return
      const t = this.now() / 1000
      this.send({ type: 'ping', req_id: this.makeRequestId(), data: { t } })
    }, HEARTBEAT_INTERVAL_MS)

    this.deadCheckTimer = this.setRepeater(() => {
      if (this.getVisibility() !== 'visible' || this.socket?.readyState !== 1) return
      if (this.now() - this.lastFrameAt > DEAD_CONNECTION_TIMEOUT_MS) this.socket.close()
    }, DEAD_CONNECTION_CHECK_INTERVAL_MS)
  }

  private scheduleReconnect(): void {
    this.clearLivenessTimers()
    if (this.stopped) return
    this.attempts += 1
    if (this.attempts >= MAX_RECONNECT_ATTEMPTS) {
      this.stopped = true
      this.offlineRetryable = true
      useRealtimeStatusStore.getState().setStatus({ status: 'OFFLINE', reconnectAttempt: this.attempts })
      return
    }
    if (this.getVisibility() !== 'visible') {
      this.needsVisibleReconnect = true
      useRealtimeStatusStore.getState().setStatus({ status: 'RECONNECTING', reconnectAttempt: this.attempts })
      return
    }

    const baseDelay = this.fastRestart && this.attempts === 1
      ? 3_000
      : this.rateLimited
        ? MAX_RECONNECT_DELAY_MS
        : Math.min(1_000 * 2 ** (this.attempts - 1), MAX_RECONNECT_DELAY_MS)
    const jitteredDelay = this.fastRestart && this.attempts === 1
      ? baseDelay
      : Math.round(baseDelay * (0.8 + this.random() * 0.4))
    this.fastRestart = false
    useRealtimeStatusStore.getState().setStatus({
      status: 'RECONNECTING',
      reconnectAttempt: this.attempts,
      error: null,
    })
    this.retryTimer = this.setTimer(() => {
      this.retryTimer = undefined
      this.openSocket()
    }, jitteredDelay)
  }

  private unauthorized(): void {
    this.stopped = true
    this.offlineRetryable = false
    this.clearRetry()
    this.clearLivenessTimers()
    useRealtimeStatusStore.getState().setStatus({
      status: 'OFFLINE',
      closeCode: 4401,
      error: 'Authentication failed',
    })
    useAuth.getState().onUnauthorized()
  }

  private closeCurrentSocket(): void {
    const socket = this.socket
    this.socket = null
    this.clearLivenessTimers()
    if (socket && socket.readyState < 2) socket.close(1000, 'client_retry')
  }

  private clearRetry(): void {
    if (this.retryTimer !== undefined) this.clearTimer(this.retryTimer)
    this.retryTimer = undefined
  }

  private clearLivenessTimers(): void {
    if (this.heartbeatTimer !== undefined) this.clearRepeater(this.heartbeatTimer)
    if (this.deadCheckTimer !== undefined) this.clearRepeater(this.deadCheckTimer)
    this.heartbeatTimer = undefined
    this.deadCheckTimer = undefined
  }
}

export const realtimeClient = new RealtimeClient()
