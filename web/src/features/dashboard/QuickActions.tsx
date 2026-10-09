import { useState } from 'react'
import { Link } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, Play, Plus } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import { schedulesQuery } from '@/features/schedules/queries'
import { describeSchedule } from '@/features/schedules/schedule-time'
import type { components } from '@/types/api'

type Schedule = components['schemas']['ScheduleView']

const tileClassName =
  'flex min-h-[4.75rem] w-full items-center gap-3 rounded-xl border bg-card px-4 py-3 text-left shadow-card transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'

function RunScheduleSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const queryClient = useQueryClient()
  const [confirm, confirmElement] = useConfirm()
  const schedules = useQuery({ ...schedulesQuery, enabled: open })
  const run = useMutation({
    mutationFn: async (schedule: Schedule) => {
      const { error, response } = await api.POST('/api/schedules/{schedule_id}/run', { params: { path: { schedule_id: schedule.id } }, body: {} })
      if (error) throw toApiError(error, response)
      return schedule
    },
    onSuccess: async (schedule) => {
      toast.success(`「${schedule.name}」已加入队列`)
      onOpenChange(false)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.queue.all() }),
        queryClient.invalidateQueries({ queryKey: keys.pipelines.current() }),
        queryClient.invalidateQueries({ queryKey: ['schedules'] }),
      ])
    },
    onError: (error) => toast.error(`运行失败：${toApiError(error).message}`),
  })

  const items = [...(schedules.data ?? [])].sort((left, right) => Number(right.enabled) - Number(left.enabled))

  const choose = async (schedule: Schedule) => {
    const ok = await confirm({
      title: `立即运行「${schedule.name}」？`,
      description: `这 ${schedule.template.length} 个任务会作为一条流水线入队，不影响原本的定时安排。`,
      confirmText: '立即运行',
    })
    if (ok) run.mutate(schedule)
  }

  return (
    <ResponsiveSheet open={open} onOpenChange={onOpenChange} title="运行定时任务" size="sm">
      {schedules.isPending ? (
        <LoadingState label="正在读取定时任务…" />
      ) : schedules.error ? (
        <ErrorState message={`读取定时任务失败：${toApiError(schedules.error).message}`} onRetry={() => void schedules.refetch()} retrying={schedules.isFetching} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={CalendarClock}
          title="还没有定时任务"
          description="先编排一组任务并保存为定时任务。"
          action={<Button asChild variant="outline" onClick={() => onOpenChange(false)}><Link to="/tasks/schedules/new">新建定时任务</Link></Button>}
        />
      ) : (
        <ul className="-mx-2 space-y-0.5">
          {items.map((schedule) => (
            <li key={schedule.id}>
              <button
                type="button"
                disabled={run.isPending}
                onClick={() => void choose(schedule)}
                className="flex min-h-14 w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
              >
                <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary-soft text-primary-text">
                  <Play aria-hidden="true" className="size-4" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[0.9375rem] font-medium">{schedule.name}</span>
                  <span className="block text-footnote text-muted-foreground">
                    {describeSchedule(schedule.cron) ?? `cron ${schedule.cron}`} · {schedule.template.length} 个任务
                  </span>
                </span>
                {schedule.enabled ? null : <Badge variant="neutral">已停用</Badge>}
              </button>
            </li>
          ))}
        </ul>
      )}
      {confirmElement}
    </ResponsiveSheet>
  )
}

/** 首页的两个常用入口：新建任务、立即运行某个定时任务。 */
export function QuickActions() {
  const [scheduleSheetOpen, setScheduleSheetOpen] = useState(false)
  return (
    <div className="grid grid-cols-2 gap-3">
      <Link to="/tasks" className={tileClassName}>
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary text-primary-foreground">
          <Plus aria-hidden="true" className="size-5" />
        </span>
        <span className="min-w-0">
          <span className="block text-[0.9375rem] font-semibold">新建任务</span>
        </span>
      </Link>
      <button type="button" className={tileClassName} onClick={() => setScheduleSheetOpen(true)}>
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary-soft text-primary-text">
          <CalendarClock aria-hidden="true" className="size-5" />
        </span>
        <span className="min-w-0">
          <span className="block text-[0.9375rem] font-semibold">运行定时任务</span>
        </span>
      </button>
      <RunScheduleSheet open={scheduleSheetOpen} onOpenChange={setScheduleSheetOpen} />
    </div>
  )
}
