import type { LogLevelFilter } from '@/features/logs/use-log-history'
import { formatDateTimeLocal, normalizeLevel, SOURCE_LABELS } from '@/features/logs/format'
import type { LogRecord } from '@/realtime/events'

export type LogSourceFilter = 'all' | keyof typeof SOURCE_LABELS

export const LOG_SOURCES: readonly { value: LogSourceFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'task', label: SOURCE_LABELS.task },
  { value: 'service', label: SOURCE_LABELS.service },
  { value: 'core', label: SOURCE_LABELS.core },
]

export const LOG_LEVELS: readonly LogLevelFilter[] = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']

export function sourceFromPath(pathname: string): LogSourceFilter {
  const source = pathname.replace(/\/+$/, '').split('/').at(-1)
  return source === 'task' || source === 'service' || source === 'core' ? source : 'all'
}

export function sourcePath(source: LogSourceFilter): string {
  return source === 'all' ? '/logs' : `/logs/${source}`
}

/** 未选任何级别表示全部；多个级别之间是「或」。 */
export function matchesLevels(record: LogRecord, levels: readonly LogLevelFilter[]): boolean {
  if (levels.length === 0) return true
  return levels.includes(normalizeLevel(record.level) as LogLevelFilter)
}

export type TimePreset = 'last-15m' | 'last-1h' | 'today'

/** 时间范围保存 `datetime-local` 的原始值；快捷项在选中时换算成开始时间。 */
export interface TimeRange {
  preset: TimePreset | null
  since: string
  until: string
}

export const ANY_TIME: TimeRange = { preset: null, since: '', until: '' }

export const TIME_PRESETS: readonly { value: TimePreset; label: string }[] = [
  { value: 'last-15m', label: '最近 15 分钟' },
  { value: 'last-1h', label: '最近 1 小时' },
  { value: 'today', label: '今天' },
]

/** `datetime-local` 的值按浏览器本地时区解释，换算成接口使用的秒级时间戳。 */
export function asEpoch(value: string): number | undefined {
  if (!value) return undefined
  const timestamp = new Date(value).getTime()
  return Number.isFinite(timestamp) ? timestamp / 1000 : undefined
}

const pad = (value: number) => String(value).padStart(2, '0')

export function toDateTimeLocal(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function presetRange(preset: TimePreset, now = new Date()): TimeRange {
  const since = new Date(now)
  if (preset === 'today') since.setHours(0, 0, 0, 0)
  else since.setTime(now.getTime() - (preset === 'last-15m' ? 15 : 60) * 60_000)
  return { preset, since: toDateTimeLocal(since), until: '' }
}

export function isAnyTime(range: TimeRange): boolean {
  return !range.preset && !range.since && !range.until
}

/** 开始时间晚于结束时间时不会有任何匹配。 */
export function isInvertedRange(range: TimeRange): boolean {
  const since = asEpoch(range.since)
  const until = asEpoch(range.until)
  return since !== undefined && until !== undefined && since > until
}

/** 筛选面板里的条件；关键词在搜索框里，不算在内。 */
export interface LogFilters {
  levels: LogLevelFilter[]
  range: TimeRange
}

export const NO_FILTERS: LogFilters = { levels: [], range: ANY_TIME }

export interface FilterCondition {
  id: string
  label: string
}

export function filterConditions({ levels, range }: LogFilters): FilterCondition[] {
  const conditions: FilterCondition[] = LOG_LEVELS
    .filter((level) => levels.includes(level))
    .map((level) => ({ id: `level:${level}`, label: level }))
  const preset = TIME_PRESETS.find((item) => item.value === range.preset)
  if (preset) {
    conditions.push({ id: 'time', label: preset.label })
  } else {
    if (range.since) conditions.push({ id: 'since', label: `开始 ${formatDateTimeLocal(range.since)}` })
    if (range.until) conditions.push({ id: 'until', label: `结束 ${formatDateTimeLocal(range.until)}` })
  }
  return conditions
}

/** 去掉一个条件；未受影响的部分保持原引用，避免历史查询无谓地重新请求。 */
export function removeCondition(filters: LogFilters, id: string): LogFilters {
  if (id.startsWith('level:')) {
    return { ...filters, levels: filters.levels.filter((level) => `level:${level}` !== id) }
  }
  if (id === 'time') return { ...filters, range: ANY_TIME }
  if (id === 'since') return { ...filters, range: { ...filters.range, preset: null, since: '' } }
  if (id === 'until') return { ...filters, range: { ...filters.range, preset: null, until: '' } }
  return filters
}

export interface LogViewCriteria {
  source: LogSourceFilter
  levels: readonly LogLevelFilter[]
  keyword: string
  since?: number
  until?: number
}

/** 合并历史与实时记录（同一 id 以实时为准），在本地按来源、级别、关键词与时间过滤，按 id 升序。 */
export function visibleRecords(
  history: readonly LogRecord[],
  live: readonly LogRecord[],
  { source, levels, keyword, since, until }: LogViewCriteria,
): LogRecord[] {
  const byId = new Map<number, LogRecord>()
  for (const record of history) byId.set(record.id, record)
  for (const record of live) byId.set(record.id, record)
  const query = keyword.trim().toLocaleLowerCase()
  return [...byId.values()]
    .filter((record) => source === 'all' || record.source === source)
    .filter((record) => matchesLevels(record, levels))
    .filter((record) => !query || String(record.content ?? '').toLocaleLowerCase().includes(query))
    .filter((record) => since === undefined || (typeof record.ts === 'number' && record.ts >= since))
    .filter((record) => until === undefined || (typeof record.ts === 'number' && record.ts <= until))
    .sort((left, right) => left.id - right.id)
}
