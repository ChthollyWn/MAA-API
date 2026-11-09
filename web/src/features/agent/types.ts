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
  /**
   * 该会话此刻真有一轮在跑（后端进程内状态，M22）。刷新/换设备回来时，本地没有
   * 流式轮次，只能靠它恢复停止入口与「本轮仍在后台执行」的状态行。
   */
  turn_running?: boolean
  atomic_grant?: { granted: boolean; expires_at?: string | null; grant_id?: string | null } | null
  created_at?: string | null
  updated_at?: string | null
  last_message_at?: string | null
  incident_key?: string | null
  /** 会话种类（v3）：`chat`、`incident`、`orchestrator`、`worker`、`subagent` 等。 */
  kind?: string | null
  /** 总控重新开始时旧会话的归档时间；有值即「已归档的总控」。 */
  archived_at?: string | null
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
  /** `stop` / `error` / `question_unanswered` / `interrupted`（M22 起新增，见 specs §4）。 */
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

export interface AgentQuestionOption {
  label: string
  description?: string | null
}

/**
 * 确认与提问的发起方（M71 §4.2）。REST 直接发起的请求没有 origin（`null`），
 * 代理运行发起的才带这四个字段；没有工单的运行两个工单字段为 `null`。
 */
export interface AgentOrigin {
  ticket_id: string | null
  ticket_title: string | null
  agent: string | null
  run_id: string | null
}

/**
 * 提问卡的渲染输入。SSE `question_required` 只带这几个字段（服务端刻意不带
 * session_id/status 等），本会话待答卡拿到的是完整 wire，因此卡片按这个子集取字段，
 * 两种来源都能直接传进来。
 */
export interface AgentQuestionPrompt {
  question_id: string
  question: string
  options: AgentQuestionOption[]
  multiple: boolean
  /** 发起这次提问的代理运行（M71）；REST 直接提问时没有这个字段。 */
  origin?: AgentOrigin | null
}

/** 服务端落库的提问 wire（REST 与 SSE 共用，12 个字段；提问没有有效期）。 */
export interface AgentQuestion extends AgentQuestionPrompt {
  session_id: string
  tool_call_id: string
  status: string
  selection?: string[] | null
  created_at?: string | null
  answered_at?: string | null
  answered_by?: string | null
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
