import { Link, useParams } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Page } from '@/components/layout/Page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ListGroup, ListItem } from '@/components/ui/list'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import { TicketStatusBadge } from '@/features/tickets/TicketStatusBadge'
import { describeGoalError, updateGoal, type GoalUpdate } from './api'
import {
  deadlineText,
  formatDateTime,
  goalProgressView,
  goalStatusBadge,
  milestoneDueText,
  milestoneProgressText,
  milestoneStatusBadge,
  nextReviewText,
  planningView,
  progressNotes,
  type PlanningView,
} from './format'
import { goalDetailQuery } from './queries'

const card = 'rounded-xl border bg-card shadow-card'

function MissingGoal() {
  return (
    <Page title="目标" back="/more/goals">
      <div className={card}>
        <EmptyState
          title="没有这个目标"
          description="回到目标列表重新选择"
          action={(
            <Button asChild variant="secondary" size="sm">
              <Link to="/more/goals">返回目标列表</Link>
            </Button>
          )}
        />
      </div>
    </Page>
  )
}

function PlanningAlert({ view }: { view: PlanningView }) {
  return (
    <InlineAlert
      tone={view.tone}
      title={view.title}
      action={view.ticketId ? (
        <Button asChild variant="outline" size="sm">
          <Link to={`/tickets/${view.ticketId}`}>查看规划工单</Link>
        </Button>
      ) : undefined}
    >
      {view.detail}
    </InlineAlert>
  )
}

function GoalDetailContent({ goalId }: { goalId: string }) {
  const queryClient = useQueryClient()
  const detail = useQuery(goalDetailQuery(goalId))
  const [confirm, confirmElement] = useConfirm()

  const update = useMutation({
    mutationFn: (body: GoalUpdate) => updateGoal(goalId, body),
    onSuccess: (next, body) => {
      queryClient.setQueryData(keys.agent.goal(goalId), next)
      toast.success(body.status === 'paused' ? '已暂停' : body.status === 'active' ? '已恢复' : '已取消')
      void queryClient.invalidateQueries({ queryKey: keys.agent.goals() })
    },
    onError: (error) => toast.error(`操作失败：${describeGoalError(error)}`),
  })

  const requestCancel = async () => {
    const approved = await confirm({
      title: '取消目标？',
      description: '停用它的定时任务，并停止关联的养成计划。',
      confirmText: '取消目标',
      cancelText: '先不取消',
      tone: 'danger',
    })
    if (approved) update.mutate({ status: 'cancelled' })
  }

  if (detail.isPending) {
    return (
      <Page title="目标" back="/more/goals">
        <div className={card}><LoadingState label="正在读取目标…" /></div>
      </Page>
    )
  }

  if (detail.error) {
    const failure = toApiError(detail.error)
    if (failure.code === 'GOAL_NOT_FOUND') return <MissingGoal />
    return (
      <Page title="目标" back="/more/goals">
        <div className={card}>
          <ErrorState
            title="目标读取失败"
            message={failure.message}
            onRetry={() => void detail.refetch()}
            retrying={detail.isFetching}
          />
        </div>
      </Page>
    )
  }

  if (!detail.data) {
    return (
      <Page title="目标" back="/more/goals">
        <div className={card}><LoadingState label="正在读取目标…" /></div>
      </Page>
    )
  }

  const goal = detail.data
  const statusBadge = goalStatusBadge(goal.status)
  const milestones = goal.milestones ?? []
  const tickets = goal.tickets ?? []
  const done = milestones.filter((milestone) => milestone.status === 'done').length
  const planning = planningView(goal)
  const progress = goalProgressView(goal)
  const allNotes = progressNotes(goal.progress)
  const notes = planning?.note ? allNotes.filter((note) => note !== planning.note) : allNotes
  const canPause = goal.status === 'active'
  const canResume = goal.status === 'paused'
  const ended = !canPause && !canResume

  const actions = ended ? null : (
    <div className="flex flex-wrap items-center gap-2 border-t border-border/60 pt-3">
      {canPause ? (
        <Button
          size="sm"
          loading={update.isPending && update.variables?.status === 'paused'}
          disabled={update.isPending}
          onClick={() => update.mutate({ status: 'paused' })}
        >
          暂停目标
        </Button>
      ) : null}
      {canResume ? (
        <Button
          size="sm"
          loading={update.isPending && update.variables?.status === 'active'}
          disabled={update.isPending}
          onClick={() => update.mutate({ status: 'active' })}
        >
          恢复目标
        </Button>
      ) : null}
      <Button
        size="sm"
        variant="destructive-soft"
        disabled={update.isPending}
        onClick={() => void requestCancel()}
      >
        取消目标
      </Button>
    </div>
  )

  return (
    <Page title={goal.title} back="/more/goals">
      <div className="space-y-5 pb-4">
        <div className={`${card} space-y-3 p-4`}>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <Badge variant={statusBadge.tone}>{statusBadge.label}</Badge>
            <span className="text-footnote text-muted-foreground">截止 {deadlineText(goal.deadline)}</span>
            <span className="text-footnote text-muted-foreground">下次复盘 {nextReviewText(goal.next_review_at)}</span>
          </div>
          <p className="whitespace-pre-wrap break-words text-sm">{goal.target.text}</p>
          {goal.description ? (
            <p className="whitespace-pre-wrap break-words text-footnote text-muted-foreground">{goal.description}</p>
          ) : null}
          {actions}
        </div>

        <Section id="goal-progress" title="进度">
          <div className={`${card} space-y-3 p-4`}>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              <dt className="text-muted-foreground">养成计划</dt>
              <dd className="break-words">{progress.plan}</dd>
              <dt className="text-muted-foreground">最近复盘</dt>
              <dd className="break-words">{progress.lastReview}</dd>
            </dl>
            {notes.length > 0 ? (
              <div className="space-y-1 border-t border-border/60 pt-3">
                <h3 className="text-footnote font-medium text-muted-foreground">处理说明</h3>
                <ul className="list-disc space-y-1 pl-5 text-footnote">
                  {notes.map((note, index) => (
                    <li key={index} className="break-words">{note}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        </Section>

        <Section
          id="goal-milestones"
          title="里程碑"
          description={milestones.length > 0 ? milestoneProgressText(done, milestones.length) : undefined}
        >
          {planning ? <PlanningAlert view={planning} /> : null}
          {milestones.length === 0 ? (
            planning ? null : (
              <div className={card}>
                <EmptyState title="还没有里程碑" description="规划工单完成后会出现在这里" />
              </div>
            )
          ) : (
            <ListGroup>
              {milestones.map((milestone) => {
                const badge = milestoneStatusBadge(milestone.status)
                return (
                  <ListItem
                    key={milestone.id}
                    title={`${milestone.ord}. ${milestone.title}`}
                    description={milestoneDueText(milestone)}
                    trailing={<Badge variant={badge.tone}>{badge.label}</Badge>}
                  />
                )
              })}
            </ListGroup>
          )}
        </Section>

        <Section id="goal-tickets" title="相关工单">
          {tickets.length === 0 ? (
            <p className="px-1 text-footnote text-muted-foreground">还没有相关工单</p>
          ) : (
            <ListGroup>
              {tickets.map((ticket) => (
                <ListItem
                  key={ticket.id}
                  to={`/tickets/${ticket.id}`}
                  title={ticket.title}
                  description={`更新于 ${formatDateTime(ticket.updated_at)}`}
                  trailing={<TicketStatusBadge status={ticket.status} />}
                />
              ))}
            </ListGroup>
          )}
        </Section>
      </div>
      {confirmElement}
    </Page>
  )
}

/** 路由入口：`useParams` 只在有 id 时渲染带 hooks 的详情组件。 */
export default function GoalDetailPage() {
  const { goalId } = useParams()
  if (!goalId) return <MissingGoal />
  return <GoalDetailContent goalId={goalId} />
}
