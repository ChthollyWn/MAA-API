import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
  type UseQueryResult,
} from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { toShownError } from '@/features/agent/format'
import {
  MESSAGE_PAGE_SIZE,
  earlierPageNumber,
  loadMessageWindow,
  prependPage,
  windowItems,
  type MessageWindow,
} from '@/features/agent/messages'
import { requestId, streamSse } from '@/features/agent/stream'
import type { PageEnvelope, ShownError } from '@/features/agent/types'
import { useResolveConfirmation, useResolveQuestion, type ConfirmationResolver } from './card-resolvers'
import {
  createLiveTurn,
  filterLiveHistory,
  finishLiveTurn,
  reduceLiveTurn,
  userMessageArrived,
  type LiveTurn,
} from './live-turn'
import { toSseV3Event } from './sse'
import type { MentionDraft, V3Message } from './types'

export interface ConversationEndpoints {
  scopeKey: string
  messagesKey: QueryKey
  /** 总控：orchestrator.state；工单：tickets.detail(id)。发送/停止收尾后一起失效。 */
  stateKey: QueryKey
  streamPath: string
  fetchPage: (page: number, signal?: AbortSignal) => Promise<PageEnvelope<V3Message>>
  interrupt: () => Promise<void>
}

async function fetchOrchestratorPage(page: number, signal?: AbortSignal): Promise<PageEnvelope<V3Message>> {
  const { data, error, response } = await api.GET('/api/agent/orchestrator/messages', {
    params: { query: { page, size: MESSAGE_PAGE_SIZE } },
    signal,
  })
  if (error) throw toApiError(error, response)
  return data as unknown as PageEnvelope<V3Message>
}

async function interruptOrchestrator(): Promise<void> {
  const { error, response } = await api.POST('/api/agent/orchestrator/interrupt')
  if (error) throw toApiError(error, response)
}

export function orchestratorEndpoints(): ConversationEndpoints {
  return {
    scopeKey: 'orchestrator',
    messagesKey: keys.agent.orchestrator.messages(),
    stateKey: keys.agent.orchestrator.state(),
    streamPath: '/api/agent/orchestrator/messages',
    fetchPage: fetchOrchestratorPage,
    interrupt: interruptOrchestrator,
  }
}

async function fetchTicketPage(ticketId: string, page: number, signal?: AbortSignal): Promise<PageEnvelope<V3Message>> {
  const { data, error, response } = await api.GET('/api/agent/tickets/{ticket_id}/messages', {
    params: {
      path: { ticket_id: ticketId },
      query: { page, size: MESSAGE_PAGE_SIZE, include_subagents: true },
    },
    signal,
  })
  if (error) throw toApiError(error, response)
  return data as unknown as PageEnvelope<V3Message>
}

async function interruptTicket(ticketId: string): Promise<void> {
  const { error, response } = await api.POST('/api/agent/tickets/{ticket_id}/interrupt', {
    params: { path: { ticket_id: ticketId } },
  })
  if (error) throw toApiError(error, response)
}

/** 工单会话：消息带子代理（`include_subagents=true`），流与中断都走该工单的端点。 */
export function ticketEndpoints(ticketId: string): ConversationEndpoints {
  return {
    scopeKey: `ticket:${ticketId}`,
    messagesKey: keys.agent.tickets.messages(ticketId, true),
    stateKey: keys.agent.tickets.detail(ticketId),
    streamPath: `/api/agent/tickets/${encodeURIComponent(ticketId)}/messages`,
    fetchPage: (page, signal) => fetchTicketPage(ticketId, page, signal),
    interrupt: () => interruptTicket(ticketId),
  }
}

export interface QueuedMessage {
  localId: string
  text: string
}

export interface ConversationController {
  /** 已加载的完整窗口。 */
  history: V3Message[]
  /** `filterLiveHistory` 之后、页面该渲染的历史。 */
  visible: V3Message[]
  query: UseQueryResult<MessageWindow<V3Message>>
  hasEarlier: boolean
  loadEarlier: (beforeApply?: () => void) => void
  loadingEarlier: boolean
  earlierError: unknown
  live: LiveTurn | null
  showOptimisticUser: boolean
  queued: readonly QueuedMessage[]
  send: (input: { content: string; mentions: MentionDraft[] }) => Promise<boolean>
  stop: () => void
  stopping: boolean
  canStop: boolean
  error: ShownError | null
  clearError: () => void
  confirmationResolver: ConfirmationResolver
  onQuestionResolved: (questionId: string) => void
}

interface QueuedState extends QueuedMessage {
  base: number
}

/**
 * 一轮会话的发送、排队、停止与历史窗口（M72 §4.5.3）。
 *
 * 行为要点：
 * - 本地已有流在跑时，第二条消息只提交、不读流（`detach`），以「排队中」气泡展示，
 *   等它落库进历史后由 `userMessageArrived` 移除。
 * - 停止走端点；成功后把 `stoppedRef` 记成当前 controller 再 abort，收尾阶段才能把
 *   「主动停止」与「换页/换工单的被动断开」区分开。
 * - `scopeKey` 变化时在 layout effect 里 abort 并清空实时副本、排队与错误。
 */
export function useConversation(
  endpoints: ConversationEndpoints,
  options: { turnRunning: boolean; enabled: boolean },
): ConversationController {
  const queryClient = useQueryClient()
  const scopeKey = endpoints.scopeKey
  const [live, setLive] = useState<LiveTurn | null>(null)
  const [queuedState, setQueuedState] = useState<QueuedState[]>([])
  const [error, setError] = useState<ShownError | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  // 被输入栏「停止」打断的本地流：catch/finally 靠它认出这是一次主动停止。
  const stoppedRef = useRef<AbortController | null>(null)
  // 发送代次：换工单/换页后，旧流迟到的收尾不能把新副本抹掉。
  const generationRef = useRef(0)

  const query = useQuery({
    queryKey: endpoints.messagesKey,
    enabled: options.enabled,
    queryFn: ({ signal }) => loadMessageWindow(
      (page) => endpoints.fetchPage(page, signal),
      () => queryClient.getQueryData<MessageWindow<V3Message>>(endpoints.messagesKey),
    ),
    retry: false,
  })

  const history = useMemo(() => windowItems(query.data), [query.data])
  const visible = useMemo(() => filterLiveHistory(history, live), [history, live])

  const earlier = useMutation({
    mutationFn: async (input: { endpoints: ConversationEndpoints; page: number; beforeApply?: () => void }) => {
      const result = await input.endpoints.fetchPage(input.page)
      const current = queryClient.getQueryData<MessageWindow<V3Message>>(input.endpoints.messagesKey)
      if (!current) return
      const next = prependPage(current, { page: input.page, items: result.items })
      if (next === current) return
      input.beforeApply?.()
      queryClient.setQueryData(input.endpoints.messagesKey, next)
    },
  })
  const earlierPage = earlierPageNumber(query.data)
  const loadingEarlier = earlier.isPending && earlier.variables?.endpoints.scopeKey === scopeKey
  const earlierError = earlier.error && earlier.variables?.endpoints.scopeKey === scopeKey
    ? earlier.error
    : null
  const loadEarlier = (beforeApply?: () => void) => {
    if (earlierPage === null || loadingEarlier) return
    earlier.mutate({ endpoints, page: earlierPage, beforeApply })
  }

  // 换工单（含卸载）：旧流不能继续往新会话的副本里写，排队与错误也清掉。
  useLayoutEffect(() => () => {
    generationRef.current += 1
    abortRef.current?.abort()
    abortRef.current = null
    stoppedRef.current = null
    setLive(null)
    setQueuedState([])
    setError(null)
  }, [scopeKey])

  const stopTurn = useMutation({
    mutationFn: async (target: ConversationEndpoints) => target.interrupt(),
    onSuccess: async (_data, target) => {
      const controller = abortRef.current
      if (controller) {
        // 先登记再 abort：send() 的收尾靠这个 ref 认出「主动停止」。
        stoppedRef.current = controller
        controller.abort()
      }
      setLive((current) => current && current.scopeKey === target.scopeKey
        ? { ...current, running: false, interrupted: true, pendingConfirmation: null, pendingQuestion: null }
        : current)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: target.messagesKey }),
        queryClient.invalidateQueries({ queryKey: target.stateKey }),
      ])
      setLive((current) => current?.pendingConfirmation || current?.pendingQuestion ? current : null)
    },
    onError: (stopError) => setError(toShownError(stopError)),
  })
  const stopping = stopTurn.isPending

  // 确认/提问处理掉之后，实时副本里的卡也要撤下（缓存由 resolver 自己失效）。
  const confirmationResolver = useResolveConfirmation((id) => {
    setLive((current) => current?.pendingConfirmation?.confirmationId === id
      ? { ...current, pendingConfirmation: null }
      : current)
  })
  const onQuestionResolved = useResolveQuestion((id) => {
    setLive((current) => current?.pendingQuestion?.question_id === id
      ? { ...current, pendingQuestion: null }
      : current)
  })

  // 排队的消息进了历史就撤掉气泡。
  useEffect(() => {
    setQueuedState((current) => {
      if (current.length === 0) return current
      const remaining = current.filter((item) => !userMessageArrived(history, item.text, item.base))
      return remaining.length === current.length ? current : remaining
    })
  }, [history])

  const send = async (input: { content: string; mentions: MentionDraft[] }): Promise<boolean> => {
    // 首屏历史还没到，没有可靠的 base：不发送，输入栏由页面保留草稿。
    if (!options.enabled || query.isPending) return false
    const text = input.content.trim()
    if (!text) return false
    const body = {
      content: text,
      mentions: input.mentions.map(({ type, id, label }) => ({ type, id, label })),
    }
    const base = history.at(-1)?.id ?? 0

    if (live?.running) {
      const localId = requestId()
      setQueuedState((current) => [...current, { localId, text, base }])
      try {
        // 服务端已受理，但本端不读流：回复经 WS 失效回到历史。
        await streamSse(endpoints.streamPath, body, new AbortController().signal, () => {}, { detach: true })
        return true
      } catch (queueError) {
        setQueuedState((current) => current.filter((item) => item.localId !== localId))
        setError(toShownError(queueError))
        return false
      }
    }

    const generation = ++generationRef.current
    const controller = new AbortController()
    abortRef.current = controller
    setError(null)
    setLive(createLiveTurn(scopeKey, text, base))
    let failed = false
    try {
      await streamSse(endpoints.streamPath, body, controller.signal, (name, data) => {
        setLive((current) => current && current.scopeKey === scopeKey
          ? reduceLiveTurn(current, toSseV3Event(name, data))
          : current)
      })
      setLive((current) => current ? finishLiveTurn(current) : current)
    } catch (streamError) {
      // 换页/换工单导致的被动断开：什么都不做（收尾也不失效/清场）。
      if (controller.signal.aborted && stoppedRef.current !== controller) return false
      if (!controller.signal.aborted) {
        const shown = toShownError(streamError)
        setError(shown)
        setLive((current) => current ? { ...finishLiveTurn(current), error: shown } : current)
        failed = true
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      const stoppedHere = stoppedRef.current === controller
      if (stoppedHere) stoppedRef.current = null
      // 被动断开：这一轮已与当前页面无关，不刷新、不清场。
      if (!(controller.signal.aborted && !stoppedHere)) {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: endpoints.messagesKey }),
          queryClient.invalidateQueries({ queryKey: endpoints.stateKey }),
        ])
        if (generation === generationRef.current) {
          // 还挂着确认或提问的一轮保留：卡要留在原位等用户处理。
          setLive((current) => current?.pendingConfirmation || current?.pendingQuestion ? current : null)
        }
      }
    }
    return !failed
  }

  const queued = useMemo<QueuedMessage[]>(
    () => queuedState.map(({ localId, text }) => ({ localId, text })),
    [queuedState],
  )

  const shownError = error ?? confirmationResolver.error
  const clearError = () => {
    setError(null)
    if (confirmationResolver.error) confirmationResolver.clearError()
  }

  return {
    history,
    visible,
    query,
    hasEarlier: earlierPage !== null,
    loadEarlier,
    loadingEarlier,
    earlierError,
    live,
    showOptimisticUser: live !== null && !userMessageArrived(history, live.userText, live.baseMessageId),
    queued,
    send,
    stop: () => stopTurn.mutate(endpoints),
    stopping,
    canStop: Boolean(live?.running) || options.turnRunning || stopping,
    error: shownError,
    clearError,
    confirmationResolver,
    onQuestionResolved,
  }
}
