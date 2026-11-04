import { useMemo } from 'react'
import { infiniteQueryOptions, queryOptions, useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { AgentQuestion, PageEnvelope } from '@/features/agent/types'
import {
  toLeaseView,
  toReportView,
  toRunNode,
  toTicketDetailView,
  toTicketView,
  type LeaseView,
  type ReportView,
  type RunNode,
  type TicketDetailView,
  type TicketStatusValue,
  type TicketView,
} from './format'

/** 工单分页；多值 `status` 直接传数组，openapi-fetch 序列化成重复参数。 */
export const TICKET_PAGE_SIZE = 20
export const REPORT_PAGE_SIZE = 20

export interface SpecSummary {
  name: string
  title: string
  kind: string
  description?: string
  version?: number
}

export interface ModelRolesView {
  items: { role: string; profile: string | null; model: string | null; source: string }[]
}

export interface TicketFilters {
  status?: TicketStatusValue[]
}

function listFilters(filters: TicketFilters) {
  return {
    ...(filters.status ? { status: filters.status } : {}),
    size: TICKET_PAGE_SIZE,
  }
}

/** 工单一页。 */
export async function fetchTicketPage(
  filters: TicketFilters = {},
  page = 1,
  signal?: AbortSignal,
): Promise<PageEnvelope<TicketView>> {
  const { data, error, response } = await api.GET('/api/agent/tickets', {
    params: {
      query: {
        status: filters.status,
        page,
        size: TICKET_PAGE_SIZE,
      },
    },
    signal,
  })
  if (error) throw toApiError(error, response)
  const envelope = data as unknown as PageEnvelope<unknown>
  return {
    items: (envelope.items ?? []).map(toTicketView),
    total: envelope.total ?? 0,
    page: envelope.page ?? page,
    size: envelope.size ?? TICKET_PAGE_SIZE,
  }
}

/** 工单列表的「加载更多」；页码不进 key，追加的页由 `pages` 承载（§4.6.2）。 */
export function ticketListInfiniteQuery(filters: TicketFilters = {}) {
  return infiniteQueryOptions({
    queryKey: keys.agent.tickets.list(listFilters(filters)),
    queryFn: ({ pageParam, signal }) => fetchTicketPage(filters, pageParam, signal),
    initialPageParam: 1,
    getNextPageParam: (last) => {
      const page = last.page ?? 1
      const size = last.size ?? TICKET_PAGE_SIZE
      return page * size < last.total ? page + 1 : undefined
    },
    retry: false,
  })
}

/** 工单详情：10 秒轮询 + WS（§4.6.2）。 */
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

/** 汇报时间线的一页，服务端按时间倒序。 */
export async function fetchTicketReportsPage(
  ticketId: string,
  page = 1,
  signal?: AbortSignal,
): Promise<PageEnvelope<ReportView>> {
  const { data, error, response } = await api.GET('/api/agent/tickets/{ticket_id}/reports', {
    params: { path: { ticket_id: ticketId }, query: { page, size: REPORT_PAGE_SIZE } },
    signal,
  })
  if (error) throw toApiError(error, response)
  const envelope = data as unknown as PageEnvelope<unknown>
  return {
    items: (envelope.items ?? []).map(toReportView),
    total: envelope.total ?? 0,
    page: envelope.page ?? page,
    size: envelope.size ?? REPORT_PAGE_SIZE,
  }
}

export function ticketReportsInfiniteQuery(ticketId: string) {
  return infiniteQueryOptions({
    queryKey: keys.agent.tickets.reports(ticketId),
    queryFn: ({ pageParam, signal }) => fetchTicketReportsPage(ticketId, pageParam, signal),
    initialPageParam: 1,
    getNextPageParam: (last) => {
      const page = last.page ?? 1
      const size = last.size ?? REPORT_PAGE_SIZE
      return page * size < last.total ? page + 1 : undefined
    },
    retry: false,
  })
}

/** 活动树的根运行（`root_only=true`）。 */
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

/** 展开活动树时读一个运行的详情（含 children）；5 秒内不重取。 */
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

/** 设备租约：15 秒轮询 + WS 的 `lease_changed`（§4.6.2）。 */
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

export const modelRolesQuery = queryOptions({
  queryKey: keys.agent.modelRoles(),
  queryFn: async (): Promise<ModelRolesView> => {
    const { data, error, response } = await api.GET('/api/agent/model-roles')
    if (error) throw toApiError(error, response)
    return data as unknown as ModelRolesView
  },
  retry: false,
})

/** 全局待答提问：10 秒轮询 + WS；缓存形状是数组，与指挥台共用。 */
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

export { pendingConfirmationsQuery } from '@/features/confirmations/ConfirmationsPanel'

/** 代理名 → 展示名；工单行的 Worker 中文名与活动树共用。读不到就退回原名。 */
export function useSpecTitles(): ReadonlyMap<string, string> {
  const specs = useQuery(specsQuery)
  return useMemo(
    () => new Map((specs.data ?? []).map((spec) => [spec.name, spec.title])),
    [specs.data],
  )
}
