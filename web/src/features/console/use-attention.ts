import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useResolveConfirmation, useResolveQuestion, type ConfirmationResolver } from './conversation/card-resolvers'
import { attentionQuestionsQuery, attentionTicketsQuery, pendingConfirmationsQuery } from './api'
import { buildAttentionItems, type AttentionItem } from './attention'

export interface AttentionState {
  items: AttentionItem[]
  isPending: boolean
  error: unknown
  refetch: () => void
  resolver: ConfirmationResolver
  onQuestionResolved: (questionId: string) => void
}

/**
 * 「待你处理」的数据源（M72 §4.6.4）：确认、全局待答提问与 waiting/blocked 工单合并。
 *
 * `excludeIds` 是实时副本里已经就地展示的卡片；它们不在这里重复出现，但仍参与
 * 工单覆盖计算（§4.5.5）。
 */
export function useAttention(options: {
  excludeIds: ReadonlySet<string>
  onQuestionResolved?: (id: string) => void
}): AttentionState {
  const confirmations = useQuery(pendingConfirmationsQuery)
  const questions = useQuery(attentionQuestionsQuery)
  const tickets = useQuery(attentionTicketsQuery)

  const resolver = useResolveConfirmation()
  const onQuestionResolved = useResolveQuestion(options.onQuestionResolved)

  const items = useMemo(
    () => buildAttentionItems({
      confirmations: confirmations.data?.items ?? [],
      questions: questions.data ?? [],
      tickets: tickets.data?.items ?? [],
      excludeIds: options.excludeIds,
    }),
    [confirmations.data?.items, questions.data, tickets.data?.items, options.excludeIds],
  )

  const refetch = () => {
    void confirmations.refetch()
    void questions.refetch()
    void tickets.refetch()
  }

  return {
    items,
    isPending: confirmations.isPending || questions.isPending || tickets.isPending,
    error: confirmations.error ?? questions.error ?? tickets.error ?? null,
    refetch,
    resolver,
    onQuestionResolved,
  }
}
