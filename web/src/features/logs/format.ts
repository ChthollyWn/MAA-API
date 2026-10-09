import type { LogRecord } from '@/realtime/events'

export const SOURCE_LABELS = { task: '任务', service: '服务', core: '内核' } as const

export function sourceLabel(source: unknown): string {
  const value = String(source ?? '')
  return Object.hasOwn(SOURCE_LABELS, value) ? SOURCE_LABELS[value as keyof typeof SOURCE_LABELS] : value || '未知来源'
}

/** 记录的级别：缺省为 INFO，`WARN` 视同 `WARNING`。 */
export function normalizeLevel(level: unknown): string {
  const value = String(level ?? 'INFO').toUpperCase()
  return value === 'WARN' ? 'WARNING' : value
}

const pad = (value: number, width = 2) => String(value).padStart(width, '0')

function toDate(value: unknown): Date | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  return new Date(value * 1000)
}

function clock(date: Date): string {
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

/** 列表里的时间：当天只显示时分秒，其余日期加上月日。 */
export function formatLogTime(value: unknown, now = new Date()): string {
  const date = toDate(value)
  if (!date) return '时间未知'
  const sameDay = date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate()
  return sameDay ? clock(date) : `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${clock(date)}`
}

export function formatFullTime(value: unknown): string {
  const date = toDate(value)
  if (!date) return '时间未知'
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${clock(date)}.${pad(date.getMilliseconds(), 3)}`
}

export function isoTime(value: unknown): string | undefined {
  return toDate(value)?.toISOString()
}

/** 把 `datetime-local` 的值写成芯片上的短文字；当年的日期省略年份。 */
export function formatDateTimeLocal(value: string, now = new Date()): string {
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/)
  if (!match) return value
  const [, year, month, day, hour, minute] = match
  const short = `${month}-${day} ${hour}:${minute}`
  return Number(year) === now.getFullYear() ? short : `${year}-${short}`
}

/** 列表与复制时使用的正文；内嵌的 data URL 图片不展示原文。 */
export function logText(record: LogRecord): string {
  const content = typeof record.content === 'string' ? record.content : ''
  if (content.startsWith('data:image/')) return '内嵌图片内容已省略'
  return content || '（空日志）'
}

export function logLogger(record: LogRecord): string | null {
  return typeof record.logger === 'string' && record.logger ? record.logger : null
}

export function copyableLine(record: LogRecord): string {
  const logger = logLogger(record)
  const head = [formatFullTime(record.ts), normalizeLevel(record.level), String(record.source ?? ''), ...(logger ? [logger] : [])]
  return `${head.join(' ')} - ${logText(record)}`
}

export interface LogImage {
  thumb: string
  full: string
}

/** 截图附件只接受本服务的 `/api/images/<sha256>/(thumb|full)`，其余地址一律忽略。 */
export function logImage(record: LogRecord): LogImage | null {
  const attachment = record.attachment
  if (!attachment || typeof attachment !== 'object') return null
  const data = attachment as Record<string, unknown>
  const hash = typeof data.sha256 === 'string' && /^[a-f\d]{64}$/i.test(data.sha256)
    ? data.sha256
    : null
  const normalize = (candidate: unknown, suffix: 'thumb' | 'full') => {
    if (typeof candidate !== 'string') return null
    const match = candidate.match(/^\/api\/images\/([a-f\d]{64})\/(thumb|full)$/i)
    return match ? `/api/images/${match[1]}/${suffix}` : null
  }
  const thumb = normalize(data.thumb_url, 'thumb') ?? (hash ? `/api/images/${hash}/thumb` : null)
  const full = normalize(data.full_url, 'full') ?? (hash ? `/api/images/${hash}/full` : null)
  return thumb && full ? { thumb, full } : null
}
