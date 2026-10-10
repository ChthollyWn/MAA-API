import { toApiError } from '@/api/errors'
import type { AccountSummaryEntry, FieldSource, OperatorDiff, OperatorRow } from './api'

/** 账号数据页的纯格式化：职业、精英化、专精与模组摘要、字段来源、识别时间与过期文案。 */

/** 就地显示的错误文案；连不上服务时给中文说明，而不是浏览器的英文报错。 */
export function errorText(error: unknown): string {
  const failure = toApiError(error)
  if (failure.status === undefined && !failure.code) return '无法连接服务，请检查网络后重试。'
  return failure.message
}

export const MAX_RARITY = 6

/** 本机职业名（WARRIOR）→ 中文（近卫）；REST 一般直接给中文，两边都认。 */
const PROFESSION_CN: Record<string, string> = {
  PIONEER: '先锋',
  WARRIOR: '近卫',
  SNIPER: '狙击',
  TANK: '重装',
  MEDIC: '医疗',
  SUPPORT: '辅助',
  CASTER: '术师',
  SPECIAL: '特种',
}

/** 筛选下拉的选项（§7.5「按职业筛选」）；顺序照游戏里的职业表。 */
export const PROFESSIONS: readonly string[] = ['先锋', '近卫', '狙击', '重装', '医疗', '辅助', '术师', '特种']

export const SOURCE_LABELS: Record<FieldSource, string> = {
  recognition: '识别',
  manual: '手动',
  agent: 'Agent',
}

export const SOURCE_HINTS: Record<FieldSource, string> = {
  recognition: '由干员识别写入',
  manual: '在账号数据页手动改过',
  agent: '由 Agent 改过',
}

export function sourceLabel(value: string | undefined | null): string | null {
  if (!value) return null
  return SOURCE_LABELS[value as FieldSource] ?? value
}

export function rarityLabel(rarity: number): string {
  return Number.isFinite(rarity) && rarity > 0 ? `${rarity}★` : '—'
}

export function professionLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return PROFESSION_CN[value.toUpperCase()] ?? value
}

export function eliteLabel(elite: number): string {
  if (elite === 0) return '精零'
  if (elite === 1) return '精一'
  if (elite === 2) return '精二'
  return `精英化 ${elite}`
}

/** 专精列：`S1 3 · S2 1`；没有专精显示「—」。 */
export function masteryText(masteries: Record<string, number> | undefined): string {
  const parts = ['1', '2', '3']
    .map((slot) => [slot, Number(masteries?.[slot] ?? 0)] as const)
    .filter(([, level]) => level > 0)
    .map(([slot, level]) => `S${slot} ${level}`)
  return parts.length ? parts.join(' · ') : '—'
}

/** 模组列：`X2 · Y1`；没有模组显示「—」。 */
export function moduleText(modules: Record<string, number> | undefined): string {
  const parts = Object.entries(modules ?? {})
    .filter(([, level]) => Number(level) > 0)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([type, level]) => `${type}${level}`)
  return parts.length ? parts.join(' · ') : '—'
}

/** 一行的字段来源集合（去重、按识别→手动→Agent 排序），用于行内的来源标记。 */
export function rowSources(row: OperatorRow): FieldSource[] {
  const order: FieldSource[] = ['recognition', 'manual', 'agent']
  const seen = new Set(Object.values(row.field_sources ?? {}))
  return order.filter((source) => seen.has(source))
}

/** 每个字段各自的来源，用于编辑抽屉里的逐字段标注。 */
export function fieldSource(row: OperatorRow, field: string): FieldSource | null {
  const value = row.field_sources?.[field]
  return value ?? null
}

const FIELD_LABELS: Record<string, string> = {
  own: '是否拥有',
  elite: '精英化',
  level: '等级',
  potential: '潜能',
  skill_level: '技能等级',
  masteries: '专精',
  modules: '模组',
}

export function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field
}

/** 差异条里把值渲染成人话：布尔转「已拥有/未拥有」，精英化转「精二」。 */
export function diffValueText(field: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (field === 'own' && typeof value === 'boolean') return value ? '已拥有' : '未拥有'
  if (field === 'elite') return eliteLabel(Number(value))
  return String(value)
}

export function diffText(diff: OperatorDiff): string {
  return `${fieldLabel(diff.field)}：手动 ${diffValueText(diff.field, diff.manual_value)} → 识别 ${diffValueText(diff.field, diff.recognized_value)}`
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/** 本地时区的 `YYYY-MM-DD HH:mm`；无效或缺失返回 null（调用方显示「—」）。 */
export function formatDateTime(value: string | null | undefined): string | null {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/**
 * 识别时间与过期状态（规格 §7.8 的口径）：
 * 从未识别 → 「尚未识别」；否则「识别于 …（N 天前）」，过期再补一句建议重新识别。
 */
export function recognitionText(entry: AccountSummaryEntry): string {
  if (!entry.recognized) return '尚未识别'
  const at = formatDateTime(entry.recognized_at) ?? '未知时间'
  const days = entry.stale_days
  const ago = days === null || days === undefined ? '' : days <= 0 ? '（今天）' : `（${days} 天前）`
  const stale = entry.stale ? `，已超过 ${entry.stale_after_days} 天` : ''
  return `识别于 ${at}${ago}${stale}`
}

/** 概览行：「共 312 名（精二 85）」；没识别过就不报数。 */
export function recognizedCountText(entry: AccountSummaryEntry, unit: string): string | null {
  if (!entry.recognized) return null
  const total = `共 ${entry.total} ${unit}`
  if (!entry.elite2) return total
  return `${total}（精二 ${entry.elite2}）`
}

export function staleBadgeText(entry: AccountSummaryEntry): string | null {
  if (!entry.recognized) return '未识别'
  return entry.stale ? '已过期' : '已同步'
}
