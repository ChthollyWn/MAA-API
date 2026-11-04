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

export interface TicketStatusEvent {
  event: 'ticket_created' | 'ticket_updated' | 'report_created'
  ticket_id: string
  title: string
  kind: string
  status: string
  last_report_status: string | null
  summary?: string | null
  human_action?: { type: string; prompt: string; ref_id?: string | null } | null
}

// M70 §4.3.3：agent_run 频道的三种消息。没有运行的消息（路由写的用户消息）
// run_id、kind、status 为 null；run_finished 额外带 finish_reason。
export interface AgentRunEvent {
  event: 'run_started' | 'run_finished' | 'message_created'
  run_id: string | null
  session_id: string
  ticket_id: string | null
  agent: string | null
  kind: string | null
  status: string | null
  finish_reason?: string | null
  message?: {
    id: number | null
    seq: number | null
    role: string | null
    inbox_id: number | null
    preview: string
  }
}

// M75 §4.6：agent_event 里与计划、产物有关的两条消息。载荷与 04 §4.2 一致；
// `artifact_created` 不带 ticket_id，列表只能按整片前缀失效。
export interface PlanUpdatedEvent {
  event: 'plan_updated'
  session_id: string
  payload: {
    run_id: string | null
    plan_id: string
    status: string
    items: { id: string; title: string; status: string; note?: string | null }[]
  }
}

export interface ArtifactCreatedEvent {
  event: 'artifact_created'
  session_id: string | null
  payload: {
    run_id: string | null
    artifact_id: string
    kind: string
    title: string
  }
}

// M79 §4.5：写入 proposed（或 require_review=false 直接生效）时广播的记忆提议。
// 载荷只带 id、种类与正文前 80 字；卡片正文由记忆页重新读取。
export interface MemoryProposedEvent {
  event: 'memory_proposed'
  session_id: string | null
  payload: {
    id: string
    kind: string
    preview: string
  }
}

// M80 §4.3：回溯提交后广播，其他标签页据此重拉该会话的消息窗口。
export interface SessionRewoundEvent {
  event: 'session_rewound'
  session_id: string
  payload: {
    session_id: string
    to_seq: number
    superseded: number
    notice_message_id: number | null
  }
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
  | WsEnvelope<'question_request', Record<string, unknown>>
  | WsEnvelope<'question_resolved', Record<string, unknown>>
  | WsEnvelope<'update_progress', Record<string, unknown> & { update_id: string }>
  | WsEnvelope<'update_available', Record<string, unknown>>
  | WsEnvelope<'agent_event', Record<string, unknown>>
  | WsEnvelope<'agent_run', AgentRunEvent>
  | WsEnvelope<'ticket_status', TicketStatusEvent>
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
  'question_request',
  'question_resolved',
  'update_progress',
  'update_available',
  'agent_event',
  'agent_run',
  'ticket_status',
] as const

export const REALTIME_CHANNELS = ['log', ...REALTIME_STATE_CHANNELS] as const

export type RealtimeChannel = (typeof REALTIME_CHANNELS)[number]

export interface LogFilter {
  sources: string[]
  min_level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'
  pipeline_id: string | null
}
