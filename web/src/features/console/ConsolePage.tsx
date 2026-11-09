import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { ArrowDown, Gauge, RotateCcw } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Page } from '@/components/layout/Page'
import { ActionMenu } from '@/components/ui/action-sheet'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InlineAlert } from '@/components/ui/inline-alert'
import { toast } from '@/components/ui/toaster'
import type { ShownError } from '@/features/agent/types'
import { useConversationScroll } from '@/features/agent/use-conversation-scroll'
import { checkpointsQueryOptions } from '@/features/compaction/api'
import { DESKTOP_QUERY, useMediaQuery } from '@/lib/media'
import { ActiveTicketsPanel } from './ActiveTicketsPanel'
import { ActiveTicketsStrip } from './ActiveTicketsStrip'
import { activeTicketsQuery, deviceLeaseQuery, orchestratorStateQuery, useSpecTitles } from './api'
import { AttentionPanel } from './AttentionPanel'
import { AttentionSheet } from './AttentionSheet'
import { AttentionSummaryBar } from './AttentionSummaryBar'
import { ConversationComposer } from './conversation/ConversationComposer'
import { ConversationView } from './conversation/ConversationView'
import { orchestratorEndpoints, useConversation } from './conversation/use-conversation'
import { useAttention } from './use-attention'

const QUICK_START_PROMPTS = ['把日常做了', '设备连上了吗', '上次那条流水线为什么失败'] as const

function QuickStart({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="mx-auto max-w-lg space-y-4 py-8 text-center">
      <h2 className="text-headline font-semibold">你可以这样开始</h2>
      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-center">
        {QUICK_START_PROMPTS.map((prompt) => (
          <Button
            key={prompt}
            type="button"
            variant="outline"
            className="h-auto whitespace-normal rounded-full py-2"
            onClick={() => onPick(prompt)}
          >
            {prompt}
          </Button>
        ))}
      </div>
      <p className="text-footnote text-muted-foreground">会消耗资源或改动游戏的操作，都会先问过你再执行。</p>
    </div>
  )
}

export default function ConsolePage() {
  const desktop = useMediaQuery(DESKTOP_QUERY)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [confirm, confirmElement] = useConfirm()
  const [sheetOpen, setSheetOpen] = useState(false)
  const [seed, setSeed] = useState<{ text: string; nonce: number } | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [scroller, setScroller] = useState<HTMLDivElement | null>(null)
  const [content, setContent] = useState<HTMLDivElement | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  const state = useQuery(orchestratorStateQuery)
  const sessionId = state.data?.session_id ?? null
  const checkpoints = useQuery(checkpointsQueryOptions(sessionId))
  const controller = useConversation(orchestratorEndpoints(sessionId), {
    turnRunning: Boolean(state.data?.turn_running),
    enabled: true,
  })
  const activeTickets = useQuery(activeTicketsQuery)
  const lease = useQuery(deviceLeaseQuery)
  const specTitles = useSpecTitles()

  const tickets = activeTickets.data?.items ?? []
  const leaseTicketId = lease.data?.held ? lease.data.ticketId : null

  // 实时副本里已经就地展示的卡不再进「待你处理」（§4.5.5）。
  const excludeIds = useMemo(() => {
    const ids = new Set<string>()
    if (controller.live?.pendingConfirmation) ids.add(controller.live.pendingConfirmation.confirmationId)
    if (controller.live?.pendingQuestion) ids.add(controller.live.pendingQuestion.question_id)
    return ids
  }, [controller.live])

  const attention = useAttention({
    excludeIds,
    onQuestionResolved: controller.onQuestionResolved,
  })

  // 处理完最后一张时手机面板自动收起（§4.6.4）；审批失败时保留面板，错误才看得见。
  useEffect(() => {
    if (sheetOpen && attention.items.length === 0 && !attention.planResolver.error) setSheetOpen(false)
  }, [sheetOpen, attention.items.length, attention.planResolver.error])

  // 服务端用 SSE error 事件回的错误在实时副本里；副本让位给历史后仍要留在页面上。
  const [liveError, setLiveError] = useState<ShownError | null>(null)
  useEffect(() => {
    if (!controller.live) return
    setLiveError(controller.live.error ?? null)
  }, [controller.live])

  const historyError = controller.query.error
    ? { message: `读取总控会话失败：${toApiError(controller.query.error).message}` }
    : null
  const shownError = controller.error ?? liveError ?? historyError

  const resetSession = useMutation({
    mutationFn: async () => {
      const { error, response } = await api.POST('/api/agent/orchestrator/reset')
      if (error) throw toApiError(error, response)
    },
    onSuccess: async () => {
      setActionError(null)
      toast.success('已重新开始总控会话')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.agent.orchestrator.all() }),
        queryClient.invalidateQueries({ queryKey: keys.agent.tickets.lists() }),
        queryClient.invalidateQueries({ queryKey: keys.agent.deviceLease() }),
        queryClient.invalidateQueries({ queryKey: keys.agent.pendingConfirmations() }),
        queryClient.invalidateQueries({ queryKey: keys.agent.attentionQuestions() }),
      ])
    },
    onError: (error) => {
      const apiError = toApiError(error)
      if (apiError.code === 'RUN_ALREADY_ACTIVE') {
        setActionError('重新开始总控会话失败：总控正在处理消息，先停止本轮')
        void queryClient.invalidateQueries({ queryKey: keys.agent.orchestrator.state() })
        return
      }
      setActionError(`重新开始总控会话失败：${apiError.message}`)
    },
  })

  const confirmReset = async () => {
    if (resetSession.isPending) return
    const accepted = await confirm({
      title: '重新开始总控会话？',
      description: '当前会话会归档；进行中的工单不受影响。',
      confirmText: '重新开始',
    })
    if (accepted) resetSession.mutate()
  }

  const actions = (
    <ActionMenu
      label="指挥台操作"
      title="指挥台操作"
      actions={[
        {
          label: '重新开始会话',
          description: '当前总控会话归档，进行中的工单不受影响',
          icon: RotateCcw,
          disabled: resetSession.isPending,
          onSelect: () => void confirmReset(),
        },
        { label: '运行状态', icon: Gauge, onSelect: () => void navigate('/more/status') },
      ]}
    />
  )

  const live = controller.live
  const liveKey = live
    ? [
        live.rootOrder.length,
        Object.values(live.runs).reduce((sum, run) => sum + run.segments.length, 0),
        Object.values(live.runs).reduce(
          (sum, run) => sum + run.segments.reduce((count, segment) => count + (segment.kind === 'text' ? segment.text.length : 0), 0),
          0,
        ),
        live.pendingConfirmation?.confirmationId ?? '',
        live.pendingQuestion?.question_id ?? '',
        live.running,
      ].join(':')
    : 'idle'
  const tailKey = [
    controller.visible.at(-1)?.id ?? 'none',
    liveKey,
    controller.queued.length,
    shownError?.message ?? '',
    controller.query.status,
  ].join('|')

  const scroll = useConversationScroll({
    container: desktop ? scroller : null,
    content,
    headKey: controller.history[0]?.id,
    tailKey,
    resetKey: 'orchestrator',
  })

  const showQuickStart = controller.query.isSuccess && controller.history.length === 0 && !controller.live
  const overlay = scroll.atBottom ? null : (
    <Button
      type="button"
      variant="secondary"
      size="sm"
      className="pointer-events-auto rounded-full shadow-float"
      onClick={() => scroll.scrollToBottom(true)}
    >
      <ArrowDown aria-hidden="true" />
      回到底部
    </Button>
  )

  const conversation = (
    <ConversationView
      scope={{ kind: 'orchestrator' }}
      history={controller.visible}
      mainSessionId={state.data?.session_id ?? null}
      specTitles={specTitles}
      checkpoints={checkpoints.data ?? []}
      live={controller.live}
      showOptimisticUser={controller.showOptimisticUser}
      queued={controller.queued}
      resolver={controller.confirmationResolver}
      onQuestionResolved={controller.onQuestionResolved}
      earlier={{
        hasEarlier: controller.hasEarlier,
        loading: controller.loadingEarlier,
        error: controller.earlierError,
        onLoad: () => controller.loadEarlier(scroll.rememberPositionBeforePrepend),
      }}
      error={shownError}
      emptyState={showQuickStart ? <QuickStart onPick={(text) => setSeed({ text, nonce: Date.now() })} /> : undefined}
      actions={{
        kind: 'orchestrator',
        running: Boolean(state.data?.turn_running),
        sessionId,
        onResend: (text) => controller.send({ content: text, mentions: [] }),
      }}
    />
  )

  const composer = (
    <ConversationComposer
      scope={{ kind: 'orchestrator' }}
      controller={controller}
      docked={!desktop}
      textareaRef={textareaRef}
      sessionId={sessionId}
      overlay={overlay}
      seed={seed ?? undefined}
    />
  )

  const actionAlert = actionError ? (
    <InlineAlert tone="danger" className="mb-3">{actionError}</InlineAlert>
  ) : null

  if (desktop) {
    return (
      <Page
        title="指挥台"
        width="full"
        hideConfirmationEntry
        actions={actions}
        className="h-[calc(100dvh_-_var(--nav-offset)_-_1.5rem)] min-h-[28rem]"
        contentClassName="flex min-h-0 flex-col"
      >
        {actionAlert}
        <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_22rem] gap-6">
          <section aria-label="总控会话" className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-card shadow-card">
            <div className="shrink-0 border-b px-4 py-2">
              <h2 className="text-headline font-semibold">总控会话</h2>
            </div>
            <div ref={setScroller} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
              <div ref={setContent} className="mx-auto w-full max-w-3xl px-6 py-6">{conversation}</div>
            </div>
            {composer}
          </section>
          <aside aria-label="指挥台侧栏" className="flex min-h-0 flex-col gap-6 overflow-y-auto">
            <AttentionPanel attention={attention} specTitles={specTitles} />
            <ActiveTicketsPanel
              tickets={tickets}
              leaseTicketId={leaseTicketId}
              specTitles={specTitles}
              isPending={activeTickets.isPending}
              error={activeTickets.error}
              onRetry={() => void activeTickets.refetch()}
              onCompose={() => textareaRef.current?.focus()}
            />
          </aside>
        </div>
        {confirmElement}
      </Page>
    )
  }

  const toolbar = attention.items.length > 0 || tickets.length > 0 ? (
    <div className="flex min-w-0 items-center gap-2 overflow-x-auto">
      {attention.items.length > 0 ? (
        <AttentionSummaryBar count={attention.items.length} onOpen={() => setSheetOpen(true)} />
      ) : null}
      <ActiveTicketsStrip
        tickets={tickets}
        leaseTicketId={leaseTicketId}
        specTitles={specTitles}
        isPending={activeTickets.isPending}
        error={activeTickets.error}
        onRetry={() => void activeTickets.refetch()}
      />
    </div>
  ) : undefined

  return (
    <Page title="指挥台" hideConfirmationEntry actions={actions} toolbar={toolbar}>
      <div ref={setContent}>
        {actionAlert}
        <section aria-label="总控会话" className="pb-4">{conversation}</section>
        {composer}
      </div>
      <AttentionSheet
        open={sheetOpen}
        onOpenChange={setSheetOpen}
        attention={attention}
        specTitles={specTitles}
      />
      {confirmElement}
    </Page>
  )
}
