export type JsonRecord = Record<string, unknown>

export interface AgentSession {
  session_id: string
  title?: string | null
  status?: string
  model?: string | null
  message_count?: number
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  atomic_grant?: { granted: boolean; expires_at?: string | null; grant_id?: string | null } | null
  created_at?: string | null
  updated_at?: string | null
  last_message_at?: string | null
  incident_key?: string | null
}

export interface AgentToolCall {
  id?: string
  function?: { name?: string; arguments?: string }
}

export interface AgentMessage {
  id: number
  session_id: string
  seq: number
  role: string
  content?: string | null
  tool_calls?: AgentToolCall[] | null
  tool_name?: string | null
  tool_call_id?: string | null
  audit_id?: number | null
  finish_reason?: string | null
  created_at?: string | null
}

export interface AgentConfirmation {
  confirmation_id: string
  action: string
  risk_level: string
  reason: string
  payload: JsonRecord
  status: string
  expires_at: string
  execution?: { status?: string; result?: string | null } | null
}

/** 本轮流式过程中收到的一次工具调用。 */
export interface ToolTrace {
  toolCallId: string
  name: string
  arguments?: unknown
  result?: unknown
  confirmationId?: string
  auditId?: number
}

/** 本轮内容按到达顺序排列：一段回复文字或一次工具调用。 */
export type TurnSegment = { kind: 'text'; text: string } | { kind: 'tool'; toolCallId: string }

/** 正在进行（或刚结束、尚未被历史取代）的一轮对话的实时副本。 */
export interface StreamingTurn {
  userText: string
  assistantText: string
  tools: ToolTrace[]
  segments: TurnSegment[]
  pendingConfirmation: string | null
  running: boolean
  error: string | null
}

export interface CapabilityResult {
  model?: string
  ready?: boolean
  tool_calling?: boolean
  vision?: boolean
  status?: string
  degraded_reason?: string | null
}

export interface PageEnvelope<T> {
  items: T[]
  total: number
  page?: number
  size?: number
}

/** 就地显示的错误：保留错误码，用来判断要不要给出「前往设置」。 */
export interface ShownError {
  message: string
  code?: string
}
