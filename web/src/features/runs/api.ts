import { queryOptions } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { components } from '@/types/api'

export type RunSummary = components['schemas']['RunSummary']
export type RunDetail = components['schemas']['RunDetail']
export type RunPage = components['schemas']['RunPage']
export type RunSpansResponse = components['schemas']['RunSpansResponse']
export type SpanOut = components['schemas']['SpanOut']
export type AgentSpecSummary = components['schemas']['SpecSummary']
export type RunStatus = components['schemas']['AgentRunStatus']

export const RUN_PAGE_SIZE = 20
/** 运行仍在进行时的轮询间隔；结束后不再轮询。 */
export const RUN_POLL_MS = 3_000

export interface RunListFilters {
  root_only?: boolean
  ticket_id?: string
  page?: number
  size?: number
}

/** 「进行中」的两种状态：还在跑或等人处理时数据会继续变。 */
export function isLiveRunStatus(status: string | null | undefined): boolean {
  return status === 'running' || status === 'waiting_human'
}

export function runListQueryOptions(filters: RunListFilters) {
  return queryOptions({
    queryKey: keys.agent.runs.list(filters as unknown as Readonly<Record<string, unknown>>),
    queryFn: async (): Promise<RunPage> => {
      const { data, error, response } = await api.GET('/api/agent/runs', {
        params: { query: filters },
      })
      if (error) throw toApiError(error, response)
      return data as RunPage
    },
    retry: false,
  })
}

export function runDetailQueryOptions(runId: string) {
  return queryOptions({
    queryKey: keys.agent.runs.detail(runId),
    queryFn: async (): Promise<RunDetail> => {
      const { data, error, response } = await api.GET('/api/agent/runs/{run_id}', {
        params: { path: { run_id: runId } },
      })
      if (error) throw toApiError(error, response)
      return data as RunDetail
    },
    enabled: runId.length > 0,
    retry: false,
  })
}

export function runSpansQueryOptions(runId: string) {
  return queryOptions({
    queryKey: keys.agent.runs.spans(runId),
    queryFn: async (): Promise<RunSpansResponse> => {
      const { data, error, response } = await api.GET('/api/agent/runs/{run_id}/spans', {
        params: { path: { run_id: runId } },
      })
      if (error) throw toApiError(error, response)
      return data as RunSpansResponse
    },
    enabled: runId.length > 0,
    retry: false,
  })
}

/** 代理定义列表；运行页与成本页都用它把代理的 `name` 换成 `title`。 */
export const specsQueryOptions = queryOptions({
  queryKey: keys.agent.specs.all(),
  queryFn: async (): Promise<AgentSpecSummary[]> => {
    const { data, error, response } = await api.GET('/api/agent/specs')
    if (error) throw toApiError(error, response)
    return data?.items ?? []
  },
  retry: false,
  staleTime: 60_000,
})
