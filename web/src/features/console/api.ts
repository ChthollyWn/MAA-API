import { useMemo } from 'react'
import { infiniteQueryOptions, queryOptions, useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { AgentQuestion, PageEnvelope } from '@/features/agent/types'
import { MESSAGE_PAGE_SIZE } from '@/features/agent/messages'
import type { HumanAction, V3Message } from './conversation/types'

export { pendingConfirmationsQuery } from '@/features/confirmations/ConfirmationsPanel'

/** 工单状态（与 03 §6.1 同口径）；T9 的 `tickets/format.ts` 会有同一张表。 */
export type TicketStatusValue = 'open' | 'waiting' | 'blocked' | 'done' | 'failed' | 'cancelled'

export const TICKET_STATUS_LABELS: Record<TicketStatusValue, string> = {
  open: '进行中',
  waiting: '等你处理',
  blocked: '受阻',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

/** 指挥台与工单页共用的工单摘要视图；wire 字段名照 03 §6.1。 */
export interface TicketView {
  id: string
  title: string
  kind: string
  status: TicketStatusValue
  origin: string
  summary: string | null
  lastReportStatus: string | null
  humanAction: HumanAction | null
  createdAt: string | null
  updatedAt: string | null
  endedAt: string | null
}

export interface ReportView {
  id: number
  status: string
  summary: string
  humanAction: HumanAction | null
  details: Record<string, unknown> | null
  createdAt: string | null
}

export interface TicketDetailView {
  ticket: TicketView
  mandate: Record<string, unknown> | null
  reports: ReportView[]
  workerSessionId: string | null
  turnRunning: boolean
}

export interface LeaseView {
  held: boolean
  ticketId: string | null
  holderAgent: string
  purpose: string
  expiresAt: string | null
  waiters: number
}

export interface RunNode {
  runId: string
  agent: string
  kind: string
  status: string
  finishReason: string | null
  steps: number
  promptTokens: number
  cachedTokens: number
  completionTokens: number
  costMicros: number
  currency: string | null
  startedAt: string | null
  finishedAt: string | null
  children: RunNode[]
}

export interface OrchestratorState {
  session_id: string
  status: string
  turn_running: boolean
  pending_inbox: number
  archived_count: number
}

export interface SpecSummary {
  name: string
  title: string
  kind: string
  description?: string
  version?: number
  spec_version?: string
  model_role?: string
  tools?: string[]
  delegates?: string[]
}

export interface ModelRolesView {
  items: { role: string; profile: string | null; model: string | null; source: string }[]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function stringValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function nullableString(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function toHumanAction(value: unknown): HumanAction | null {
  if (!isRecord(value)) return null
  const type = stringValue(value.type)
  if (type !== 'confirmation' && type !== 'question' && type !== 'decision') return null
  return { type, prompt: stringValue(value.prompt), ref_id: nullableString(value.ref_id) }
}

export function toTicketView(raw: unknown): TicketView {
  const row = isRecord(raw) ? raw : {}
  const id = stringValue(row.id) || stringValue(row.ticket_id)
  const status = stringValue(row.status) as TicketStatusValue
  return {
    id,
    title: stringValue(row.title),
    kind: stringValue(row.kind),
    status,
    origin: stringValue(row.origin),
    summary: nullableString(row.summary),
    lastReportStatus: nullableString(row.last_report_status),
    humanAction: toHumanAction(row.human_action),
    createdAt: nullableString(row.created_at),
    updatedAt: nullableString(row.updated_at),
    endedAt: nullableString(row.ended_at),
  }
}

export function toReportView(raw: unknown): ReportView {
  const row = isRecord(raw) ? raw : {}
  return {
    id: typeof row.id === 'number' ? row.id : 0,
    status: stringValue(row.status),
    summary: stringValue(row.summary),
    humanAction: toHumanAction(row.human_action),
    details: isRecord(row.details) ? row.details : null,
    createdAt: nullableString(row.created_at),
  }
}

export function toTicketDetailView(raw: unknown): TicketDetailView {
  const row = isRecord(raw) ? raw : {}
  const reports = Array.isArray(row.reports) ? row.reports : Array.isArray(row.recent_reports) ? row.recent_reports : []
  return {
    ticket: toTicketView(row.ticket),
    mandate: isRecord(row.mandate) ? row.mandate : null,
    reports: reports.map(toReportView),
    workerSessionId: nullableString(row.worker_session_id),
    turnRunning: row.turn_running === true,
  }
}

export function toLeaseView(raw: unknown): LeaseView {
  const row = isRecord(raw) ? raw : {}
  const lease = isRecord(row.lease) ? row.lease : null
  return {
    held: row.held === true,
    ticketId: lease ? nullableString(lease.ticket_id) : null,
    holderAgent: lease ? stringValue(lease.holder_agent) : '',
    purpose: lease ? stringValue(lease.purpose) : '',
    expiresAt: lease ? nullableString(lease.expires_at) : null,
    waiters: typeof row.waiters === 'number' ? row.waiters : 0,
  }
}

export function toRunNode(raw: unknown): RunNode {
  const row = isRecord(raw) ? raw : {}
  return {
    runId: stringValue(row.run_id) || stringValue(row.id),
    agent: stringValue(row.agent_name) || stringValue(row.agent),
    kind: stringValue(row.kind),
    status: stringValue(row.status),
    finishReason: nullableString(row.finish_reason),
    steps: typeof row.steps === 'number' ? row.steps : 0,
    promptTokens: typeof row.prompt_tokens === 'number' ? row.prompt_tokens : 0,
    cachedTokens: typeof row.cached_tokens === 'number' ? row.cached_tokens : 0,
    completionTokens: typeof row.completion_tokens === 'number' ? row.completion_tokens : 0,
    costMicros: typeof row.cost_micros === 'number' ? row.cost_micros : 0,
    currency: nullableString(row.currency),
    startedAt: nullableString(row.started_at),
    finishedAt: nullableString(row.finished_at),
    children: Array.isArray(row.children) ? row.children.map(toRunNode) : [],
  }
}

async function fetchOrchestratorState(): Promise<OrchestratorState> {
  const { data, error, response } = await api.GET('/api/agent/orchestrator')
  if (error) throw toApiError(error, response)
  return data as unknown as OrchestratorState
}

/** 总控状态：10 秒轮询 + WS（§4.6.2）。页面用它恢复 `turn_running` 的停止入口。 */
export const orchestratorStateQuery = queryOptions({
  queryKey: keys.agent.orchestrator.state(),
  queryFn: fetchOrchestratorState,
  refetchInterval: 10_000,
  retry: false,
})

/**
 * 总控消息的一页。消息窗口本身由 G6 的 `useConversation` 用
 * `loadMessageWindow` 管理（同一个 key，不能在这里再写一份同 key 的 queryOptions）。
 */
export async function fetchOrchestratorMessagePage(
  page: number,
  size = MESSAGE_PAGE_SIZE,
  signal?: AbortSignal,
): Promise<PageEnvelope<V3Message>> {
  const { data, error, response } = await api.GET('/api/agent/orchestrator/messages', {
    params: { query: { page, size } },
    signal,
  })
  if (error) throw toApiError(error, response)
  return data as unknown as PageEnvelope<V3Message>
}

export interface TicketFilters {
  status?: TicketStatusValue[]
  page?: number
  size?: number
}

/** 工单一页。多值 `status` 直接传数组，openapi-fetch 会序列化成重复参数。 */
export async function fetchTicketPage(
  filters: TicketFilters = {},
  signal?: AbortSignal,
): Promise<PageEnvelope<TicketView>> {
  const page = filters.page ?? 1
  const size = filters.size ?? 20
  const { data, error, response } = await api.GET('/api/agent/tickets', {
    params: { query: { status: filters.status, page, size } },
    signal,
  })
  if (error) throw toApiError(error, response)
  const envelope = data as unknown as PageEnvelope<unknown>
  return {
    items: (envelope.items ?? []).map(toTicketView),
    total: envelope.total ?? 0,
    page: envelope.page ?? page,
    size: envelope.size ?? size,
  }
}

export function ticketListQuery(filters: TicketFilters = {}) {
  return queryOptions({
    queryKey: keys.agent.tickets.list(filters as Record<string, unknown>),
    queryFn: ({ signal }) => fetchTicketPage(filters, signal),
    retry: false,
  })
}

/** 工单列表的「加载更多」；页码不进 key，追加的页由 `pages` 承载。 */
export function ticketListInfiniteQuery(filters: TicketFilters = {}) {
  return infiniteQueryOptions({
    queryKey: keys.agent.tickets.list(filters as Record<string, unknown>),
    queryFn: ({ pageParam, signal }) => fetchTicketPage({ ...filters, page: pageParam }, signal),
    initialPageParam: 1,
    getNextPageParam: (last) => {
      const page = last.page ?? 1
      const size = last.size ?? filters.size ?? 20
      return page * size < last.total ? page + 1 : undefined
    },
    retry: false,
  })
}

/** 进行中的工单：侧栏与手机工具条共用；WS 经 `tickets.lists()` 失效。 */
export const activeTicketsQuery = queryOptions({
  queryKey: keys.agent.tickets.list({ status: ['open'], page: 1, size: 20 }),
  queryFn: () => fetchTicketPage({ status: ['open'], page: 1, size: 20 }),
  retry: false,
})

/** 「待你处理」里的 waiting / blocked 工单。 */
export const attentionTicketsQuery = queryOptions({
  queryKey: keys.agent.tickets.list({ status: ['waiting', 'blocked'], page: 1, size: 50 }),
  queryFn: () => fetchTicketPage({ status: ['waiting', 'blocked'], page: 1, size: 50 }),
  retry: false,
})

/** 历史已失效提问：工单卡回填选项用（维护性补丁 questions-no-expiry §4.6）。 */
export const expiredQuestionsQuery = queryOptions({
  queryKey: keys.agent.expiredQuestions(),
  queryFn: async (): Promise<AgentQuestion[]> => {
    const { data, error, response } = await api.GET('/api/agent/questions', {
      params: { query: { status: 'expired', page: 1, size: 50 } },
    })
    if (error) throw toApiError(error, response)
    return (data as unknown as PageEnvelope<AgentQuestion>).items ?? []
  },
  staleTime: 60_000,
  retry: false,
})

/** 全局待答提问：10 秒轮询 + WS；缓存形状是数组，`useResolveQuestion` 直接摘项。 */
export const attentionQuestionsQuery = queryOptions({
  queryKey: keys.agent.attentionQuestions(),
  queryFn: async (): Promise<AgentQuestion[]> => {
    const { data, error, response } = await api.GET('/api/agent/questions', {
      params: { query: { status: 'pending', page: 1, size: 50 } },
    })
    if (error) throw toApiError(error, response)
    return (data as unknown as PageEnvelope<AgentQuestion>).items ?? []
  },
  refetchInterval: 10_000,
  retry: false,
})

export function ticketDetailQuery(ticketId: string) {
  return queryOptions({
    queryKey: keys.agent.tickets.detail(ticketId),
    queryFn: async (): Promise<TicketDetailView> => {
      const { data, error, response } = await api.GET('/api/agent/tickets/{ticket_id}', {
        params: { path: { ticket_id: ticketId } },
      })
      if (error) throw toApiError(error, response)
      return toTicketDetailView(data)
    },
    refetchInterval: 10_000,
    retry: false,
  })
}

/** 工单消息的一页（含子代理）。窗口由 `useConversation(ticketEndpoints(...))` 管理。 */
export async function fetchTicketMessagePage(
  ticketId: string,
  page: number,
  size = MESSAGE_PAGE_SIZE,
  signal?: AbortSignal,
): Promise<PageEnvelope<V3Message>> {
  const { data, error, response } = await api.GET('/api/agent/tickets/{ticket_id}/messages', {
    params: {
      path: { ticket_id: ticketId },
      query: { include_subagents: true, page, size },
    },
    signal,
  })
  if (error) throw toApiError(error, response)
  return data as unknown as PageEnvelope<V3Message>
}

/** 工单汇报的一页，时间倒序。 */
export async function fetchTicketReportsPage(
  ticketId: string,
  page = 1,
  size = 20,
  signal?: AbortSignal,
): Promise<PageEnvelope<ReportView>> {
  const { data, error, response } = await api.GET('/api/agent/tickets/{ticket_id}/reports', {
    params: { path: { ticket_id: ticketId }, query: { page, size } },
    signal,
  })
  if (error) throw toApiError(error, response)
  const envelope = data as unknown as PageEnvelope<unknown>
  return {
    items: (envelope.items ?? []).map(toReportView),
    total: envelope.total ?? 0,
    page: envelope.page ?? page,
    size: envelope.size ?? size,
  }
}

export function ticketReportsQuery(ticketId: string) {
  return queryOptions({
    queryKey: keys.agent.tickets.reports(ticketId),
    queryFn: ({ signal }) => fetchTicketReportsPage(ticketId, 1, 20, signal),
    retry: false,
  })
}

/** 工单的根运行列表（活动树的第一层）。 */
export const ticketRunsQuery = (ticketId: string) => queryOptions({
  queryKey: keys.agent.tickets.runs(ticketId),
  queryFn: async (): Promise<RunNode[]> => {
    const { data, error, response } = await api.GET('/api/agent/runs', {
      params: { query: { ticket_id: ticketId, root_only: true, page: 1, size: 50 } },
    })
    if (error) throw toApiError(error, response)
    return ((data as unknown as PageEnvelope<unknown>).items ?? []).map(toRunNode)
  },
  retry: false,
})

/** 展开活动树时读一个运行的详情；5 秒内不重取。 */
export const runDetailQuery = (runId: string) => queryOptions({
  queryKey: keys.agent.runs.detail(runId),
  queryFn: async (): Promise<RunNode> => {
    const { data, error, response } = await api.GET('/api/agent/runs/{run_id}', {
      params: { path: { run_id: runId } },
    })
    if (error) throw toApiError(error, response)
    return toRunNode(data)
  },
  staleTime: 5_000,
  retry: false,
})

/** 代理定义列表：定义随服务版本发布，读一次就不再重取。 */
export const specsQuery = queryOptions({
  queryKey: keys.agent.specs.all(),
  queryFn: async (): Promise<SpecSummary[]> => {
    const { data, error, response } = await api.GET('/api/agent/specs')
    if (error) throw toApiError(error, response)
    return (data as unknown as { items?: SpecSummary[] }).items ?? []
  },
  staleTime: Infinity,
  retry: false,
})

export const specDetailQuery = (name: string) => queryOptions({
  queryKey: keys.agent.specs.detail(name),
  queryFn: async (): Promise<SpecSummary> => {
    const { data, error, response } = await api.GET('/api/agent/specs/{name}', {
      params: { path: { name } },
    })
    if (error) throw toApiError(error, response)
    return data as unknown as SpecSummary
  },
  staleTime: Infinity,
  retry: false,
})

/** 设备租约：15 秒轮询 + WS 的 `lease_changed`。 */
export const deviceLeaseQuery = queryOptions({
  queryKey: keys.agent.deviceLease(),
  queryFn: async (): Promise<LeaseView> => {
    const { data, error, response } = await api.GET('/api/agent/device-lease')
    if (error) throw toApiError(error, response)
    return toLeaseView(data)
  },
  refetchInterval: 15_000,
  retry: false,
})

export const modelRolesQuery = queryOptions({
  queryKey: keys.agent.modelRoles(),
  queryFn: async (): Promise<ModelRolesView> => {
    const { data, error, response } = await api.GET('/api/agent/model-roles')
    if (error) throw toApiError(error, response)
    return data as unknown as ModelRolesView
  },
  retry: false,
})

/** 代理名 → 展示名；来源行与工单行共用。读不到就退回原名。 */
export function useSpecTitles(): ReadonlyMap<string, string> {
  const specs = useQuery(specsQuery)
  return useMemo(
    () => new Map((specs.data ?? []).map((spec) => [spec.name, spec.title])),
    [specs.data],
  )
}
