import type { AgentQuestion } from '@/features/agent/types'
import type { Confirmation } from '@/features/confirmations/ConfirmationsPanel'
import type { MemoryItem } from '@/features/memory/api'
import type { SpendKindUsage, SpendSummary } from '@/features/spend/api'
import type { PlanView } from '@/features/tickets/workspace-api'
import type { AlertOut } from '@/features/watchdog/api'
import type { TicketView } from './api'
import type { Origin } from './conversation/types'
import type { PlanAttentionView } from './use-attention-plans'

export type AttentionItem =
  | { kind: 'confirmation'; id: string; confirmation: Confirmation; origin: Origin | null }
  | { kind: 'question'; id: string; question: AgentQuestion; origin: Origin | null }
  /** 待批准的计划（spec `console-plan-approval` §4.1）：指挥台就地批准/退回。 */
  | { kind: 'plan'; id: string; ticket: TicketView; plan: PlanView; workerSessionId: string }
  /** 待审核记忆合成一项，文案「N 条记忆待审核」（M79 规格 §4.5）。 */
  | { kind: 'memory'; id: string; count: number; previews: string[] }
  /** 未解除的严重值守告警（M83 §4.5）；动作是「标记已处理」。 */
  | { kind: 'alert'; id: string; alert: AlertOut }
  /** 消耗护栏的汇总行（M84 §4.6）：有拦截或某一种用量接近上限时出现。 */
  | { kind: 'spend'; id: string; attention: SpendAttention }
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

/** 消耗护栏进入「待你处理」的两种情形（M84 §4.6）：有拦截，或用量接近上限。 */
export type SpendAttention =
  | { kind: 'blocked'; count: number }
  | { kind: 'usage'; usage: SpendKindUsage }

/** 用量达到上限八成（向上取整）就算「接近上限」；与页面进度条的 warning 同口径。 */
export const SPEND_ATTENTION_RATIO = 0.8

/** 判定是否该出现消耗汇总行；不该出现时返回 null。 */
export function spendAttention(summary: SpendSummary | undefined): SpendAttention | null {
  if (!summary) return null
  // 有拦截时优先说拦截次数，这是用户最需要知道的一件事。
  if (summary.blocked_today.length > 0) {
    return { kind: 'blocked', count: summary.blocked_today.length }
  }
  let highest: SpendKindUsage | null = null
  for (const item of summary.today) {
    if (item.limit <= 0) continue
    if (item.used < Math.ceil(item.limit * SPEND_ATTENTION_RATIO)) continue
    // 并列时保留接口顺序里的前一种（理智药 → 源石 → 加急许可）。
    if (!highest || item.used / item.limit > highest.used / highest.limit) highest = item
  }
  return highest ? { kind: 'usage', usage: highest } : null
}

/** 汇总行文案：有拦截说次数，否则说比例最高的一种用了多少。 */
export function spendAttentionText(attention: SpendAttention): string {
  if (attention.kind === 'blocked') return `今天有 ${attention.count} 次提交被消耗上限拦下`
  return `${attention.usage.label}今天已用 ${attention.usage.used} / ${attention.usage.limit}`
}

/**
 * 「待你处理」的合并与排序（M72 §4.5.5 + 计划批准补丁）。
 *
 * 1. 工单已经被一张确认/提问覆盖（`human_action.ref_id` 命中、或它的 id 就是卡片来源的
 *    `origin.ticket_id`）就不再单列；被 `excludeIds` 排除的卡片仍然参与覆盖计算，
 *    否则实时副本里的卡会让同一张卡在侧栏里再出现一次。
 * 2. 待批准计划（`plans`）压掉同一工单的工单卡；计划刚批准/退回、Worker 还没清
 *    `human_action` 的工单由 `suppressedTicketIds` 暂时压掉，避免出现点了就 409 的死卡。
 * 3. 顺序：确认 → 提问 → 待审核记忆 → 严重告警 → blocked 工单 → 计划待批准 →
 *    waiting 工单 → 消耗汇总；确认与提问按过期时间升序（读不出的排最后），
 *    告警、计划与工单按更新时间降序，同一时刻按 id 兜底。
 *    消耗汇总排在最后：规格 §4.6 要求它跟在观察记录汇总行之后，而观察记录汇总行
 *    目前还没有接入「待你处理」。
 */
export function buildAttentionItems(input: {
  confirmations: readonly Confirmation[]
  questions: readonly AgentQuestion[]
  tickets: readonly TicketView[]
  /** 待批准计划（spec `console-plan-approval` §4.1）；与工单卡互斥。 */
  plans?: readonly PlanAttentionView[]
  /** 计划已不再待批准、但工单 `human_action` 还没清掉的工单 id。 */
  suppressedTicketIds?: ReadonlySet<string>
  /** 待审核（proposed）的记忆；合成一项入口，不逐条列卡片（M79 §4.5）。 */
  memories?: readonly MemoryItem[]
  /** 严重且未解除的值守告警（M83 §4.5）；调用方已按接口过滤，这里再兜一层。 */
  alerts?: readonly AlertOut[]
  /** 消耗汇总（M84 §4.6）；进入条件见 `spendAttention`。 */
  spend?: SpendSummary
  excludeIds?: ReadonlySet<string>
}): AttentionItem[] {
  const {
    confirmations,
    questions,
    tickets,
    plans = [],
    suppressedTicketIds,
    memories = [],
    alerts = [],
    spend,
    excludeIds,
  } = input
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
  const plannedTickets = new Set(plans.map((entry) => entry.ticket.id))

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

  for (const alert of alerts) {
    if (alert.severity !== 'critical' || alert.resolved_at !== null) continue
    sorted.push({
      group: 3,
      item: { kind: 'alert', id: String(alert.id), alert },
      expires: Number.POSITIVE_INFINITY,
      updated: updatedAt(alert.created_at),
    })
  }

  for (const entry of plans) {
    sorted.push({
      group: 5,
      item: {
        kind: 'plan',
        id: `plan-${entry.plan.id}`,
        ticket: entry.ticket,
        plan: entry.plan,
        workerSessionId: entry.workerSessionId,
      },
      expires: Number.POSITIVE_INFINITY,
      updated: updatedAt(entry.ticket.updatedAt),
    })
  }

  for (const ticket of tickets) {
    if (ticket.status !== 'blocked' && ticket.status !== 'waiting') continue
    if (coveredTickets.has(ticket.id)) continue
    if (plannedTickets.has(ticket.id)) continue
    if (suppressedTicketIds?.has(ticket.id)) continue
    const refId = ticket.humanAction?.ref_id
    if (refId && coveredRefs.has(refId)) continue
    sorted.push({
      group: ticket.status === 'blocked' ? 4 : 6,
      item: { kind: 'ticket', id: ticket.id, ticket },
      expires: Number.POSITIVE_INFINITY,
      updated: updatedAt(ticket.updatedAt),
    })
  }

  const spendEntry = spendAttention(spend)
  if (spendEntry) {
    sorted.push({
      group: 7,
      item: { kind: 'spend', id: 'spend-today', attention: spendEntry },
      expires: Number.POSITIVE_INFINITY,
      updated: 0,
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
