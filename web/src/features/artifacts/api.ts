import { infiniteQueryOptions, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { components } from '@/types/api'

/**
 * M90 §4.6.1 的产物查询与动作。
 *
 * 列表挂在 `artifactsAll()` 前缀下，`artifact_created` 的整片失效自动带上它；
 * 单个产物的详情与置顶单独失效（`artifact(id)` 不在前缀里）。
 */
export type ArtifactKind = components['schemas']['ArtifactKind']
export type ArtifactView = components['schemas']['ArtifactOut']
export type ArtifactDetailView = components['schemas']['ArtifactDetailOut']
export type ArtifactPage = components['schemas']['ArtifactPage']

/** 列表每页条数；与目标、工单同口径。 */
export const ARTIFACT_PAGE_SIZE = 20

export interface ArtifactListFilters {
  kind?: ArtifactKind
  ticketId?: string
}

/** 全部产物的分页列表；`kind` 与 `ticketId` 进 key，换筛选重新取第一页。 */
export function artifactsListInfiniteQuery(filters: ArtifactListFilters = {}) {
  const { kind, ticketId } = filters
  const request = {
    ...(kind ? { kind } : {}),
    ...(ticketId ? { ticket_id: ticketId } : {}),
  }
  return infiniteQueryOptions({
    queryKey: keys.agent.artifactsList({ kind, ticket_id: ticketId }),
    queryFn: async ({ pageParam }): Promise<ArtifactPage> => {
      const { data, error, response } = await api.GET('/api/agent/artifacts', {
        params: { query: { ...request, page: pageParam, size: ARTIFACT_PAGE_SIZE } },
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    initialPageParam: 1,
    getNextPageParam: (last) => {
      const page = last.page ?? 1
      const size = last.size ?? ARTIFACT_PAGE_SIZE
      return page * size < last.total ? page + 1 : undefined
    },
    retry: false,
  })
}

export interface PinArtifactInput {
  artifactId: string
  pinned: boolean
}

/** 置顶或取消置顶；成功失效整片产物列表与这个产物的详情。 */
export function usePinArtifact() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ artifactId, pinned }: PinArtifactInput): Promise<ArtifactView> => {
      const { data, error, response } = await api.PUT('/api/agent/artifacts/{artifact_id}/pin', {
        params: { path: { artifact_id: artifactId } },
        body: { pinned },
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    onSuccess: async (_data, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.agent.artifactsAll() }),
        queryClient.invalidateQueries({ queryKey: keys.agent.artifact(variables.artifactId) }),
      ])
    },
  })
}

/** 提交生成式表单；成功后详情、列表与工单会话都要重读。 */
export function useSubmitArtifactForm(artifactId: string, ticketId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (values: Record<string, unknown>) => {
      const { data, error, response } = await api.POST('/api/agent/artifacts/{artifact_id}/submit', {
        params: { path: { artifact_id: artifactId } },
        body: { values },
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.agent.artifact(artifactId) }),
        queryClient.invalidateQueries({ queryKey: keys.agent.artifactsAll() }),
        queryClient.invalidateQueries({ queryKey: keys.agent.tickets.messagesAll(ticketId) }),
        queryClient.invalidateQueries({ queryKey: keys.agent.tickets.detail(ticketId) }),
      ])
    },
  })
}
