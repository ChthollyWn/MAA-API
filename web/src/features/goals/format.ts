import type { StatusTone } from '@/components/ui/badge'
import type { GoalDetail, Milestone } from './api'

/**
 * 目标页的展示规则（M82 规格 §4.6）。
 *
 * 状态文案沿用项目里工单 / 任务的口径；复盘时间在界面上是 `HH:MM`，后端存
 * 五段 cron，两者在这里互转。设置里的 cron 不是「每天某时某分」的形状时，
 * `reviewTimeFromCron` 返回 null，界面改为只读显示原 cron。
 */

/** 设置项读不到时的兜底；与 `goals.default_review_cron` 的默认值一致。 */
export const DEFAULT_REVIEW_TIME = '22:00'

const GOAL_STATUS_LABELS: Record<string, string> = {
  active: '进行中',
  paused: '已暂停',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const GOAL_STATUS_TONES: Record<string, StatusTone> = {
  active: 'primary',
  paused: 'warning',
  done: 'success',
  failed: 'danger',
  cancelled: 'neutral',
}

export const MILESTONE_STATUS_LABELS: Record<string, string> = {
  pending: '待开始',
  active: '进行中',
  done: '已完成',
  missed: '已逾期',
  skipped: '已跳过',
}

const MILESTONE_STATUS_TONES: Record<string, StatusTone> = {
  pending: 'neutral',
  active: 'primary',
  done: 'success',
  missed: 'danger',
  skipped: 'neutral',
}

/** 表里没有的取值原样显示，避免把服务端新增的枚举写丢。 */
export function goalStatusBadge(status: string): { label: string; tone: StatusTone } {
  return {
    label: GOAL_STATUS_LABELS[status] ?? status,
    tone: GOAL_STATUS_TONES[status] ?? 'neutral',
  }
}

export function milestoneStatusBadge(status: string): { label: string; tone: StatusTone } {
  return {
    label: MILESTONE_STATUS_LABELS[status] ?? status,
    tone: MILESTONE_STATUS_TONES[status] ?? 'neutral',
  }
}

const dateTimeFormat = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' })

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : dateTimeFormat.format(date)
}

/** 目标列表与详情里的「截止」；没有截止时写明。 */
export function deadlineText(value: string | null | undefined): string {
  return value ? formatDateTime(value) : '没有截止'
}

/** 下次复盘；已结束的目标没有 job。 */
export function nextReviewText(value: string | null | undefined): string {
  return value ? formatDateTime(value) : '—'
}

/** 列表行逐字用「已完成 n / 共 m」。 */
export function milestoneProgressText(done: number, total: number): string {
  return `已完成 ${done} / 共 ${total}`
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** `progress.notes` 只认字符串条目；旧行或异常的 payload 直接跳过。 */
export function progressNotes(progress: GoalDetail['progress']): string[] {
  const raw = progress?.notes
  if (!Array.isArray(raw)) return []
  return raw.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
}

function lastNoteStartingWith(notes: readonly string[], prefix: string): string | null {
  for (let index = notes.length - 1; index >= 0; index -= 1) {
    const note = notes[index]!
    if (note.startsWith(prefix)) return note
  }
  return null
}

export interface PlanningView {
  /** 规划中走 info；失败、放弃、结果不合法走 warning。 */
  tone: 'info' | 'warning'
  title: string
  /** 规划中时的一句解释；失败时原因已经写在 title 里。 */
  detail: string | null
  /** 有规划工单时给出「查看规划工单」的落点。 */
  ticketId: string | null
  /** 作为原因展示的那条 `progress.notes`；详情页不再在「处理说明」里重复一遍。 */
  note: string | null
}

/**
 * `progress.planning` 的展示（M82 §4.6）：
 * `pending` 显示「规划中」；`failed`、`dropped`、`invalid_details` 显示原因与
 * 「查看规划工单」。原因优先取 `progress.notes` 里最后一条对应的说明
 * （创建失败是「规划没开始：…」，结果不合法是「规划结果不合法：…」）。
 */
export function planningView(
  goal: Pick<GoalDetail, 'progress' | 'planning_ticket_id'>,
): PlanningView | null {
  const progress = goal.progress ?? {}
  const planning = typeof progress.planning === 'string' ? progress.planning : null
  if (!planning || planning === 'done') return null
  const ticketId = goal.planning_ticket_id ?? null
  if (planning === 'pending') {
    return {
      tone: 'info',
      title: '规划中',
      detail: '规划工单还在执行，完成后这里会列出里程碑。',
      ticketId,
      note: null,
    }
  }
  const notes = progressNotes(progress)
  if (planning === 'failed') {
    const note = lastNoteStartingWith(notes, '规划没开始')
    return {
      tone: 'warning',
      title: note ?? '规划失败',
      detail: null,
      ticketId,
      note,
    }
  }
  if (planning === 'dropped') {
    return {
      tone: 'warning',
      title: '规划工单已放弃',
      detail: null,
      ticketId,
      note: null,
    }
  }
  if (planning === 'invalid_details') {
    const note = lastNoteStartingWith(notes, '规划结果')
    return {
      tone: 'warning',
      title: note ?? '规划结果缺里程碑',
      detail: null,
      ticketId,
      note,
    }
  }
  return { tone: 'warning', title: planning, detail: null, ticketId, note: null }
}

export interface GoalProgressView {
  /** 养成计划剩余关卡与场次；没有关联计划时写明。 */
  plan: string
  /** 最近一次复盘时间。 */
  lastReview: string
}

/** 详情页「进度」：读复盘快照里的计划剩余量（M82 §4.6）。 */
export function goalProgressView(
  goal: Pick<GoalDetail, 'progress' | 'last_review_at' | 'upgrade_plan_id'>,
): GoalProgressView {
  const snapshot = asRecord(goal.progress?.last_snapshot)
  const plan = asRecord(snapshot.plan)
  const hasPlan = Object.keys(plan).length > 0
  const stages = numberValue(plan.remaining_stages)
  const runs = numberValue(plan.remaining_runs)
  return {
    plan: hasPlan
      ? `剩余 ${stages ?? '—'} 关 · ${runs ?? '—'} 场`
      : goal.upgrade_plan_id
        ? '等待第一次复盘'
        : '还没有关联养成计划',
    lastReview: goal.last_review_at ? formatDateTime(goal.last_review_at) : '还没有复盘',
  }
}

/** 里程碑的预计完成时间；没有时写明，不借用目标的截止。 */
export function milestoneDueText(milestone: Pick<Milestone, 'due_at'>): string {
  return milestone.due_at ? `预计完成 ${formatDateTime(milestone.due_at)}` : '预计完成时间待定'
}

const pad = (value: number) => String(value).padStart(2, '0')

/**
 * 设置里的 cron → 界面的 `HH:MM`。只接受「每天某时某分」的形状
 * （分 时 * * *）；其它表达式返回 null，界面只读显示原 cron。
 */
export function reviewTimeFromCron(cron: string | null | undefined): string | null {
  if (!cron) return null
  const parts = cron.trim().split(/\s+/)
  if (parts.length !== 5) return null
  if (parts[2] !== '*' || parts[3] !== '*' || parts[4] !== '*') return null
  if (!/^\d{1,2}$/.test(parts[0]!) || !/^\d{1,2}$/.test(parts[1]!)) return null
  const minute = Number(parts[0])
  const hour = Number(parts[1])
  if (minute < 0 || minute > 59 || hour < 0 || hour > 23) return null
  return `${pad(hour)}:${pad(minute)}`
}

/** 界面的 `HH:MM` → cron `MM HH * * *`；格式不对返回 null。 */
export function reviewCronFromTime(value: string): string | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(value.trim())
  if (!match) return null
  const hour = Number(match[1])
  const minute = Number(match[2])
  if (minute < 0 || minute > 59 || hour < 0 || hour > 23) return null
  return `${minute} ${hour} * * *`
}

/** `datetime-local` 的本地写法 → 带时区的 ISO；空串或非法值返回 null。 */
export function localDateTimeToIso(value: string): string | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date.toISOString()
}
