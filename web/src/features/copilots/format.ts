import { toApiError } from '@/api/errors'
import type { CopilotAsset, CopilotAttempt, CopilotJudge, CopilotOutcome, CopilotSource } from './api'

/** 作业页的纯格式化：来源 / 结果 / 判定方的中文文案、版本链、时间与错误文案。 */

/** 就地显示的错误文案；连不上服务时给中文说明，而不是浏览器的英文报错。 */
export function errorText(error: unknown): string {
  const failure = toApiError(error)
  if (failure.status === undefined && !failure.code) return '无法连接服务，请检查网络后重试。'
  return failure.message
}

export const SOURCE_LABELS: Record<CopilotSource, string> = {
  user: '用户上传',
  site: '作业站',
  ai: 'AI 生成',
}

export const SOURCE_HINTS: Record<CopilotSource, string> = {
  user: '你自己上传或让 Agent 保存的作业',
  site: '从作业站导入的副本',
  ai: 'Agent 研究这一关时自己设计的作业',
}

export function sourceLabel(value: string | null | undefined): string {
  if (value && value in SOURCE_LABELS) return SOURCE_LABELS[value as CopilotSource]
  return value?.trim() || '未知来源'
}

/** 来源筛选的选项（§9.10「来源（用户上传 / 作业站 / AI 生成）」）。 */
export const SOURCE_FILTERS: readonly { value: CopilotSource; label: string }[] = [
  { value: 'user', label: SOURCE_LABELS.user },
  { value: 'site', label: SOURCE_LABELS.site },
  { value: 'ai', label: SOURCE_LABELS.ai },
]

export const OUTCOME_LABELS: Record<CopilotOutcome, string> = {
  three_star: '三星',
  two_star: '二星',
  clear: '通关',
  failed: '行动失败',
  support_unavailable: '助战没借到',
  sanity_insufficient: '理智不足',
  not_started: '没能开始',
  unknown: '无法判定',
  error: '出错',
  interrupted: '已中断',
}

export function outcomeLabel(value: string | null | undefined): string {
  if (value && value in OUTCOME_LABELS) return OUTCOME_LABELS[value as CopilotOutcome]
  return value?.trim() || '未知结果'
}

export type OutcomeTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'outline'

/** 结果标签的色调：成功绿、通关蓝、失败红、待判定黄。 */
export function outcomeTone(value: string | null | undefined): OutcomeTone {
  switch (value) {
    case 'three_star':
      return 'success'
    case 'two_star':
    case 'clear':
      return 'primary'
    case 'failed':
    case 'error':
      return 'danger'
    case 'support_unavailable':
    case 'unknown':
    case 'sanity_insufficient':
      return 'warning'
    default:
      return 'neutral'
  }
}

export const JUDGE_LABELS: Record<CopilotJudge, string> = {
  core: '内核判定',
  model: '模型判定',
}

/** 判定方文案；`model` 就是规格 §9.7 要求标注的「模型判定」。 */
export function judgeLabel(value: string | null | undefined): string {
  if (value && value in JUDGE_LABELS) return JUDGE_LABELS[value as CopilotJudge]
  return value?.trim() || JUDGE_LABELS.core
}

/** 一次尝试的完整描述：三星 · 内核判定 · 10-10 12:30。 */
export function attemptText(attempt: CopilotAttempt): string {
  const parts = [outcomeLabel(attempt.outcome)]
  if (attempt.judgedBy === 'model') parts.push(judgeLabel(attempt.judgedBy))
  const time = shortTime(attempt.at)
  if (time) parts.push(time)
  return parts.join(' · ')
}

const TIME_FORMAT = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' })

/** 精确到分钟的本地时间；解析不了就原样显示，空值显示「—」。 */
export function shortTime(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : TIME_FORMAT.format(date)
}

export function createdText(value: string | null | undefined): string {
  return shortTime(value) || '时间未知'
}

/** 最后一条尝试的结果摘要；没有尝试时给 null，页面显示「还没有跑过」。 */
export function lastAttemptText(attempts: readonly CopilotAttempt[]): string | null {
  const last = attempts.at(-1)
  return last ? `${outcomeLabel(last.outcome)}（${judgeLabel(last.judgedBy)}）` : null
}

/** 关卡：草稿的 `meta.stage_id` 就是作业 JSON 的 `stage_name`（例如 `1-7`）。 */
export function stageText(asset: Pick<CopilotAsset, 'meta'>): string {
  return asset.meta.stageId ?? '关卡未知'
}

/** 标题：作业自己的标题优先，没有就用资源名（上传时的名字）。 */
export function titleText(asset: Pick<CopilotAsset, 'name' | 'meta'>): string {
  return asset.meta.title ?? asset.name
}

/** 版本链里一步的渲染输入。 */
export interface VersionChainEntry {
  id: string
  title: string
  /** 当前正在看的这一版。 */
  current: boolean
  /** 父版本不在已取回的列表里（分页之外或被删了），只显示 id 后 6 位。 */
  missing: boolean
}

/**
 * 版本链（§9.10「版本链（父子关系）」）：沿 `meta.parent_id` 一路往上，再反转成
 * 「最早 → 当前」。父版本不在已取回的列表里时保留一个 `missing` 占位，
 * 不让链看起来像是从这一版开始的。带环时（数据不该出现，但防御）就地截断。
 */
export function buildVersionChain(
  asset: CopilotAsset,
  lookup: ReadonlyMap<string, CopilotAsset>,
  limit = 8,
): VersionChainEntry[] {
  const chain: VersionChainEntry[] = [{ id: asset.id, title: titleText(asset), current: true, missing: false }]
  const seen = new Set([asset.id])
  let parentId = asset.meta.parentId
  while (parentId && !seen.has(parentId) && chain.length < limit) {
    seen.add(parentId)
    const parent = lookup.get(parentId)
    chain.push({
      id: parentId,
      title: parent ? titleText(parent) : `版本 ${shortId(parentId)}`,
      current: false,
      missing: !parent,
    })
    parentId = parent?.meta.parentId ?? null
  }
  return chain.reverse()
}

/** 长 id 的后 6 位，用于「父版本不在列表里」这类占位。 */
export function shortId(value: string): string {
  return value.length > 6 ? `…${value.slice(-6)}` : value
}

/**
 * 作业站来源的链接（§9.10「作业站来源标签与链接」）。
 *
 * 链接由作业站客户端给出（§9.4：格式在实测后写进结果里，查不到就不给链接），
 * 这里只做白名单校验：只认 https 与作业站域名，别的一律不给可点链接。
 */
export function siteUrl(value: string | null | undefined): string | null {
  if (!value?.trim()) return null
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:') return null
    const host = url.hostname.toLowerCase()
    const allowed = host === 'prts.maa.plus' || host.endsWith('.prts.maa.plus') || host === 'prts.plus'
    return allowed ? url.toString() : null
  } catch {
    return null
  }
}

/** 确认卡片上的成功标准（§9.6）。 */
export const SUCCESS_LABELS: Record<string, string> = {
  three_star: '必须三星',
  clear: '通关即可',
}

export function successLabel(value: string | null | undefined): string {
  return value ? SUCCESS_LABELS[value] ?? value : SUCCESS_LABELS.three_star
}

/** 确认卡片上的助战补位（§9.6、§9.1 的 Q42）。 */
export const SUPPORT_LABELS: Record<string, string> = {
  fill_one: '允许助战补位',
  none: '不用助战',
}

export function supportLabel(value: string | null | undefined): string {
  return value ? SUPPORT_LABELS[value] ?? value : SUPPORT_LABELS.fill_one
}

/** 授权有效期的只读说明（§9.6）：卡片上不给改，只讲清怎么算。 */
export function windowText(minutes: number | null): string {
  const span = minutes && minutes > 0 ? `${minutes} 分钟` : '服务端配置的时长'
  return `有效期 ${span}，从批准起算；只在开始下一次尝试时检查，已经开始的尝试不会被打断。`
}
