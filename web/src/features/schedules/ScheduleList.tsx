import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, Pencil, Play, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { ActionMenu } from '@/components/ui/action-sheet'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Section } from '@/components/ui/section'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState, ErrorState, Spinner } from '@/components/ui/states'
import { Switch } from '@/components/ui/switch'
import { toast } from '@/components/ui/toaster'
import { cn } from '@/lib/utils'
import { deleteSchedule, NEW_SCHEDULE_PATH, refreshAfterRun, runSchedule, schedulePath, setScheduleEnabled, updateScheduleCache } from './api'
import { deleteConfirmation, formatWhen, runConfirmation, runDetail, runStatusLabel, runTone, scheduleTimeSummary } from './format'
import type { ScheduleView } from './payload'
import { schedulesQuery } from './queries'

const card = 'rounded-xl border bg-card shadow-card'

type Confirm = ReturnType<typeof useConfirm>[0]

function ScheduleRow({ schedule, confirm }: { schedule: ScheduleView; confirm: Confirm }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const latest = schedule.last_run_result ?? schedule.recent_runs[0] ?? null
  const detail = latest ? runDetail(latest) : null

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => setScheduleEnabled(schedule.id, enabled),
    onMutate: async (enabled) => {
      setError(null)
      await queryClient.cancelQueries({ queryKey: schedulesQuery.queryKey })
      queryClient.setQueryData(schedulesQuery.queryKey, (rows) => rows?.map((row) => (row.id === schedule.id ? { ...row, enabled } : row)))
    },
    onSuccess: (saved) => updateScheduleCache(queryClient, { saved }),
    onError: (failure, enabled) => {
      queryClient.setQueryData(schedulesQuery.queryKey, (rows) => rows?.map((row) => (row.id === schedule.id ? { ...row, enabled: !enabled } : row)))
      void queryClient.invalidateQueries({ queryKey: ['schedules'] })
      setError(`没能${enabled ? '启用' : '停用'}：${toApiError(failure).message}`)
    },
  })
  const run = useMutation({
    mutationFn: () => runSchedule(schedule.id),
    onMutate: () => setError(null),
    onSuccess: async () => {
      toast.success(`「${schedule.name}」已加入队列`)
      await refreshAfterRun(queryClient)
    },
    onError: (failure) => setError(`运行失败：${toApiError(failure).message}`),
  })
  const remove = useMutation({
    mutationFn: () => deleteSchedule(schedule.id),
    onMutate: () => setError(null),
    onSuccess: () => {
      toast.success(`已删除「${schedule.name}」`)
      updateScheduleCache(queryClient, { removedId: schedule.id })
    },
    onError: (failure) => setError(`删除失败：${toApiError(failure).message}`),
  })
  const busy = run.isPending || remove.isPending

  return (
    <li>
      <div className="flex items-center pr-1.5">
        <Link
          to={schedulePath(schedule.id)}
          className="flex min-h-16 min-w-0 flex-1 flex-col justify-center py-3 pl-4 pr-2 transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        >
          <span className="block truncate text-[0.9375rem] font-medium">{schedule.name}</span>
          <span className="mt-0.5 block text-footnote text-muted-foreground">
            {scheduleTimeSummary(schedule)} · {schedule.template.length} 个任务
          </span>
          <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-footnote tabular-nums text-muted-foreground">
            <span>{schedule.enabled ? `下次 ${formatWhen(schedule.next_run_at)}` : '已停用'}</span>
            {latest ? (
              <span className="inline-flex items-center gap-1">上次 <Badge variant={runTone(latest.status)}>{runStatusLabel(latest.status)}</Badge></span>
            ) : (
              <span>尚未运行</span>
            )}
          </span>
          {detail ? (
            <span className={cn('mt-1 line-clamp-2 break-words text-footnote', detail.tone === 'danger' ? 'text-danger' : 'text-muted-foreground')}>{detail.text}</span>
          ) : null}
        </Link>
        <Switch
          className="mx-2"
          aria-label={`启用「${schedule.name}」`}
          checked={schedule.enabled}
          disabled={toggle.isPending}
          onCheckedChange={(enabled) => toggle.mutate(enabled)}
        />
        <ActionMenu
          label={`「${schedule.name}」的更多操作`}
          title={schedule.name}
          disabled={busy}
          actions={[
            {
              label: '立即运行',
              description: '马上执行一次，不影响定时安排',
              icon: Play,
              onSelect: () => void confirm(runConfirmation(schedule)).then((ok) => ok && run.mutate()),
            },
            { label: '编辑', icon: Pencil, onSelect: () => navigate(schedulePath(schedule.id)) },
            {
              label: '删除',
              icon: Trash2,
              tone: 'danger',
              onSelect: () => void confirm(deleteConfirmation(schedule)).then((ok) => ok && remove.mutate()),
            },
          ]}
        />
      </div>
      {error ? <p role="alert" className="px-4 pb-3 text-footnote font-medium text-danger">{error}</p> : null}
    </li>
  )
}

function MigrationNote() {
  return (
    <p className="px-1 text-footnote text-muted-foreground">
      旧版 <span className="font-mono">resource/daily_task.json</span> 里按星期配置的任务组，会在服务启动时自动迁移成定时任务，旧的星期编号也会一并校正。这里管理的是迁移后的记录，不会读写旧文件。
    </p>
  )
}

/** 任务 › 定时：每行名称、启停开关、时间摘要、下次执行、最近结果与任务数；点行进入编辑器。 */
export function ScheduleList() {
  const schedules = useQuery(schedulesQuery)
  const [confirm, confirmElement] = useConfirm()
  const rows = schedules.data ?? []

  let content
  if (schedules.isPending) {
    content = (
      <div role="status" aria-label="正在读取定时任务" className={cn(card, 'divide-y overflow-hidden')}>
        {[0, 1].map((index) => (
          <div key={index} className="space-y-2 px-4 py-4"><Skeleton className="h-4 w-32" /><Skeleton className="h-3 w-52" /><Skeleton className="h-3 w-40" /></div>
        ))}
      </div>
    )
  } else if (schedules.error && !schedules.data) {
    content = (
      <div className={card}>
        <ErrorState message={`无法读取定时任务：${toApiError(schedules.error).message}`} onRetry={() => void schedules.refetch()} retrying={schedules.isFetching} />
      </div>
    )
  } else if (rows.length === 0) {
    content = (
      <div className={card}>
        <EmptyState
          icon={CalendarClock}
          title="还没有定时任务"
          description="按星期和时间自动执行一组任务，比如每天早上刷理智、基建换班、领取奖励。"
          action={<Button asChild><Link to={NEW_SCHEDULE_PATH}><Plus aria-hidden="true" />新建定时任务</Link></Button>}
        />
      </div>
    )
  } else {
    const enabled = rows.filter((row) => row.enabled).length
    content = (
      <Section
        id="schedule-list"
        title="定时任务"
        description={`共 ${rows.length} 个，已启用 ${enabled} 个`}
        actions={(
          <>
            <Button variant="ghost" size="icon-sm" aria-label="刷新定时任务" onClick={() => void schedules.refetch()}>
              {schedules.isFetching ? <Spinner /> : <RefreshCw aria-hidden="true" />}
            </Button>
            <Button asChild variant="soft" size="sm">
              <Link to={NEW_SCHEDULE_PATH}><Plus aria-hidden="true" />新建定时任务</Link>
            </Button>
          </>
        )}
      >
        {schedules.error ? <InlineAlert tone="warning">定时任务更新失败：{toApiError(schedules.error).message}</InlineAlert> : null}
        <ul className={cn(card, 'divide-y overflow-hidden')}>
          {rows.map((schedule) => <ScheduleRow key={schedule.id} schedule={schedule} confirm={confirm} />)}
        </ul>
      </Section>
    )
  }

  return (
    <div className="space-y-4 pb-4">
      {content}
      <MigrationNote />
      {confirmElement}
    </div>
  )
}
