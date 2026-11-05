import { useMemo } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { attentionMemoriesQuery } from '@/features/memory/api'
import {
  invalidateWatchdogAlerts,
  resolveWatchdogAlert,
} from '@/features/watchdog/api'
import { criticalWatchdogAlertsQuery } from '@/features/watchdog/queries'
import { toShownError } from '@/features/agent/format'
import type { ShownError } from '@/features/agent/types'
import { useResolveConfirmation, useResolveQuestion, type ConfirmationResolver } from './conversation/card-resolvers'
import { attentionQuestionsQuery, attentionTicketsQuery, pendingConfirmationsQuery } from './api'
import { buildAttentionItems, type AttentionItem } from './attention'

/** 严重告警卡上的「标记已处理」（M83 §4.5）：解除后整片重取告警列表。 */
export interface WatchdogAlertResolver {
  resolve: (id: number) => void
  busyFor: (id: number) => boolean
  error: ShownError | null
  clearError: () => void
}

export interface AttentionState {
  items: AttentionItem[]
  isPending: boolean
  error: unknown
  refetch: () => void
  resolver: ConfirmationResolver
  alertResolver: WatchdogAlertResolver
  onQuestionResolved: (questionId: string) => void
}

function useResolveWatchdogAlert(): WatchdogAlertResolver {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: (alertId: number) => resolveWatchdogAlert(alertId),
    onSuccess: () => invalidateWatchdogAlerts(queryClient),
    // 404 说明这条告警已经在别处解除：同样重取，卡片才会消失。
    onError: () => invalidateWatchdogAlerts(queryClient),
  })

  return {
    resolve: (id) => mutation.mutate(id),
    busyFor: (id) => mutation.isPending && mutation.variables === id,
    error: mutation.error ? toShownError(mutation.error) : null,
    clearError: () => mutation.reset(),
  }
}

/**
 * 「待你处理」的数据源（M72 §4.6.4）：确认、全局待答提问、待审核记忆、
 * 严重且未解除的值守告警（M83 §4.5）与 waiting/blocked 工单合并。
 *
 * `excludeIds` 是实时副本里已经就地展示的卡片；它们不在这里重复出现，但仍参与
 * 工单覆盖计算（§4.5.5）。待审核记忆合成一项「N 条记忆待审核」（M79 §4.5）。
 */
export function useAttention(options: {
  excludeIds: ReadonlySet<string>
  onQuestionResolved?: (id: string) => void
}): AttentionState {
  const confirmations = useQuery(pendingConfirmationsQuery)
  const questions = useQuery(attentionQuestionsQuery)
  const tickets = useQuery(attentionTicketsQuery)
  const memories = useQuery(attentionMemoriesQuery)
  const alerts = useQuery(criticalWatchdogAlertsQuery)

  const resolver = useResolveConfirmation()
  const alertResolver = useResolveWatchdogAlert()
  const onQuestionResolved = useResolveQuestion(options.onQuestionResolved)

  const items = useMemo(
    () => buildAttentionItems({
      confirmations: confirmations.data?.items ?? [],
      questions: questions.data ?? [],
      tickets: tickets.data?.items ?? [],
      memories: memories.data ?? [],
      alerts: alerts.data?.items ?? [],
      excludeIds: options.excludeIds,
    }),
    [
      confirmations.data?.items,
      questions.data,
      tickets.data?.items,
      memories.data,
      alerts.data?.items,
      options.excludeIds,
    ],
  )

  const refetch = () => {
    void confirmations.refetch()
    void questions.refetch()
    void tickets.refetch()
    void memories.refetch()
    void alerts.refetch()
  }

  return {
    items,
    isPending: confirmations.isPending || questions.isPending || tickets.isPending || memories.isPending
      || alerts.isPending,
    error: confirmations.error ?? questions.error ?? tickets.error ?? memories.error ?? alerts.error ?? null,
    refetch,
    resolver,
    alertResolver,
    onQuestionResolved,
  }
}
