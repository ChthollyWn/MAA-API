import { queryOptions, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { components } from '@/types/api'

/**
 * 检索来源与测试检索（M76 规格 §4.6）。
 *
 * 来源列表带全局向量状态：任一来源或向量服务在 `indexing` 时每 2 秒轮询一次，
 * 其余时候不轮询。测试检索只在点「检索」时发（`enabled: false` + `refetch()`），
 * 查询参数进 key，新词不会复用旧结果。
 */
export type RetrievalSource = components['schemas']['RetrievalSourceView']
export type RetrievalSourceList = components['schemas']['RetrievalSourceList']
export type RetrievalSourceUpdate = components['schemas']['RetrievalSourceUpdate']
export type EmbeddingStatus = components['schemas']['EmbeddingStatusView']
export type RetrievalResults = components['schemas']['RetrievalResult']
export type RetrievalHit = components['schemas']['RetrievalHit']

/** 轮询间隔：任意来源或向量服务正在建索引时使用（规格 §4.6）。 */
export const RETRIEVAL_POLL_MS = 2000

export const retrievalSourcesKey = [...keys.agent.all(), 'retrieval', 'sources'] as const

export interface RetrievalSearchParams {
  q: string
  sources?: string[]
  top_k?: number
}

export function retrievalSearchKey(params: RetrievalSearchParams) {
  return [...keys.agent.all(), 'retrieval', 'search', params] as const
}

function isIndexing(list: RetrievalSourceList | undefined): boolean {
  if (!list) return false
  return list.embedding.status === 'indexing' || list.items.some((source) => source.status === 'indexing')
}

export const retrievalSourcesQuery = queryOptions({
  queryKey: retrievalSourcesKey,
  queryFn: async (): Promise<RetrievalSourceList> => {
    const { data, error, response } = await api.GET('/api/agent/retrieval/sources')
    if (error || !data) throw toApiError(error, response)
    return data
  },
  refetchInterval: (query) => (isIndexing(query.state.data) ? RETRIEVAL_POLL_MS : false),
  retry: false,
})

export function retrievalSearchQuery(params: RetrievalSearchParams | null) {
  return queryOptions({
    queryKey: retrievalSearchKey(params ?? { q: '' }),
    queryFn: async (): Promise<RetrievalResults> => {
      if (!params) throw new Error('还没有检索词')
      const { data, error, response } = await api.GET('/api/agent/retrieval/search', {
        params: {
          query: {
            q: params.q,
            sources: params.sources?.join(','),
            top_k: params.top_k,
          },
        },
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    enabled: false,
    retry: false,
  })
}

/** 开关来源；返回的视图直接写回缓存，再让列表整片失效一次。 */
export function useUpdateRetrievalSource() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      sourceId,
      body,
    }: {
      sourceId: string
      body: RetrievalSourceUpdate
    }): Promise<RetrievalSource> => {
      const { data, error, response } = await api.PUT('/api/agent/retrieval/sources/{source_id}', {
        params: { path: { source_id: sourceId } },
        body,
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    onSuccess: (source) => {
      queryClient.setQueryData<RetrievalSourceList>(retrievalSourcesKey, (current) =>
        current
          ? { ...current, items: current.items.map((item) => (item.id === source.id ? source : item)) }
          : current,
      )
      void queryClient.invalidateQueries({ queryKey: retrievalSourcesKey })
    },
  })
}

/**
 * 排队重建（202）。后台任务可能还没把状态改成 `indexing`，立刻 refetch 会读到
 * 旧的 `idle` 并停掉轮询；这里先把状态写成 `indexing`，由 2 秒轮询读到真正结果。
 */
export function useReindexRetrievalSource() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (sourceId: string): Promise<components['schemas']['ReindexAccepted']> => {
      const { data, error, response } = await api.POST('/api/agent/retrieval/sources/{source_id}/reindex', {
        params: { path: { source_id: sourceId } },
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    onSuccess: (_accepted, sourceId) => {
      queryClient.setQueryData<RetrievalSourceList>(retrievalSourcesKey, (current) =>
        current
          ? {
              ...current,
              items: current.items.map((item) =>
                item.id === sourceId ? { ...item, status: 'indexing' as const, error: null } : item,
              ),
            }
          : current,
      )
    },
  })
}
