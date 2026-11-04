import type { StatusTone } from '@/components/ui/badge'
import type { SpanOut } from './api'

/** 运行种类；界面不出现代码名。 */
export const RUN_KIND_LABELS: Record<string, string> = {
  orchestrator_turn: '总控一轮',
  worker_turn: 'Worker 一轮',
  subagent: '子代理',
  trigger_eval: '事件研判',
  compaction: '摘要压缩',
  memory_extract: '记忆整理',
  title: '标题生成',
  watchdog_check: 'Watchdog 巡检',
  eval_case: '评测用例',
  legacy_turn: '旧会话一轮',
}

/** 运行状态（11 §11 的写法）。 */
export const RUN_STATUS_LABELS: Record<string, string> = {
  running: '运行中',
  waiting_human: '等你处理',
  succeeded: '已完成',
  failed: '失败',
  interrupted: '已停止',
  cancelled: '已取消',
}

/** 终止原因；未知取值不把代码名摆到界面上。 */
export const FINISH_REASON_LABELS: Record<string, string> = {
  stop: '模型给出最终回答',
  report_submitted: '提交了汇报',
  question_unanswered: '提问超时或被跳过',
  confirmation_rejected: '确认被拒',
  confirmation_expired: '确认超时',
  stuck_loop: '连续重复同样的调用',
  max_steps: '达到步数上限',
  budget_exceeded: '超出成本预算',
  context_overflow: '上下文装不下',
  interrupted: '用户或 Watchdog 中断',
  cancelled: '工单已取消',
  lease_preempted: '设备被用户接管',
  error: '运行出错',
  recovered_after_restart: '服务重启时中断',
  confirmation_resolved: '确认已处理',
  turn_error: '本轮出错',
  incident_error: '诊断出错',
  incident_context_update: '诊断上下文更新',
}

/** span 种类的中文名。 */
export const SPAN_KIND_LABELS: Record<string, string> = {
  llm: '模型调用',
  tool: '工具调用',
  retrieval: '检索',
  policy: '策略',
  confirmation: '确认等待',
  memory: '记忆',
  compaction: '摘要压缩',
  lease: '设备租约',
  delegate: '委派',
  other: '运行',
}

export const SPAN_STATUS_LABELS: Record<string, string> = {
  ok: '正常',
  error: '出错',
}

function attributeText(span: Pick<SpanOut, 'attributes'>, key: string): string | null {
  const value = span.attributes?.[key]
  return typeof value === 'string' && value ? value : null
}

/**
 * 瀑布图与详情抽屉的 span 标签。名字里带代理名时换成定义里的 title；
 * 其余 span 只写种类中文名。
 */
export function spanLabel(span: Pick<SpanOut, 'name' | 'kind' | 'attributes'>, titles: ReadonlyMap<string, string>): string {
  if (span.name === 'agent.run') {
    return `运行 · ${agentTitle(attributeText(span, 'maa.agent.name'), titles)}`
  }
  if (span.name === 'llm.chat') {
    const model = attributeText(span, 'gen_ai.response.model') ?? attributeText(span, 'gen_ai.request.model')
    return `模型 · ${model ?? '未知模型'}`
  }
  if (span.name === 'tool.execute') {
    return `工具 · ${attributeText(span, 'gen_ai.tool.name') ?? '未知工具'}`
  }
  if (span.name === 'delegate.subagent') {
    return `委派 · ${agentTitle(attributeText(span, 'maa.subagent.name'), titles)}`
  }
  if (span.name === 'confirmation.wait') return '等待确认'
  return spanKindLabel(span.kind)
}

function labelOf(labels: Readonly<Record<string, string>>, value: string | null | undefined, fallback: string): string {
  if (!value) return fallback
  return labels[value] ?? fallback
}

export function runKindLabel(value: string): string {
  return labelOf(RUN_KIND_LABELS, value, '未知种类')
}

export function runStatusLabel(value: string): string {
  return labelOf(RUN_STATUS_LABELS, value, '未知状态')
}

export function finishReasonLabel(value: string | null | undefined): string {
  return labelOf(FINISH_REASON_LABELS, value, value ? '未知原因' : '—')
}

export function spanKindLabel(value: string): string {
  return labelOf(SPAN_KIND_LABELS, value, '其他')
}

export function spanStatusLabel(value: string): string {
  return labelOf(SPAN_STATUS_LABELS, value, '未知状态')
}

export function runStatusTone(value: string): StatusTone {
  if (value === 'running') return 'primary'
  if (value === 'waiting_human') return 'warning'
  if (value === 'succeeded') return 'success'
  if (value === 'failed') return 'danger'
  return 'neutral'
}

/** 代理一律显示定义里的 title；查不到时写「未知代理」。 */
export function agentTitle(name: string | null | undefined, titles: ReadonlyMap<string, string>): string {
  if (!name) return '未知代理'
  return titles.get(name) ?? '未知代理'
}

const CURRENCY_SYMBOLS: Record<string, string> = { CNY: '¥', USD: '$' }

/** 微单位成本 → 带币种符号的金额；CNY 写 ¥、USD 写 $，其他写币种代码，最多 4 位小数。 */
export function formatMicros(micros: number, currency: string | null | undefined): string {
  const amount = Number.isFinite(micros) ? Math.max(micros, 0) / 1_000_000 : 0
  const text = amount.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')
  const code = (currency ?? '').toUpperCase()
  if (!code) return text
  if (CURRENCY_SYMBOLS[code]) return `${CURRENCY_SYMBOLS[code]}${text}`
  return `${code} ${text}`
}

export function formatTokens(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '0'
  return new Intl.NumberFormat('zh-CN').format(Math.max(0, Math.round(value)))
}

/** 毫秒时长；不写「None」，用中文单位。 */
export function formatDuration(ms: number | null | undefined): string {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) return '—'
  if (ms < 1_000) return `${Math.round(ms)} ms`
  const seconds = ms / 1_000
  if (seconds < 60) return `${seconds >= 10 ? seconds.toFixed(0) : seconds.toFixed(1)} 秒`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  if (minutes < 60) return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`
  const hours = Math.floor(minutes / 60)
  const restMinutes = minutes % 60
  return restMinutes ? `${hours} 小时 ${restMinutes} 分` : `${hours} 小时`
}

const dateTimeFormat = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
})

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : dateTimeFormat.format(date)
}

/** 定义版本哈希只露前 8 位。 */
export function formatSpecVersion(value: string | null | undefined): string {
  return value ? value.slice(0, 8) : '—'
}

export function runDurationMs(run: { started_at: string | null; finished_at: string | null }): number | null {
  if (!run.started_at || !run.finished_at) return null
  const started = Date.parse(run.started_at)
  const finished = Date.parse(run.finished_at)
  if (!Number.isFinite(started) || !Number.isFinite(finished) || finished < started) return null
  return finished - started
}
