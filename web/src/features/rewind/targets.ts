import { isInboxEvent } from '@/features/console/conversation/transcript-v3'
import type { V3Message } from '@/features/console/conversation/types'

/** 会话属于哪一侧：总控（`orchestrator`）还是工单的 Worker（`worker`）。 */
export type MessageKind = 'orchestrator' | 'worker'

/** 一条消息可用的操作（M80 §4.6）；不出现的键表示不提供该动作。 */
export interface MessageActionSet {
  /** 「回到这里」的目标 seq（消息自己的 seq）。 */
  rewindTo?: number
  /** 「重新生成」：先回溯到那条人类消息之前，再重发它的正文。 */
  regenerate?: { toSeq: number; content: string }
  /** 「编辑后重发」：回溯到原消息之前，重发编辑后的正文。 */
  edit?: { toSeq: number; content: string }
  /** 「分叉」：只给工单页，分叉处是这条消息的 seq。 */
  forkAt?: number
}

export interface MessageActionOptions {
  kind: MessageKind
  running: boolean
}

function isSuperseded(message: V3Message): boolean {
  return Boolean(message.superseded_at)
}

/** 人类消息：`role='user'` 且不是收件箱转出的事件（§4.6 的「人类消息」）。 */
function isHumanMessage(message: V3Message): boolean {
  return message.role === 'user' && !isInboxEvent(message)
}

/**
 * 目标表（§4.5.7）：`user` / `system`、没有 `tool_calls` 的 `assistant`，以及同一批
 * tool_call 里 seq 最大的那条 `tool` 结果。已被取代的消息不是合法目标。
 */
function isLegalTarget(messages: readonly V3Message[], index: number): boolean {
  const message = messages[index]
  if (!message || isSuperseded(message)) return false
  if (message.role === 'user' || message.role === 'system') return true
  if (message.role === 'assistant') return !message.tool_calls?.length
  if (message.role !== 'tool' || !message.tool_call_id) return false

  // 往前找发起这次调用的 assistant；找不到（调用在更早的页）就当作批次中间。
  let callIds: Set<string> | null = null
  for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
    const candidate = messages[cursor]
    if (candidate?.role !== 'assistant' || !candidate.tool_calls) continue
    if (candidate.tool_calls.some((call) => call.id === message.tool_call_id)) {
      callIds = new Set(candidate.tool_calls.flatMap((call) => (call.id ? [call.id] : [])))
      break
    }
  }
  if (!callIds) return false
  let lastSeq = message.seq
  for (const candidate of messages) {
    if (
      candidate.role === 'tool'
      && candidate.tool_call_id
      && callIds.has(candidate.tool_call_id)
      && candidate.seq > lastSeq
    ) {
      lastSeq = candidate.seq
    }
  }
  return message.seq === lastSeq
}

/** 目标之前最近一条未被取代消息的 seq；没有时为 -1（只有总控合法）。 */
function seqBefore(
  messages: readonly V3Message[],
  index: number,
  kind: MessageKind,
): number | null {
  for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
    const candidate = messages[cursor]
    if (candidate && !isSuperseded(candidate)) return candidate.seq
  }
  return kind === 'orchestrator' ? -1 : null
}

/** 往前最近的一条人类消息下标；没有返回 -1。 */
function humanBefore(messages: readonly V3Message[], index: number): number {
  for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
    const candidate = messages[cursor]
    if (candidate && isHumanMessage(candidate)) return cursor
  }
  return -1
}

/**
 * 消息上的四个动作（M80 §4.6）。`running` 不改变返回的动作集合——运行中由
 * `MessageActions` 统一禁用并写上「正在运行，停下后再操作」。
 */
export function messageActions(
  messages: readonly V3Message[],
  message: V3Message,
  options: MessageActionOptions,
): MessageActionSet {
  const index = messages.findIndex((candidate) => candidate.id === message.id)
  if (index < 0) return {}
  const actions: MessageActionSet = {}

  if (isLegalTarget(messages, index)) {
    actions.rewindTo = message.seq
    if (options.kind === 'worker') actions.forkAt = message.seq
  }

  if (message.role === 'assistant' && !message.tool_calls?.length && !isSuperseded(message)) {
    const humanIndex = humanBefore(messages, index)
    if (humanIndex >= 0) {
      const toSeq = seqBefore(messages, humanIndex, options.kind)
      if (toSeq !== null) {
        actions.regenerate = { toSeq, content: messages[humanIndex]?.content ?? '' }
      }
    }
  }

  if (isHumanMessage(message) && !isSuperseded(message)) {
    const toSeq = seqBefore(messages, index, options.kind)
    if (toSeq !== null) actions.edit = { toSeq, content: message.content ?? '' }
  }

  return actions
}
