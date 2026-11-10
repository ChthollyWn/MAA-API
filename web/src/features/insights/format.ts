import type { StatusTone } from '@/components/ui/badge'
import { formatMicros } from '@/features/runs/format'
import type { components } from '@/types/api'

/**
 * 能力效果看板的展示口径（M99 §4.6）。
 *
 * 比率一律按 1 位小数的百分比展示；成本复用运行页的微单位格式，卡片上没有币种
 * 信息，所以主值只写金额、分币种明细由服务端的 secondary 给出。
 */

export type MetricId = components['schemas']['MetricId']
export type EvidenceGrade = components['schemas']['EvidenceGrade']
export type FeedbackReason = components['schemas']['FeedbackReason']

/** 证据徽章：与目录里的证据等级一一对应，界面只写这四个词。 */
export const EVIDENCE_LABELS: Record<EvidenceGrade, string> = {
  measured: '实测',
  self_reported: '自述',
  proxy: '代理',
  unavailable: '暂无',
}

/** 没有数据时每个指标的获取方式（M99 §4.6 第 3、4 项）。 */
export const METRIC_EMPTY_HINTS: Record<MetricId, string> = {
  accuracy: '先跑一次 live 评测：scripts/agent_eval.py --suite capability --mode live --publish',
  hallucination_rate: '先跑一次 live 评测：scripts/agent_eval.py --suite capability --mode live --publish',
  safety_rate: '先跑一次 live 评测：scripts/agent_eval.py --suite capability --mode live --publish',
  task_completion: '有工单进入终态后显示',
  conversation_efficiency: '完成的工单有用户消息后显示',
  cost: '运行产生模型调用后显示',
  user_satisfaction: '在运行详情页评价一次后用',
}

/** 反馈原因（M99 §4.5.4）；未知取值不把代码名摆到界面上。 */
export const FEEDBACK_REASONS: Record<FeedbackReason, string> = {
  inaccurate: '不准确',
  incomplete: '不完整',
  slow: '太慢',
  unsafe: '不安全',
  other: '其他',
}

const numberFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 })

/** 比率 → 百分比，保留 1 位小数。 */
export function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

/** 计数、轮次等普通数值；整数不带小数点。 */
export function formatCount(value: number): string {
  return numberFormat.format(value)
}

/** 图表轴/系列的单位名称。 */
export function unitLabel(unit: string): string {
  switch (unit) {
    case 'ratio':
      return '比例'
    case 'micros':
      return '成本'
    case 'turns':
      return '轮'
    case 'count':
      return '个'
    case 'percent':
      return '%'
    default:
      return unit
  }
}

/** 指标卡主值：无数据写「暂无数据」，不拿 0 冒充。 */
export function formatMetricValue(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '暂无数据'
  switch (unit) {
    case 'ratio':
      return formatPercent(value)
    case 'micros':
      return formatMicros(value, null)
    case 'turns':
      return `${formatCount(value)} 轮`
    case 'count':
      return formatCount(value)
    case 'percent':
      return `${formatCount(value)}%`
    default:
      return formatCount(value)
  }
}

/** secondary 的一行值。 */
export function formatSubMetricValue(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '暂无'
  return formatMetricValue(value, unit)
}

/** 与上一同长窗口的差；比率用百分点（pp），其余沿用单位的格式。 */
export function formatDelta(delta: number, unit: string): string {
  const sign = delta > 0 ? '+' : delta < 0 ? '−' : '±'
  const absolute = Math.abs(delta)
  if (unit === 'ratio') return `${sign}${(absolute * 100).toFixed(1)}pp`
  if (unit === 'micros') return `${sign}${formatMicros(absolute, null)}`
  if (unit === 'turns') return `${sign}${formatCount(absolute)} 轮`
  if (unit === 'percent') return `${sign}${formatCount(absolute)}%`
  return `${sign}${formatCount(absolute)}`
}

/** Wilson 95% 区间；只在上下界都有值时显示。 */
export function formatCi(low: number, high: number, unit: string): string {
  if (unit === 'micros') return `95% CI ${formatMicros(low, null)}–${formatMicros(high, null)}`
  if (unit === 'turns') return `95% CI ${formatCount(low)}–${formatCount(high)} 轮`
  const range = unit === 'ratio'
    ? `${formatPercent(low)}–${formatPercent(high)}`
    : `${formatCount(low)}–${formatCount(high)}`
  return `95% CI ${range}`
}

/** 评测套件、模式与状态的中文写法。 */
export const EVAL_SUITE_LABELS: Record<string, string> = {
  regression: '回归',
  capability: '能力',
}

export const EVAL_MODE_LABELS: Record<string, string> = {
  replay: '回放',
  record: '录制',
  live: '实跑',
}

export function evalSuiteLabel(value: string): string {
  return EVAL_SUITE_LABELS[value] ?? '未知套件'
}

export function evalModeLabel(value: string): string {
  return EVAL_MODE_LABELS[value] ?? '未知模式'
}

export interface EvalRunCounts {
  total: number
  passed: number
  failed: number
  error: number
  skipped: number
}

/** 评测行的状态：按计数给一个能直接看的结论。 */
export function evalRunStatus(run: EvalRunCounts): { label: string; tone: StatusTone } {
  if (run.error > 0) return { label: '有错误', tone: 'danger' }
  if (run.failed > 0) return { label: '有失败', tone: 'warning' }
  if (run.skipped > 0) return { label: '有跳过', tone: 'neutral' }
  if (run.total === 0) return { label: '没有用例', tone: 'neutral' }
  return { label: '全部通过', tone: 'success' }
}

/** 反馈原因的中文标签。 */
export function feedbackReasonLabel(value: string): string {
  return FEEDBACK_REASONS[value as FeedbackReason] ?? '其他'
}

/** 1–5 分 → 星串（实心星 + 空心星），配 `aria-label` 给读屏。 */
export function ratingStars(rating: number): string {
  const filled = Math.min(Math.max(Math.round(rating), 0), 5)
  return `${'★'.repeat(filled)}${'☆'.repeat(5 - filled)}`
}
