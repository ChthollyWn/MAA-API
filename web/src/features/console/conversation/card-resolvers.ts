import { useCallback } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { isRecord, toShownError } from '@/features/agent/format'
import type { ShownError } from '@/features/agent/types'
import type { CopilotGrantOptions } from '@/features/copilots/CopilotGrantCard'

/** 确认卡的决定出口（M72 §4.6.3）：确认卡只认这五个成员，不依赖具体页面。 */
export interface ConfirmationResolver {
  resolve: (id: string, approved: boolean, overrides?: CopilotGrantOptions) => void
  busyFor: (id: string) => 'approve' | 'reject' | null
  error: ShownError | null
  clearError: () => void
}

interface ResolveInput {
  id: string
  approved: boolean
  overrides?: CopilotGrantOptions
}

function withoutQuestion(current: unknown, questionId: string): unknown {
  const keep = (items: unknown[]) =>
    items.filter((item) => !(isRecord(item) && item.question_id === questionId))
  if (Array.isArray(current)) return keep(current)
  if (isRecord(current) && Array.isArray(current.items)) {
    return { ...current, items: keep(current.items) }
  }
  return current
}

/**
 * 批准/拒绝一张确认卡（M72 §4.6.3）。
 *
 * 成功与 409（已在别处处理/已过期）都要让缓存跟上：成功另调 `onResolved`，把本轮
 * 实时副本里的卡也撤下。错误留在 `resolver.error` 里，由页面决定就地显示。
 */
export function useResolveConfirmation(onResolved?: (id: string) => void): ConfirmationResolver {
  const queryClient = useQueryClient()
  const invalidate = (id: string) => Promise.all([
    queryClient.invalidateQueries({ queryKey: keys.agent.pendingConfirmations() }),
    queryClient.invalidateQueries({ queryKey: [...keys.agent.confirmations(), id] }),
    queryClient.invalidateQueries({ queryKey: keys.agent.audits() }),
  ])

  const mutation = useMutation({
    mutationFn: async (input: ResolveInput) => {
      const { error, response } = await api.POST('/api/confirmations/{confirmation_id}', {
        params: { path: { confirmation_id: input.id } },
        body: { approved: input.approved, ...(input.overrides ? { overrides: input.overrides } : {}) },
      })
      if (error) throw toApiError(error, response)
      return input
    },
    onSuccess: async ({ id }) => {
      await invalidate(id)
      onResolved?.(id)
    },
    onError: async (error, input) => {
      // 409 说明这张卡已经不是待处理状态（别处批准/拒绝或过期）：缓存同样要重取。
      if (toApiError(error).status === 409) await invalidate(input.id)
    },
  })

  return {
    resolve: (id, approved, overrides) => mutation.mutate({ id, approved, overrides }),
    busyFor: (id) => mutation.isPending && mutation.variables?.id === id
      ? (mutation.variables.approved ? 'approve' : 'reject')
      : null,
    error: mutation.error ? toShownError(mutation.error) : null,
    clearError: () => mutation.reset(),
  }
}

/**
 * 提问处理完之后的收尾（M72 §4.6.3）：从「待你处理」的提问缓存里就地摘掉，
 * 再失效本会话的待答列表；提问卡自己负责提交答案。
 */
export function useResolveQuestion(onResolved?: (questionId: string) => void): (questionId: string) => void {
  const queryClient = useQueryClient()
  return useCallback((questionId: string) => {
    queryClient.setQueryData(keys.agent.attentionQuestions(), (current: unknown) =>
      withoutQuestion(current, questionId))
    void queryClient.invalidateQueries({ queryKey: keys.agent.pendingQuestions() })
    onResolved?.(questionId)
  }, [queryClient, onResolved])
}
