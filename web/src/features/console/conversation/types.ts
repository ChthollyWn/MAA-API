import type { AgentMessage } from '@/features/agent/types'

/** 会话属于哪一侧：总控（指挥台）还是某个工单的 Worker 会话。 */
export type ConversationScope = { kind: 'orchestrator' } | { kind: 'ticket'; ticketId: string }

/** 总控收件箱项的类别（`maa_api/domain/enums.py` 的 `InboxKind`）。 */
export type InboxKind = 'user_message' | 'report' | 'thread_event' | 'system' | 'trigger'

/**
 * v3 消息：v2 消息窗口的 `AgentMessage` 加上收件箱与运行的 wire 字段。
 * 四个新字段都可选，所以 v2 消息窗口（旧会话查看器）的数据可以直接传进来。
 */
export interface V3Message extends AgentMessage {
  run_id?: string | null
  inbox_id?: number | null
  inbox_kind?: InboxKind | null
  inbox_thread_key?: string | null
  /** 回溯后被取代的时刻（M80）；非空的消息不进上下文，前端默认折叠。 */
  superseded_at?: string | null
}

/** 确认与提问的发起方（M71 §4.2）；REST 直接发起时为 `null`。 */
export interface Origin {
  ticket_id: string | null
  ticket_title: string | null
  agent: string | null
  run_id: string | null
}

/** 工单上「需要用户处理」的动作（与 03 §6.1 的 `human_action` 同形）。 */
export interface HumanAction {
  type: 'confirmation' | 'question' | 'decision'
  prompt: string
  ref_id: string | null
}

/** 输入栏里待发送的 @ 提及；T7 的 `composer-assist.ts` 会再导出这两个类型。 */
export type MentionType = 'ticket' | 'knowledge' | 'operator' | 'stage' | 'pipeline'

export interface MentionDraft {
  type: MentionType
  id: string
  label: string
}

/** 实时副本的键：总控是 `orchestrator`，工单是 `ticket:<id>`（规格 §4.5.2）。 */
export function conversationScopeKey(scope: ConversationScope): string {
  return scope.kind === 'ticket' ? `ticket:${scope.ticketId}` : 'orchestrator'
}
