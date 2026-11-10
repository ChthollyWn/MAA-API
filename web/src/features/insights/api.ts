import { queryOptions, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { components } from '@/types/api'

/**
 * 能力效果看板的查询与评价写入（M99 §4.6）。
 *
 * 所有具体缓存都挂在 `keys.agent.insightsAll()` 前缀外或内，提交评价后统一失效
 * 整片前缀；详情与运行反馈各自再失效一次。
 */

export type MetricDefinitionView = components['schemas']['MetricDefinitionOut']
export type MetricCardView = components['schemas']['MetricCardOut']
export type OverviewView = components['schemas']['OverviewOut']
export type TrendView = components['schemas']['TrendOut']
export type BreakdownView = components['schemas']['BreakdownOut']
export type EvalRunPageView = components['schemas']['EvalRunPage']
export type EvalRunSummaryView = components['schemas']['EvalRunSummaryOut']
export type EvalRunDetailView = components['schemas']['EvalRunDetailOut']
export type FeedbackDigestView = components['schemas']['FeedbackDigestOut']
export type RunFeedbackView = components['schemas']['RunFeedbackOut']
export type FeedbackView = components['schemas']['FeedbackOut']
export type FeedbackInput = components['schemas']['FeedbackIn']
export type MetricId = components['schemas']['MetricId']
export type EvalRunMode = components['schemas']['EvalRunMode']

/** 最近评价一次读 10 条（接口上限 20）；离线评测表展示最近 10 次。 */
export const INSIGHTS_FEEDBACK_LIMIT = 10
export const EVAL_RUNS_PAGE_SIZE = 10

export const insightsCatalogQueryOptions = queryOptions({
  queryKey: keys.agent.insightsCatalog(),
  queryFn: async (): Promise<MetricDefinitionView[]> => {
    const { data, error, response } = await api.GET('/api/agent/insights/catalog')
    if (error || !data) throw toApiError(error, response)
    return data.items
  },
  retry: false,
  staleTime: 60_000,
})

export function insightsOverviewQueryOptions(windowDays: number) {
  return queryOptions({
    queryKey: keys.agent.insightsOverview(windowDays),
    queryFn: async (): Promise<OverviewView> => {
      const { data, error, response } = await api.GET('/api/agent/insights/overview', {
        params: { query: { window_days: windowDays } },
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    retry: false,
  })
}

export function insightTrendQueryOptions(metric: MetricId, windowDays: number) {
  return queryOptions({
    queryKey: keys.agent.insightTrend(metric, windowDays),
    queryFn: async (): Promise<TrendView> => {
      const { data, error, response } = await api.GET('/api/agent/insights/trend', {
        params: { query: { metric, window_days: windowDays } },
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    retry: false,
  })
}

export function insightBreakdownQueryOptions(metric: MetricId, windowDays: number, enabled = true) {
  return queryOptions({
    queryKey: keys.agent.insightBreakdown(metric, windowDays),
    queryFn: async (): Promise<BreakdownView> => {
      const { data, error, response } = await api.GET('/api/agent/insights/breakdown', {
        params: { query: { metric, window_days: windowDays } },
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    enabled,
    retry: false,
  })
}

export interface EvalRunFilters {
  page?: number
  size?: number
  suite?: string | null
  mode?: EvalRunMode | null
}

export function evalRunsQueryOptions(filters: EvalRunFilters = {}) {
  const { page = 1, size = EVAL_RUNS_PAGE_SIZE } = filters
  const query = {
    page,
    size,
    ...(filters.suite ? { suite: filters.suite } : {}),
    ...(filters.mode ? { mode: filters.mode } : {}),
  }
  return queryOptions({
    queryKey: keys.agent.evalRuns(query as unknown as Readonly<Record<string, unknown>>),
    queryFn: async (): Promise<EvalRunPageView> => {
      const { data, error, response } = await api.GET('/api/agent/evals/runs', {
        params: { query },
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    retry: false,
  })
}

export function evalRunQueryOptions(runId: string) {
  return queryOptions({
    queryKey: keys.agent.evalRun(runId),
    queryFn: async (): Promise<EvalRunDetailView> => {
      const { data, error, response } = await api.GET('/api/agent/evals/runs/{run_id}', {
        params: { path: { run_id: runId } },
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    enabled: runId.length > 0,
    retry: false,
  })
}

export function insightsFeedbackQueryOptions(windowDays: number, limit = INSIGHTS_FEEDBACK_LIMIT) {
  // 反馈摘要没有单独的主 key：它挂在整片前缀下，提交评价后随 `insightsAll()` 一起失效。
  return queryOptions({
    queryKey: [...keys.agent.insightsAll(), 'feedback', windowDays, limit] as const,
    queryFn: async (): Promise<FeedbackDigestView> => {
      const { data, error, response } = await api.GET('/api/agent/insights/feedback', {
        params: { query: { window_days: windowDays, limit } },
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    retry: false,
  })
}

export function runFeedbackQueryOptions(runId: string) {
  return queryOptions({
    queryKey: keys.agent.runFeedback(runId),
    queryFn: async (): Promise<RunFeedbackView> => {
      const { data, error, response } = await api.GET('/api/agent/runs/{run_id}/feedback', {
        params: { path: { run_id: runId } },
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    enabled: runId.length > 0,
    retry: false,
  })
}

/**
 * 提交一次运行评价（T11 用）。
 *
 * 乐观写入当前运行的反馈缓存，失败回滚；成功后失效这条反馈与 `insightsAll()`，
 * 让看板的满意度与最近评价重读。
 */
export function usePutRunFeedback(runId: string) {
  const queryClient = useQueryClient()
  const queryKey = keys.agent.runFeedback(runId)

  return useMutation({
    mutationFn: async (input: FeedbackInput): Promise<FeedbackView> => {
      const { data, error, response } = await api.PUT('/api/agent/runs/{run_id}/feedback', {
        params: { path: { run_id: runId } },
        body: input,
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    onMutate: async (input) => {
      await queryClient.cancelQueries({ queryKey })
      const previous = queryClient.getQueryData<RunFeedbackView>(queryKey)
      const now = new Date().toISOString()
      queryClient.setQueryData<RunFeedbackView>(queryKey, {
        item: {
          id: previous?.item?.id ?? 0,
          run_id: runId,
          rating: input.rating,
          reasons: input.reasons ?? [],
          comment: input.comment ?? null,
          created_at: previous?.item?.created_at ?? now,
          updated_at: now,
        },
      })
      return { previous }
    },
    onError: (_error, _input, context) => {
      if (context?.previous) {
        queryClient.setQueryData(queryKey, context.previous)
      } else {
        void queryClient.invalidateQueries({ queryKey })
      }
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey }),
        queryClient.invalidateQueries({ queryKey: keys.agent.insightsAll() }),
      ])
    },
  })
}
