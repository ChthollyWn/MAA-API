export type WsEnvelope<TType extends string = string, TData = unknown> = {
  type: TType
  ts?: number
  req_id?: string
  data: TData
  id?: number
}

export interface LogRecord {
  id: number
  stream_id?: string
  source: string
  level?: string
  content?: string
  [key: string]: unknown
}

export interface LogBatch {
  records: LogRecord[]
  truncated: boolean
  stream_id?: string
}

export interface PipelineStatus {
  pipeline_id: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | string
  [key: string]: unknown
}

export interface TaskStatus {
  pipeline_id: string
  task_id: string
  status: string
  [key: string]: unknown
}

export type ServerEvent =
  | WsEnvelope<'log', LogRecord>
  | WsEnvelope<'log_batch', LogBatch>
  | WsEnvelope<'pipeline_status', PipelineStatus>
  | WsEnvelope<'task_status', TaskStatus>
  | WsEnvelope<'queue_changed', Record<string, unknown>>
  | WsEnvelope<'core_status', Record<string, unknown>>
  | WsEnvelope<'device_status', Record<string, unknown>>
  | WsEnvelope<'confirm_request', Record<string, unknown>>
  | WsEnvelope<'confirm_resolved', Record<string, unknown>>
  | WsEnvelope<'update_progress', Record<string, unknown> & { update_id: string }>
  | WsEnvelope<'update_available', Record<string, unknown>>
  | WsEnvelope<'agent_event', Record<string, unknown>>
  | WsEnvelope<'server_shutdown', Record<string, unknown>>
  | WsEnvelope<'subscribed', { channels?: string[]; backfilled?: number; truncated?: boolean; stream_id?: string | null }>
  | WsEnvelope<'pong', { t?: number }>
  | WsEnvelope<'error', { code?: string; message?: string }>

export const REALTIME_STATE_CHANNELS = [
  'pipeline_status',
  'task_status',
  'queue_changed',
  'core_status',
  'device_status',
  'confirm_request',
  'confirm_resolved',
  'update_progress',
  'update_available',
  'agent_event',
] as const

export const REALTIME_CHANNELS = ['log', ...REALTIME_STATE_CHANNELS] as const

export type RealtimeChannel = (typeof REALTIME_CHANNELS)[number]

export interface LogFilter {
  sources: string[]
  min_level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'
  pipeline_id: string | null
}
