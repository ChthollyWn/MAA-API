import type { AgentQuestion } from '@/features/agent/types'
import type { Confirmation } from '@/features/confirmations/ConfirmationsPanel'
import type { MemoryItem } from '@/features/memory/api'
import type { TicketView } from './api'
import type { Origin } from './conversation/types'

export type AttentionItem =
  | { kind: 'confirmation'; id: string; confirmation: Confirmation; origin: Origin | null }
  | { kind: 'question'; id: string; question: AgentQuestion; origin: Origin | null }
  /** 待审核记忆合成一项，文案「N 条记忆待审核」（M79 规格 §4.5）。 */
  | { kind: 'memory'; id: string; count: number; previews: string[] }
  | { kind: 'ticket'; id: string; ticket: TicketView }

function originOf(value: unknown): Origin | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null
  const raw = value as Record<string, unknown>
  return {
    ticket_id: typeof raw.ticket_id === 'string' ? raw.ticket_id : null,
    ticket_title: typeof raw.ticket_title === 'string' ? raw.ticket_title : null,
    agent: typeof raw.agent === 'string' ? raw.agent : null,
    run_id: typeof raw.run_id === 'string' ? raw.run_id : null,
  }
}

function expiry(value: unknown): number {
  if (typeof value !== 'string') return Number.POSITIVE_INFINITY
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : Number.POSITIVE_INFINITY
}

function updatedAt(value: string | null): number {
  if (!value) return 0
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : 0
}

type SortedItem = { group: number; item: AttentionItem; expires: number; updated: number }

/**
 * 「待你处理」的合并与排序（M72 §4.5.5）。
 *
 * 1. 工单已经被一张确认/提问覆盖（`human_action.ref_id` 命中、或它的 id 就是卡片来源的
 *    `origin.ticket_id`）就不再单列；被 `excludeIds` 排除的卡片仍然参与覆盖计算，
 *    否则实时副本里的卡会让同一张卡在侧栏里再出现一次。
 * 2. 顺序：确认 → 提问 → 待审核记忆 → blocked 工单 → waiting 工单；确认与提问按
 *    过期时间升序（读不出的排最后），工单按更新时间降序。
 */
export function buildAttentionItems(input: {
  confirmations: readonly Confirmation[]
  questions: readonly AgentQuestion[]
  tickets: readonly TicketView[]
  /** 待审核（proposed）的记忆；合成一项入口，不逐条列卡片（M79 §4.5）。 */
  memories?: readonly MemoryItem[]
  excludeIds?: ReadonlySet<string>
}): AttentionItem[] {
  const { confirmations, questions, tickets, memories = [], excludeIds } = input
  const coveredRefs = new Set<string>()
  const coveredTickets = new Set<string>()
  for (const confirmation of confirmations) {
    coveredRefs.add(confirmation.confirmation_id)
    const ticketId = confirmation.origin?.ticket_id
    if (ticketId) coveredTickets.add(ticketId)
  }
  for (const question of questions) {
    coveredRefs.add(question.question_id)
    const ticketId = question.origin?.ticket_id
    if (ticketId) coveredTickets.add(ticketId)
  }

  const sorted: SortedItem[] = []

  for (const confirmation of confirmations) {
    if (excludeIds?.has(confirmation.confirmation_id)) continue
    sorted.push({
      group: 0,
      item: {
        kind: 'confirmation',
        id: confirmation.confirmation_id,
        confirmation,
        origin: originOf(confirmation.origin),
      },
      expires: expiry(confirmation.expires_at),
      updated: 0,
    })
  }

  for (const question of questions) {
    if (excludeIds?.has(question.question_id)) continue
    sorted.push({
      group: 1,
      item: {
        kind: 'question',
        id: question.question_id,
        question,
        origin: originOf(question.origin),
      },
      expires: expiry(question.expires_at),
      updated: 0,
    })
  }

  const pendingMemories = memories.filter((memory) => memory.status === 'proposed')
  if (pendingMemories.length > 0) {
    sorted.push({
      group: 2,
      item: {
        kind: 'memory',
        id: 'memory-proposed',
        count: pendingMemories.length,
        previews: pendingMemories.slice(0, 3).map((memory) => memory.content),
      },
      expires: Number.POSITIVE_INFINITY,
      updated: 0,
    })
  }

  for (const ticket of tickets) {
    if (ticket.status !== 'blocked' && ticket.status !== 'waiting') continue
    if (coveredTickets.has(ticket.id)) continue
    const refId = ticket.humanAction?.ref_id
    if (refId && coveredRefs.has(refId)) continue
    sorted.push({
      group: ticket.status === 'blocked' ? 3 : 4,
      item: { kind: 'ticket', id: ticket.id, ticket },
      expires: Number.POSITIVE_INFINITY,
      updated: updatedAt(ticket.updatedAt),
    })
  }

  sorted.sort((left, right) => {
    if (left.group !== right.group) return left.group - right.group
    if (left.group <= 1) {
      if (left.expires !== right.expires) return left.expires - right.expires
    } else if (left.updated !== right.updated) {
      return right.updated - left.updated
    }
    return left.item.id.localeCompare(right.item.id)
  })

  return sorted.map(({ item }) => item)
}

/**
 * 来源文案（M72 §4.5.5）：工单 → 「来自工单「标题」· 代理」；否则按调用方区分
 * REST 与旧会话。代理名先查定义标题，查不到就用代码名。
 */
export function originLabel(
  origin: Origin | null,
  caller: Confirmation['caller'] | undefined,
  specTitles: ReadonlyMap<string, string>,
): string {
  if (origin?.ticket_id) {
    const agent = origin.agent ?? '代理'
    return `来自工单「${origin.ticket_title ?? '未命名'}」· ${specTitles.get(agent) ?? agent}`
  }
  if (caller?.kind === 'rest') return '来自 REST 调用'
  return '来自旧会话'
}
