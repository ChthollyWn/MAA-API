import { isRecord, stringValue } from '@/features/agent/format'
import { buildTranscript, type ToolView, type TranscriptEntry } from '@/features/agent/transcript'
import { toHumanAction } from './sse'
import { splitReferenceBlock } from './references'
import type { HumanAction, V3Message } from './types'

export type EventKind = 'report' | 'thread_event' | 'system' | 'trigger'

export interface EventLink {
  to: string
  label: string
}

export type ConversationEntry =
  | { kind: 'user'; key: string; message: V3Message; text: string; references: string[] }
  | { kind: 'event'; key: string; message: V3Message; eventKind: EventKind; body: string; link: EventLink | null }
  | { kind: 'assistant'; key: string; message: V3Message }
  | { kind: 'tool'; key: string; tool: ToolView; subRunId: string | null }
  | { kind: 'report'; key: string; status: string; summary: string; humanAction: HumanAction | null }

const EVENT_KINDS: ReadonlySet<string> = new Set(['report', 'thread_event', 'system', 'trigger'])

/** 收件箱转出的消息（不是用户本人说的话）：有 `inbox_id` 且不是 `user_message`。 */
export function isInboxEvent(message: Pick<V3Message, 'inbox_id' | 'inbox_kind'>): boolean {
  return message.inbox_id != null && message.inbox_kind !== 'user_message'
}

const EVENT_PREFIXES: ReadonlyArray<[string, EventKind]> = [
  ['【工单汇报】', 'report'],
  ['【事件】', 'thread_event'],
  ['【系统】', 'system'],
]

function eventKindOf(message: V3Message): EventKind {
  const kind = message.inbox_kind
  if (kind && EVENT_KINDS.has(kind)) return kind as EventKind
  const content = message.content ?? ''
  const match = EVENT_PREFIXES.find(([prefix]) => content.startsWith(prefix))
  return match?.[1] ?? 'system'
}

/** 去掉事件正文的前缀与首尾空白；认不出的前缀原样保留（只 trim）。 */
function eventBody(message: V3Message): string {
  const content = message.content ?? ''
  const match = EVENT_PREFIXES.find(([prefix]) => content.startsWith(prefix))
  return (match ? content.slice(match[0].length) : content).trim()
}

/** 事件卡上的跳转（§4.5.4）：当前工单自己不出「打开工单」。 */
function eventLink(message: V3Message, currentTicketId?: string): EventLink | null {
  const key = message.inbox_thread_key ?? ''
  if (key.startsWith('ticket:')) {
    const ticketId = key.slice('ticket:'.length)
    if (!ticketId || ticketId === currentTicketId) return null
    return { to: `/tickets/${ticketId}`, label: '打开工单' }
  }
  if (key.startsWith('pipeline:')) {
    const pipelineId = key.slice('pipeline:'.length)
    if (!pipelineId) return null
    return { to: `/tasks/history/${pipelineId}`, label: '查看流水线' }
  }
  return null
}

function toEntry(entry: TranscriptEntry, currentTicketId?: string): ConversationEntry {
  if (entry.kind === 'user') {
    const message = entry.message as V3Message
    if (isInboxEvent(message)) {
      return {
        kind: 'event',
        key: entry.key,
        message,
        eventKind: eventKindOf(message),
        body: eventBody(message),
        link: eventLink(message, currentTicketId),
      }
    }
    const { text, references } = splitReferenceBlock(message.content ?? '')
    return { kind: 'user', key: entry.key, message, text, references }
  }
  if (entry.kind === 'assistant') {
    return { kind: 'assistant', key: entry.key, message: entry.message as V3Message }
  }

  const tool = entry.tool
  if (tool.name === 'report') {
    const args = isRecord(tool.arguments) ? tool.arguments : {}
    return {
      kind: 'report',
      key: entry.key,
      status: stringValue(args.status),
      summary: stringValue(args.summary),
      humanAction: toHumanAction(args.human_action),
    }
  }
  let subRunId: string | null = null
  if (tool.name === 'delegate' && isRecord(tool.result) && isRecord(tool.result.result)) {
    const runId = tool.result.result.run_id
    if (typeof runId === 'string') subRunId = runId
  }
  return { kind: 'tool', key: entry.key, tool, subRunId }
}

/**
 * 把 v3 消息排成会话条目（§4.5.4）。
 *
 * `mainSessionId` 之外的消息（子代理会话）按 `run_id` 收进 `subRuns`，不进主时间线；
 * 其余消息先过 v2 `buildTranscript` 的调用/结果配对，再按收件箱、引用段、汇报与
 * 委派改写。工具条目里的 `subRunId` 对应的消息由调用方从 `subRuns` 里取。
 */
export function buildConversationEntries(
  messages: readonly V3Message[],
  options: { mainSessionId: string | null; currentTicketId?: string },
): { entries: ConversationEntry[]; subRuns: Map<string, V3Message[]> } {
  const { mainSessionId, currentTicketId } = options
  const subRuns = new Map<string, V3Message[]>()
  const main: V3Message[] = []
  for (const message of messages) {
    if (mainSessionId && message.session_id !== mainSessionId) {
      const runId = message.run_id ?? ''
      const group = subRuns.get(runId)
      if (group) group.push(message)
      else subRuns.set(runId, [message])
      continue
    }
    main.push(message)
  }
  return {
    entries: buildTranscript(main).map((entry) => toEntry(entry, currentTicketId)),
    subRuns,
  }
}
