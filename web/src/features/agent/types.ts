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
 * 提问卡的渲染输入。SSE `question_required` 只带这几个字段（服务端刻意不带
 * session_id/status 等），本会话待答卡拿到的是完整 wire，因此卡片按这个子集取字段，
 * 两种来源都能直接传进来。
 */
export interface AgentQuestionPrompt {
  question_id: string
  question: string
  options: AgentQuestionOption[]
  multiple: boolean
  expires_at: string
}

/** 服务端落库的提问 wire（REST 与 SSE 共用，12 个字段）。 */
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

/** 本轮内容按到达顺序排列：一段回复文字或一次工具调用。 */
export type TurnSegment = { kind: 'text'; text: string } | { kind: 'tool'; toolCallId: string }

/** 正在进行（或刚结束、尚未被历史取代）的一轮对话的实时副本。 */
export interface StreamingTurn {
  /** 这一轮属于哪个会话（M59）：URL 后退/前进切换会话时，别的会话的副本不能继续渲染。 */
  sessionId: string
  userText: string
  assistantText: string
  tools: ToolTrace[]
  segments: TurnSegment[]
  pendingConfirmation: string | null
  /** 本轮正挂着、等用户选择的提问；同一轮不会有第二个（后端是阻塞等待）。 */
  pendingQuestion: AgentQuestionPrompt | null
  running: boolean
  /**
   * 本轮开始前的历史消息 id 上界（M59）。本轮已落库的消息 id 都大于它；实时副本
   * 在屏时，历史里这些消息不再渲染，否则直播中任意一次历史刷新都会让本轮重复。
   * 发送时先记下当时历史窗口的最后一条，服务端 `message_start` 带回用户消息 id 后
   * 收敛成它的前一条（更精确，且不依赖发送瞬间历史是否已加载完）。
   */
  baseMessageId: number
  /**
   * 本轮已被中断（M22）：本地点了「停止」，或收到 SSE `interrupted`（别的标签页/
   * 设备停的）。它不是失败——状态行只说「本轮已停止」，不弹错误。
   */
  interrupted?: boolean
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
