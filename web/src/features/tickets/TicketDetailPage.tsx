import { useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity as ActivityIcon, Ban, Square } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Page } from '@/components/layout/Page'
import { ActionMenu } from '@/components/ui/action-sheet'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InlineAlert } from '@/components/ui/inline-alert'
import { SegmentedControl, type SegmentedOption } from '@/components/ui/segmented-control'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import { ConfirmationCard } from '@/features/agent/ConfirmationCard'
import { QuestionCard } from '@/features/agent/QuestionCard'
import type { ShownError } from '@/features/agent/types'
import { useConversationScroll } from '@/features/agent/use-conversation-scroll'
import { ConversationComposer } from '@/features/console/conversation/ConversationComposer'
import { ConversationView } from '@/features/console/conversation/ConversationView'
import { ticketEndpoints, useConversation } from '@/features/console/conversation/use-conversation'
import { checkpointsQueryOptions } from '@/features/compaction/api'
import { DESKTOP_QUERY, useMediaQuery } from '@/lib/media'
import { ActivityTree } from './ActivityTree'
import {
  attentionQuestionsQuery,
  deviceLeaseQuery,
  pendingConfirmationsQuery,
  ticketDetailQuery,
  useSpecTitles,
} from './api'
import { LeaseIndicator } from './LeaseIndicator'
import { MandateCard } from './MandateCard'
import { ReportTimeline } from './ReportTimeline'
import type { TicketStatusValue } from './format'
import { TicketStatusBadge } from './TicketStatusBadge'
import { TicketSummaryCard } from './TicketSummaryCard'

type TicketTab = 'messages' | 'reports' | 'activity'

const TAB_OPTIONS: readonly SegmentedOption<TicketTab>[] = [
  { value: 'messages', label: '消息' },
  { value: 'reports', label: '汇报' },
  { value: 'activity', label: '活动' },
]

const ENDED_STATUSES: readonly TicketStatusValue[] = ['done', 'failed', 'cancelled']
const ENDED_REASON = '工单已结束，不能再发消息'

function tabOf(value: string | null): TicketTab {
  return value === 'reports' || value === 'activity' ? value : 'messages'
}

/** 找不到工单时的空态（404）。 */
function MissingTicket() {
  return (
    <Page title="工单" back="/tickets">
      <div className="rounded-xl border bg-card shadow-card">
        <EmptyState
          title="没有这个工单"
          description="回到工单列表重新选择"
          action={(
            <Button asChild variant="secondary" size="sm">
              <Link to="/tickets">返回工单列表</Link>
            </Button>
          )}
        />
      </div>
    </Page>
  )
}

/**
 * 工单详情（M72 §4.6.5）：Worker 会话 + 概要、租约、汇报、活动与委托。
 *
 * 桌面两列；手机吸顶状态行与「消息 / 汇报 / 活动」分段，只有消息分段有输入栏。
 */
function TicketDetail({ ticketId }: { ticketId: string }) {
  const desktop = useMediaQuery(DESKTOP_QUERY)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const [confirm, confirmElement] = useConfirm()
  const [actionError, setActionError] = useState<string | null>(null)
  const [scroller, setScroller] = useState<HTMLDivElement | null>(null)
  const [content, setContent] = useState<HTMLDivElement | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  const detail = useQuery(ticketDetailQuery(ticketId))
  const ticket = detail.data?.ticket
  const ended = ticket ? ENDED_STATUSES.includes(ticket.status) : false
  const specTitles = useSpecTitles()
  const lease = useQuery(deviceLeaseQuery)
  // M74 §4.6：压缩标记跟工单的 Worker 会话走，不用工单 id。
  const workerSessionId = detail.data?.workerSessionId ?? null
  const checkpoints = useQuery(checkpointsQueryOptions(workerSessionId))

  const controller = useConversation(ticketEndpoints(ticketId, workerSessionId), {
    turnRunning: detail.data?.turnRunning === true,
    enabled: true,
  })

  const confirmations = useQuery(pendingConfirmationsQuery)
  const questions = useQuery(attentionQuestionsQuery)

  // 设备被别的工单占着时，租约指示要写出那个工单的标题（§4.6.5）。
  const holderTicketId = lease.data?.held === true && lease.data.ticketId && lease.data.ticketId !== ticketId
    ? lease.data.ticketId
    : null
  const holderTicket = useQuery({
    ...ticketDetailQuery(holderTicketId ?? ''),
    enabled: Boolean(holderTicketId),
  })

  const tab = tabOf(searchParams.get('tab'))
  const changeTab = (next: TicketTab) => {
    const params = new URLSearchParams(searchParams)
    params.set('tab', next)
    setSearchParams(params, { replace: true })
  }

  const liveConfirmationId = controller.live?.pendingConfirmation?.confirmationId
  const liveQuestionId = controller.live?.pendingQuestion?.question_id
  const pendingConfirmations = (confirmations.data?.items ?? []).filter(
    (item) => item.origin?.ticket_id === ticketId && item.confirmation_id !== liveConfirmationId,
  )
  const pendingQuestions = (questions.data ?? []).filter(
    (item) => item.origin?.ticket_id === ticketId && item.question_id !== liveQuestionId,
  )
  const leftovers = pendingConfirmations.length === 0 && pendingQuestions.length === 0 ? null : (
    <div className="space-y-3">
      {pendingConfirmations.map((item) => (
        <ConfirmationCard
          key={item.confirmation_id}
          confirmationId={item.confirmation_id}
          busy={controller.confirmationResolver.busyFor(item.confirmation_id)}
          onResolve={controller.confirmationResolver.resolve}
        />
      ))}
      {pendingQuestions.map((item) => (
        <QuestionCard key={item.question_id} question={item} onResolved={controller.onQuestionResolved} />
      ))}
    </div>
  )

  const cancelTicket = useMutation({
    mutationFn: async () => {
      const { error, response } = await api.POST('/api/agent/tickets/{ticket_id}/cancel', {
        params: { path: { ticket_id: ticketId } },
        body: {},
      })
      if (error) throw toApiError(error, response)
    },
    onSuccess: async () => {
      setActionError(null)
      toast.success('已取消工单')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.agent.tickets.detail(ticketId) }),
        queryClient.invalidateQueries({ queryKey: keys.agent.tickets.lists() }),
        queryClient.invalidateQueries({ queryKey: keys.agent.deviceLease() }),
      ])
    },
    onError: (error) => {
      const apiError = toApiError(error)
      setActionError(`取消工单失败：${apiError.message}`)
      if (apiError.code === 'TICKET_ENDED') {
        void queryClient.invalidateQueries({ queryKey: keys.agent.tickets.detail(ticketId) })
      }
    },
  })

  const confirmCancel = async () => {
    if (cancelTicket.isPending) return
    const accepted = await confirm({
      title: `取消工单「${ticket?.title ?? ''}」？`,
      description: 'Worker 会停下并释放设备，已提交的流水线不受影响。',
      confirmText: '取消工单',
      tone: 'danger',
    })
    if (accepted) cancelTicket.mutate()
  }

  const focusComposer = () => {
    if (!desktop) changeTab('messages')
    window.setTimeout(() => textareaRef.current?.focus(), 0)
  }

  const live = controller.live
  const liveKey = live
    ? [
        live.rootOrder.length,
        Object.values(live.runs).reduce((sum, run) => sum + run.segments.length, 0),
        Object.values(live.runs).reduce(
          (sum, run) => sum + run.segments.reduce(
            (count, segment) => count + (segment.kind === 'text' ? segment.text.length : 0),
            0,
          ),
          0,
        ),
        live.pendingConfirmation?.confirmationId ?? '',
        live.pendingQuestion?.question_id ?? '',
        live.running,
      ].join(':')
    : 'idle'

  const historyError: ShownError | null = controller.query.error
    ? { message: `读取工单会话失败：${toApiError(controller.query.error).message}` }
    : null
  const shownError: ShownError | null = controller.error
    ? { message: `发送消息失败：${controller.error.message}`, code: controller.error.code }
    : historyError
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
    resetKey: `ticket:${ticketId}`,
  })

  const actions = ticket ? (
    <ActionMenu
      label="工单操作"
      title="工单操作"
      actions={[
        ...(controller.canStop ? [{
          label: '停止本轮',
          icon: Square,
          onSelect: () => controller.stop(),
        }] : []),
        ...(!ended ? [{
          label: '取消工单',
          icon: Ban,
          tone: 'danger' as const,
          disabled: cancelTicket.isPending,
          onSelect: () => void confirmCancel(),
        }] : []),
        {
          label: '查看运行追踪',
          icon: ActivityIcon,
          onSelect: () => void navigate(`/more/runs?ticket_id=${encodeURIComponent(ticketId)}`),
        },
      ]}
    />
  ) : undefined

  const actionAlert = actionError ? (
    <InlineAlert tone="danger" className="mb-3">{actionError}</InlineAlert>
  ) : null

  const conversation = (
    <ConversationView
      scope={{ kind: 'ticket', ticketId }}
      history={controller.visible}
      mainSessionId={detail.data?.workerSessionId ?? null}
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
      leftovers={desktop ? undefined : leftovers}
    />
  )

  const composer = ended ? (
    <>
      <ConversationComposer
        scope={{ kind: 'ticket', ticketId }}
        controller={controller}
        docked={!desktop}
        textareaRef={textareaRef}
        sessionId={workerSessionId}
        disabledReason={ENDED_REASON}
      />
      <p className="pb-3 text-center text-footnote">
        <Link
          to="/"
          className="font-medium text-primary-text underline decoration-primary-text/40 underline-offset-2 hover:decoration-primary-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          在指挥台继续
        </Link>
      </p>
    </>
  ) : (
    <ConversationComposer
      scope={{ kind: 'ticket', ticketId }}
      controller={controller}
      docked={!desktop}
      textareaRef={textareaRef}
      sessionId={workerSessionId}
    />
  )

  if (detail.isPending) {
    return (
      <Page title="工单" back="/tickets">
        <div className="rounded-xl border bg-card shadow-card">
          <LoadingState label="正在读取工单…" />
        </div>
      </Page>
    )
  }

  if (detail.error) {
    const apiError = toApiError(detail.error)
    if (apiError.status === 404 || apiError.code === 'TICKET_NOT_FOUND') return <MissingTicket />
    return (
      <Page title="工单" back="/tickets">
        <div className="rounded-xl border bg-card shadow-card">
          <ErrorState
            message={`读取工单失败：${apiError.message}`}
            onRetry={() => void detail.refetch()}
            retrying={detail.isFetching}
          />
        </div>
      </Page>
    )
  }

  if (!detail.data || !ticket) return <MissingTicket />

  const pageTitle = ticket.title
  const leaseIndicator = (
    <LeaseIndicator
      ticketId={ticketId}
      lease={lease.data}
      holderTitle={holderTicket.data?.ticket.title ?? null}
    />
  )

  if (desktop) {
    return (
      <Page
        title={pageTitle}
        back="/tickets"
        width="full"
        actions={actions}
        className="h-[calc(100dvh_-_var(--nav-offset)_-_1.5rem)] min-h-[28rem]"
        contentClassName="flex min-h-0 flex-col"
      >
        {actionAlert}
        <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_22rem] gap-6">
          <section aria-label="Worker 会话" className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-card shadow-card">
            <div className="shrink-0 border-b px-4 py-2">
              <h2 className="text-headline font-semibold">消息</h2>
            </div>
            <div ref={setScroller} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
              <div ref={setContent} className="mx-auto w-full max-w-3xl px-6 py-6">{conversation}</div>
            </div>
            {composer}
          </section>
          <aside aria-label="工单侧栏" className="flex min-h-0 flex-col gap-6 overflow-y-auto">
            <TicketSummaryCard detail={detail.data} specTitles={specTitles}>{leftovers}</TicketSummaryCard>
            {leaseIndicator}
            <ReportTimeline ticketId={ticketId} onCompose={focusComposer} />
            <ActivityTree ticketId={ticketId} specTitles={specTitles} />
            {detail.data.mandate ? <MandateCard mandate={detail.data.mandate} /> : null}
          </aside>
        </div>
        {confirmElement}
      </Page>
    )
  }

  const toolbar = (
    <div className="space-y-2">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <TicketStatusBadge status={ticket.status} />
        {lease.data?.held ? <div className="w-full">{leaseIndicator}</div> : null}
      </div>
      <SegmentedControl
        ariaLabel="工单视图"
        value={tab}
        onValueChange={changeTab}
        options={TAB_OPTIONS}
        fullWidth
      />
    </div>
  )

  return (
    <Page title={pageTitle} back="/tickets" toolbar={toolbar} actions={actions}>
      <div ref={setContent}>
        {actionAlert}
        {tab === 'messages' ? (
          <div className="space-y-4 pb-4">
            <TicketSummaryCard detail={detail.data} specTitles={specTitles} />
            <section aria-label="Worker 会话">{conversation}</section>
          </div>
        ) : null}
        {tab === 'reports' ? (
          <div className="space-y-4 pb-4">
            {detail.data.mandate ? <MandateCard mandate={detail.data.mandate} /> : null}
            <ReportTimeline ticketId={ticketId} onCompose={focusComposer} />
          </div>
        ) : null}
        {tab === 'activity' ? (
          <div className="pb-4">
            <ActivityTree ticketId={ticketId} specTitles={specTitles} />
          </div>
        ) : null}
        {tab === 'messages' ? composer : null}
      </div>
      {confirmElement}
    </Page>
  )
}

/** 路由入口：`useParams` 只在有 id 时渲染带 hooks 的详情组件。 */
export default function TicketDetailPage() {
  const { ticketId } = useParams()
  if (!ticketId) return <MissingTicket />
  return <TicketDetail ticketId={ticketId} />
}
