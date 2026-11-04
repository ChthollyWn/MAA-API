import { queryOptions, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { ApiError, toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { components } from '@/types/api'

/** 一个压缩检查点（04 §2.3 的 `CheckpointView`）。 */
export type CheckpointView = components['schemas']['CheckpointView']
/** 手动压缩的结果（04 §2.3 的 `CompactResultView`）。 */
export type CompactResultView = components['schemas']['CompactResultView']

async function fetchCheckpoints(sessionId: string): Promise<CheckpointView[]> {
  const { data, error, response } = await api.GET('/api/agent/sessions/{session_id}/checkpoints', {
    params: { path: { session_id: sessionId } },
  })
  if (error || !data) throw toApiError(error, response)
  return data.items
}

/** 会话的检查点列表（`created_at` 降序）；没有会话 id 时不发请求。 */
export function checkpointsQueryOptions(sessionId: string | null | undefined) {
  return queryOptions({
    queryKey: keys.agent.checkpoints(sessionId ?? ''),
    queryFn: () => fetchCheckpoints(sessionId as string),
    enabled: Boolean(sessionId),
    retry: false,
  })
}

/**
 * 手动压缩（`POST /api/agent/sessions/{id}/compact`）。
 * 成功后只失效这一会话的 checkpoints，消息窗口由 SSE / WS 的既有路径处理。
 */
export function useCompactSession(sessionId: string | null | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (): Promise<CompactResultView> => {
      if (!sessionId) {
        throw new ApiError({ message: '会话还没有准备好，请稍后再试。' })
      }
      const { data, error, response } = await api.POST('/api/agent/sessions/{session_id}/compact', {
        params: { path: { session_id: sessionId } },
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    onSuccess: () => {
      if (sessionId) {
        void queryClient.invalidateQueries({ queryKey: keys.agent.checkpoints(sessionId) })
      }
    },
  })
}
