import { useMemo } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import {
  MESSAGE_PAGE_SIZE,
  earlierPageNumber,
  loadMessageWindow,
  prependPage,
  windowItems,
  type MessageWindow,
} from './messages'
import type { AgentMessage, PageEnvelope } from './types'

async function fetchMessagePage(sessionId: string, page: number, signal?: AbortSignal): Promise<PageEnvelope<AgentMessage>> {
  const { data, error, response } = await api.GET('/api/agent/sessions/{session_id}/messages', {
    params: { path: { session_id: sessionId }, query: { page, size: MESSAGE_PAGE_SIZE } },
    signal,
  })
  if (error) throw toApiError(error, response)
  return data as unknown as PageEnvelope<AgentMessage>
}

/**
 * 当前会话的消息窗口。query key 沿用 `keys.agent.messages(id)`：发送结束后的 invalidate
 * 与实时层的 incident 事件都靠它刷新，刷新时保留已经加载的更早消息。
 */
export function useMessageWindow(sessionId: string | null) {
  const queryClient = useQueryClient()
  const queryKey = useMemo(
    () => (sessionId ? keys.agent.messages(sessionId) : [...keys.agent.all(), 'no-messages'] as const),
    [sessionId],
  )
  const query = useQuery({
    queryKey,
    enabled: Boolean(sessionId),
    queryFn: ({ signal }) => loadMessageWindow(
      (page) => fetchMessagePage(sessionId!, page, signal),
      () => queryClient.getQueryData<MessageWindow>(queryKey),
    ),
    retry: false,
  })

  const earlier = useMutation({
    mutationFn: async (input: { sessionId: string; page: number; beforeApply?: () => void }) => {
      const result = await fetchMessagePage(input.sessionId, input.page)
      const key = keys.agent.messages(input.sessionId)
      const current = queryClient.getQueryData<MessageWindow>(key)
      if (!current) return
      const next = prependPage(current, { page: input.page, items: result.items })
      if (next === current) return
      input.beforeApply?.()
      queryClient.setQueryData(key, next)
    },
  })

  const items = useMemo(() => windowItems(query.data), [query.data])
  const earlierPage = earlierPageNumber(query.data)
  const loadingEarlier = earlier.isPending && earlier.variables?.sessionId === sessionId
  const earlierError = earlier.error && earlier.variables?.sessionId === sessionId ? earlier.error : null

  /** 读取窗口之前的一页插到顶部；`beforeApply` 在写入前调用，用来记下滚动位置。 */
  const loadEarlier = (beforeApply?: () => void) => {
    if (!sessionId || earlierPage === null || loadingEarlier) return
    earlier.mutate({ sessionId, page: earlierPage, beforeApply })
  }

  return {
    query,
    items,
    hasEarlier: earlierPage !== null,
    loadEarlier,
    loadingEarlier,
    earlierError,
  }
}
