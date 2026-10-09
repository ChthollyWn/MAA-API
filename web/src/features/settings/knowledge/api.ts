import { queryOptions, type QueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { ApiError, toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { components } from '@/types/api'
import { isKnowledgeKind } from './format'

export type KnowledgeSummary = components['schemas']['KnowledgeSummary']
export type KnowledgeDetail = components['schemas']['KnowledgeDetail']
export type KnowledgeWrite = components['schemas']['KnowledgeWriteRequest']

const KNOWLEDGE_KEY = [...keys.agent.all(), 'knowledge'] as const

export const knowledgeListKey = [...KNOWLEDGE_KEY, 'list'] as const

export function knowledgeDetailKey(entryId: string) {
  return [...KNOWLEDGE_KEY, 'detail', entryId] as const
}

export const knowledgeListQuery = queryOptions({
  queryKey: knowledgeListKey,
  queryFn: async (): Promise<KnowledgeSummary[]> => {
    const { data, error, response } = await api.GET('/api/agent/knowledge')
    if (error || !data) throw toApiError(error, response)
    return data.items
  },
  staleTime: 15_000,
})

export function knowledgeDetailQuery(entryId: string) {
  return queryOptions({
    queryKey: knowledgeDetailKey(entryId),
    queryFn: () => fetchKnowledgeEntry(entryId),
    staleTime: 15_000,
  })
}

async function fetchKnowledgeEntry(entryId: string): Promise<KnowledgeDetail> {
  const { data, error, response } = await api.GET('/api/agent/knowledge/{entry_id}', {
    params: { path: { entry_id: entryId } },
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

export async function saveKnowledgeEntry(entryId: string, body: KnowledgeWrite): Promise<KnowledgeDetail> {
  const { data, error, response } = await api.PUT('/api/agent/knowledge/{entry_id}', {
    params: { path: { entry_id: entryId } },
    body,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

export async function deleteKnowledgeEntry(entryId: string): Promise<void> {
  const { error, response } = await api.DELETE('/api/agent/knowledge/{entry_id}', {
    params: { path: { entry_id: entryId } },
  })
  if (error) throw toApiError(error, response)
}

/**
 * 列表里的启用开关：`PUT` 要求整份生效内容，而列表不带正文，
 * 所以先读一次详情，再改 `enabled` 写回。
 */
export async function setKnowledgeEnabled(entryId: string, enabled: boolean): Promise<KnowledgeDetail> {
  const detail = await fetchKnowledgeEntry(entryId)
  if (!isKnowledgeKind(detail.kind)) {
    throw new ApiError({ status: 500, message: `知识条目「${entryId}」的类型无法识别，请刷新页面后重试。` })
  }
  return saveKnowledgeEntry(entryId, {
    kind: detail.kind,
    title: detail.title,
    when_to_use: detail.when_to_use,
    body: detail.body,
    enabled,
  })
}

/** 写入或删除后让列表与这一条的详情一起失效。 */
export function invalidateKnowledge(queryClient: QueryClient, entryId?: string): Promise<unknown> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: knowledgeListKey }),
    entryId ? queryClient.invalidateQueries({ queryKey: knowledgeDetailKey(entryId) }) : Promise.resolve(),
  ])
}
