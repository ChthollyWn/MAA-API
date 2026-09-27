import type { StatusTone } from '@/components/ui/badge'
import type { ConfirmOptions } from '@/components/ui/confirm-dialog'
import { isObject } from '@/features/tasks/form'
import type { ScheduleRun, ScheduleView } from './payload'
import { describeSchedule } from './schedule-time'

function normalize(status: string | null | undefined): string {
  return (status ?? '').toLowerCase()
}

const RUN_LABELS: Readonly<Record<string, string>> = {
  completed: '成功',
  success: '成功',
  succeeded: '成功',
  failed: '失败',
  error: '失败',
  running: '运行中',
  pending: '排队中',
  skipped: '已跳过',
  cancelled: '已取消',
}

export function runStatusLabel(status: string | null | undefined): string {
  const key = normalize(status)
  return RUN_LABELS[key] ?? (key ? String(status) : '结果未知')
}

export function runTone(status: string | null | undefined): StatusTone {
  const label = runStatusLabel(status)
  if (label === '成功') return 'success'
  if (label === '失败') return 'danger'
  if (label === '运行中') return 'primary'
  if (label === '已跳过') return 'warning'
  return 'neutral'
}

/** 一次运行的补充说明：有错误消息就显示它；跳过但没有消息时说明原因。 */
export function runDetail(run: Pick<ScheduleRun, 'status' | 'error'>): { text: string; tone: 'danger' | 'muted' } | null {
  const message = isObject(run.error) && typeof run.error.message === 'string' && run.error.message ? run.error.message : null
  const failed = runTone(run.status) === 'danger'
  if (message) return { text: message, tone: failed ? 'danger' : 'muted' }
  if (normalize(run.status) === 'skipped') return { text: '上一次还没执行完，这次到点跳过了。', tone: 'muted' }
  return null
}

const pad = (value: number) => String(value).padStart(2, '0')

function dayNumber(date: Date): number {
  return Math.round(new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime() / 86_400_000)
}

/** 「今天 07:05」「明天 07:00」「昨天 23:00」「10月3日 04:00」，跨年时带年份；按设备时区显示。 */
export function formatWhen(value: string | null | undefined, now = new Date()): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  const clock = `${pad(date.getHours())}:${pad(date.getMinutes())}`
  const offset = dayNumber(date) - dayNumber(now)
  if (offset === 0) return `今天 ${clock}`
  if (offset === 1) return `明天 ${clock}`
  if (offset === -1) return `昨天 ${clock}`
  const day = `${date.getMonth() + 1}月${date.getDate()}日`
  return date.getFullYear() === now.getFullYear() ? `${day} ${clock}` : `${date.getFullYear()}年${day} ${clock}`
}

export function deviceTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone
}

/** 「工作日 04:00」；超出按星期设置的范围时显示原始 cron；时区与设备不同时注明时区。 */
export function scheduleTimeSummary(schedule: Pick<ScheduleView, 'cron' | 'timezone'>, localTimeZone = deviceTimeZone()): string {
  const summary = describeSchedule(schedule.cron) ?? `cron ${schedule.cron}`
  return schedule.timezone && schedule.timezone !== localTimeZone ? `${summary}（${schedule.timezone}）` : summary
}

/** 运行的总是服务端保存的版本；编辑器里有未保存的更改时一并说明。 */
export function runConfirmation(schedule: Pick<ScheduleView, 'name' | 'template'>, unsaved = false): ConfirmOptions {
  return {
    title: `立即运行「${schedule.name}」？`,
    description: `${schedule.template.length} 个任务会以最低优先级加入队列，不影响原本的定时安排。${unsaved ? '还没保存的更改不会生效，运行的是已保存的版本。' : ''}`,
    confirmText: '立即运行',
  }
}

export function deleteConfirmation(schedule: Pick<ScheduleView, 'name'>, unsaved = false): ConfirmOptions {
  return {
    title: `删除「${schedule.name}」？`,
    description: `删除后不再按时运行，已经执行过的流水线仍保留在历史里。${unsaved ? '还没保存的更改也会一并丢弃。' : ''}`,
    confirmText: '删除定时任务',
    tone: 'danger',
  }
}
