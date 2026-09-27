import { formatDuration, pipelineStatusLabels, pipelineTone, type Tone } from '@/features/dashboard/format'
import { isObject, textValue } from './form'

/** 队列条目、历史列表行与流水线详情的宽松形状：后端返回的是无类型的字典。 */
export type PipelineRow = Record<string, unknown>

export function pipelineIdOf(row: PipelineRow): string {
  return textValue(row.id, textValue(row.pipeline_id))
}

/** 用户起的名字；队列条目没有名字时 `name` 就是 id，不算名字。 */
export function explicitTitle(row: PipelineRow): string | null {
  const id = pipelineIdOf(row)
  for (const candidate of [row.title, row.name]) {
    if (typeof candidate === 'string' && candidate.trim() && candidate !== id) return candidate.trim()
  }
  return null
}

/** 没有名字的流水线用任务类型中文名组合代替：最多列 3 个，更多时写成「开始唤醒、刷理智 等 5 项」。 */
export function taskNamesTitle(tasks: unknown): string | null {
  if (!Array.isArray(tasks)) return null
  const names = tasks
    .map((task) => (isObject(task) ? textValue(task.task_name) || textValue(task.type_name) : ''))
    .filter(Boolean)
  if (names.length === 0) return null
  return names.length > 3 ? `${names.slice(0, 2).join('、')} 等 ${names.length} 项` : names.join('、')
}

export const UNTITLED = '未命名流水线'

export function normalizeStatus(value: unknown): string {
  return typeof value === 'string' && value ? value.toLowerCase() : 'unknown'
}

export function statusLabel(status: string): string {
  return pipelineStatusLabels[status] ?? (status === 'unknown' ? '状态未知' : status)
}

export function statusTone(status: string): Tone {
  return status === 'unknown' ? 'neutral' : pipelineTone(status)
}

export function isActiveStatus(status: string): boolean {
  return status === 'pending' || status === 'running'
}

const sources: Record<string, string> = { manual: '手动', agent: 'Agent', scheduled: '定时' }

export function sourceLabel(source: unknown): string {
  if (typeof source !== 'string' || !source) return '未知来源'
  return sources[source] ?? source
}

const priorities: Record<number, string> = { 0: '最高', 1: '普通', 2: '最低' }

export function priorityLabel(priority: unknown): string {
  if (typeof priority !== 'number') return '未知'
  return priorities[priority] ? `${priority}（${priorities[priority]}）` : String(priority)
}

/** 接口里的时间：REST 是 ISO 字符串，实时事件是秒级时间戳。 */
export function parseTime(value: unknown): Date | null {
  if (typeof value === 'number' && Number.isFinite(value)) return new Date(value * 1000)
  if (typeof value !== 'string' || !value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

const pad = (value: number) => String(value).padStart(2, '0')

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
}

/** 「今天 14:05」「昨天 09:12」「9月25日 04:00」，跨年时带年份。 */
export function formatTime(value: unknown, options: { seconds?: boolean; now?: Date } = {}): string {
  const date = parseTime(value)
  if (!date) return '—'
  const now = options.now ?? new Date()
  const clock = `${pad(date.getHours())}:${pad(date.getMinutes())}${options.seconds ? `:${pad(date.getSeconds())}` : ''}`
  const days = Math.round((startOfDay(now) - startOfDay(date)) / 86_400_000)
  if (days === 0) return `今天 ${clock}`
  if (days === 1) return `昨天 ${clock}`
  const day = `${date.getMonth() + 1}月${date.getDate()}日`
  return date.getFullYear() === now.getFullYear() ? `${day} ${clock}` : `${date.getFullYear()}年${day} ${clock}`
}

/** 已结束的流水线给总耗时；运行中的给已运行时长；没开始的没有耗时。 */
export function pipelineElapsed(row: PipelineRow, now = Date.now()): { text: string; running: boolean } | null {
  const started = parseTime(row.started_at)
  if (!started) return null
  const finished = parseTime(row.finished_at)
  if (finished) return { text: formatDuration((finished.getTime() - started.getTime()) / 1000), running: false }
  if (normalizeStatus(row.status) !== 'running') return null
  return { text: formatDuration((now - started.getTime()) / 1000), running: true }
}

export function taskDuration(seconds: unknown): string | null {
  return typeof seconds === 'number' && Number.isFinite(seconds) ? formatDuration(seconds) : null
}
