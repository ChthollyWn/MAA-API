import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { useDebouncedValue } from '@/features/account/use-debounced-value'
import { knowledgeListQuery } from '@/features/settings/knowledge/api'
import { statusLabel as pipelineStatusLabel } from '@/features/tasks/pipeline-format'
import type { components } from '@/types/api'
import { STAGE_QUERY, type MentionDraft, type MentionType } from './composer-assist'

type TicketSummary = components['schemas']['TicketSummary']
type AccountOperatorRow = components['schemas']['AccountOperatorRow']

/** 面板里的一行：`label` 是要插入正文的文字，`detail` 是右侧的说明。 */
export interface MentionCandidate extends MentionDraft {
  detail: string
}

/** 筛选函数的输入只取用得到的列，测试不必造整份 API 行。 */
export interface TicketCandidateSource {
  id: string
  title: string
  status: string
}

export interface KnowledgeCandidateSource {
  id: string
  title: string
  enabled: boolean
}

export interface OperatorCandidateSource {
  char_id: string
  name?: string | null
  own: boolean
}

/** `/api/pipelines` 的列表项在后端没有强类型，前端按宽松形状读。 */
export type PipelineRow = Record<string, unknown>

/** 与 `features/tickets/format.ts` 的标签表同口径（T7 先本地使用，T9 有同一张表）。 */
const TICKET_STATUS_LABELS: Record<string, string> = {
  open: '进行中',
  waiting: '等你处理',
  blocked: '受阻',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

function ticketStatusLabel(status: string): string {
  return TICKET_STATUS_LABELS[status] ?? status
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

/** 工单：标题包含查询（不分大小写），`detail` 是状态文案。 */
export function toTicketCandidates(items: readonly TicketCandidateSource[], query: string): MentionCandidate[] {
  const needle = query.trim().toLowerCase()
  return items
    .filter((item) => !needle || item.title.toLowerCase().includes(needle))
    .map((item) => ({
      type: 'ticket',
      id: item.id,
      label: item.title,
      detail: ticketStatusLabel(item.status),
    }))
}

/** 知识：只留启用项，id 或标题包含查询；`detail` 是 id。 */
export function toKnowledgeCandidates(items: readonly KnowledgeCandidateSource[], query: string): MentionCandidate[] {
  const needle = query.trim().toLowerCase()
  return items
    .filter((item) => item.enabled)
    .filter((item) => !needle
      || item.id.toLowerCase().includes(needle)
      || item.title.toLowerCase().includes(needle))
    .map((item) => ({
      type: 'knowledge',
      id: item.id,
      label: item.title,
      detail: item.id,
    }))
}

/** 干员：请求已按名字筛过，这里只把行转成候选；`detail` 是持有 / 未持有。 */
export function toOperatorCandidates(items: readonly OperatorCandidateSource[]): MentionCandidate[] {
  return items.flatMap((item) => {
    const name = item.name?.trim()
    if (!name) return []
    return [{
      type: 'operator' as const,
      id: item.char_id,
      label: name,
      detail: item.own ? '持有' : '未持有',
    }]
  })
}

/** 关卡：不发请求，只在查询符合本地代号形状时给一条候选。 */
export function stageCandidate(query: string): MentionCandidate | null {
  if (!STAGE_QUERY.test(query)) return null
  return { type: 'stage', id: query, label: query, detail: '关卡' }
}

/** 流水线：标题或 id 前 8 位包含查询；没有标题时用 `流水线 {id 前 8 位}`。 */
export function toPipelineCandidates(items: readonly PipelineRow[], query: string): MentionCandidate[] {
  const needle = query.trim().toLowerCase()
  return items.flatMap((row) => {
    const id = text(row.id)
    if (!id) return []
    const title = text(row.title).trim()
    const idPrefix = id.slice(0, 8)
    if (needle && !title.toLowerCase().includes(needle) && !idPrefix.toLowerCase().includes(needle)) {
      return []
    }
    return [{
      type: 'pipeline' as const,
      id,
      label: title || `流水线 ${idPrefix}`,
      detail: pipelineStatusLabel(text(row.status)),
    }]
  })
}

async function fetchTickets(): Promise<TicketSummary[]> {
  const { data, error, response } = await api.GET('/api/agent/tickets', {
    params: { query: { page: 1, size: 20 } },
  })
  if (error || !data) throw toApiError(error, response)
  return data.items
}

async function fetchPipelines(): Promise<PipelineRow[]> {
  const { data, error, response } = await api.GET('/api/pipelines', {
    params: { query: { page: 1, size: 10 } },
  })
  if (error || !data) throw toApiError(error, response)
  const items = (data as { items?: unknown }).items
  return Array.isArray(items) ? items.filter(isRecord) : []
}

async function fetchOperators(query: string): Promise<AccountOperatorRow[]> {
  const { data, error, response } = await api.GET('/api/account/operators', {
    params: { query: { query, owned_only: false, limit: 8 } },
  })
  if (error || !data) throw toApiError(error, response)
  return data.items
}

export interface MentionCandidates {
  items: MentionCandidate[]
  isPending: boolean
  error: unknown
}

/**
 * 一类提及的候选（M72 §4.5.6）：
 *
 * - 工单、知识、流水线：进面板时各读一页并在本地筛选，只使用与自己类型相同的查询；
 * - 干员：服务端按名字筛，查询防抖 200 ms，查询为空不发请求；
 * - 关卡：完全本地，不发请求。
 */
export function useMentionCandidates(type: MentionType, query: string, enabled: boolean): MentionCandidates {
  const debouncedQuery = useDebouncedValue(query, 200)
  const operatorQuery = debouncedQuery.trim()

  const tickets = useQuery({
    queryKey: keys.agent.tickets.list({ page: 1, size: 20 }),
    queryFn: fetchTickets,
    enabled: enabled && type === 'ticket',
  })
  const knowledge = useQuery({
    ...knowledgeListQuery,
    enabled: enabled && type === 'knowledge',
  })
  const operators = useQuery({
    queryKey: keys.agent.mentionCandidates('operator', operatorQuery),
    queryFn: () => fetchOperators(operatorQuery),
    enabled: enabled && type === 'operator' && operatorQuery.length > 0,
  })
  const pipelines = useQuery({
    queryKey: keys.pipelines.list({ page: 1, size: 10 }),
    queryFn: fetchPipelines,
    enabled: enabled && type === 'pipeline',
  })

  switch (type) {
    case 'ticket':
      return {
        items: tickets.data ? toTicketCandidates(tickets.data, query) : [],
        isPending: tickets.isPending,
        error: tickets.error,
      }
    case 'knowledge':
      return {
        items: knowledge.data ? toKnowledgeCandidates(knowledge.data, query) : [],
        isPending: knowledge.isPending,
        error: knowledge.error,
      }
    case 'operator':
      return {
        items: operators.data ? toOperatorCandidates(operators.data) : [],
        isPending: operatorQuery.length > 0 && operators.isPending,
        error: operators.error,
      }
    case 'stage': {
      const candidate = stageCandidate(query)
      return { items: candidate ? [candidate] : [], isPending: false, error: null }
    }
    case 'pipeline':
      return {
        items: pipelines.data ? toPipelineCandidates(pipelines.data, query) : [],
        isPending: pipelines.isPending,
        error: pipelines.error,
      }
  }
}
