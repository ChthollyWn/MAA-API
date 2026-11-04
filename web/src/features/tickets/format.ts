import type { HumanAction } from '@/features/console/conversation/types'

export type { HumanAction } from '@/features/console/conversation/types'

/**
 * 工单页的数据视图与文案（M72 §4.6.5）。
 *
 * wire 字段名照 03 §6.1 与 04；适配函数只在这里做一次映射，组件全部用下面的
 * camelCase 字段。表里没有的取值原样显示，避免把新枚举写丢。
 */
export type TicketStatusValue = 'open' | 'waiting' | 'blocked' | 'done' | 'failed' | 'cancelled'

export interface TicketView {
  id: string
  title: string
  kind: string
  status: TicketStatusValue
  origin: string
  summary: string | null
  lastReportStatus: string | null
  humanAction: HumanAction | null
  createdAt: string | null
  updatedAt: string | null
  endedAt: string | null
}

export interface MandateView {
  scope: string
  strength: string
  source: string
  sender: string | null
  riskCeiling: string
  createdAt: string | null
}

export interface ReportView {
  id: number
  status: string
  summary: string
  humanAction: HumanAction | null
  details: Record<string, unknown> | null
  createdAt: string | null
}

export interface LeaseView {
  held: boolean
  ticketId: string | null
  holderAgent: string
  purpose: string
  expiresAt: string | null
  waiters: number
}

export interface RunNode {
  runId: string
  agent: string
  kind: string
  status: string
  finishReason: string | null
  steps: number
  promptTokens: number
  cachedTokens: number
  completionTokens: number
  costMicros: number
  currency: string | null
  startedAt: string | null
  finishedAt: string | null
  children: RunNode[]
}

export interface TicketDetailView {
  ticket: TicketView
  mandate: MandateView | null
  reports: ReportView[]
  workerSessionId: string | null
  turnRunning: boolean
}

export const TICKET_STATUS_LABELS: Record<TicketStatusValue, string> = {
  open: '进行中',
  waiting: '等你处理',
  blocked: '受阻',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

export const REPORT_STATUS_LABELS: Record<string, string> = {
  progress: '进展',
  waiting: '等你处理',
  ready: '已完成',
  blocked: '受阻',
  failed: '失败',
  dropped: '已放弃',
}

export const RUN_STATUS_LABELS: Record<string, string> = {
  running: '运行中',
  waiting_human: '等你处理',
  succeeded: '已完成',
  failed: '失败',
  interrupted: '已停止',
  cancelled: '已取消',
}

export const RUN_KIND_LABELS: Record<string, string> = {
  orchestrator_turn: '总控一轮',
  worker_turn: 'Worker 一轮',
  subagent: '子代理',
  trigger_eval: '事件研判',
  compaction: '摘要压缩',
  memory_extract: '记忆整理',
  title: '标题',
  watchdog_check: 'Watchdog 巡检',
  eval_case: '评测',
  legacy_turn: '旧会话一轮',
}

export const FINISH_REASON_LABELS: Record<string, string> = {
  stop: '正常结束',
  report_submitted: '已汇报',
  question_unanswered: '提问没有回答',
  confirmation_rejected: '确认被拒绝',
  confirmation_expired: '确认已过期',
  stuck_loop: '重复调用被叫停',
  max_steps: '达到步数上限',
  budget_exceeded: '超出成本预算',
  context_overflow: '上下文装不下',
  interrupted: '被停止',
  cancelled: '工单已取消',
  lease_preempted: '设备被接管',
  error: '出错',
  recovered_after_restart: '服务重启时停止',
}

export const ORIGIN_LABELS: Record<string, string> = {
  user: '你发起',
  trigger: '事件',
  goal: '目标',
  watchdog: 'Watchdog',
  system: '系统',
}

export const STRENGTH_LABELS: Record<string, string> = {
  explicit: '你明确要求的',
  inferred: '系统推断的',
}

export const RISK_LABELS: Record<string, string> = {
  none: '只做无需确认的操作',
  consume: '最高到消耗类操作',
  destructive: '最高到破坏类操作',
}

/** 工单的 Worker 中文名；定义里读不到时退回代码名（与指挥台的工单行同口径）。 */
export function workerTitle(kind: string, specTitles: ReadonlyMap<string, string>): string {
  if (!kind) return 'Worker'
  return specTitles.get(kind) ?? kind
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function stringValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function nullableString(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function numberValue(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

/** `HumanActionOut` → 视图；认不出类型时按 `null`（不是待人工处理的动作）。 */
function toHumanAction(value: unknown): HumanAction | null {
  if (!isRecord(value)) return null
  const type = stringValue(value.type)
  if (type !== 'confirmation' && type !== 'question' && type !== 'decision') return null
  return { type, prompt: stringValue(value.prompt), ref_id: nullableString(value.ref_id) }
}

/** `TicketSummary` → 视图；`id` 缺了取 `ticket_id`（旧 wire 的写法）。 */
export function toTicketView(raw: unknown): TicketView {
  const row = isRecord(raw) ? raw : {}
  return {
    id: stringValue(row.id) || stringValue(row.ticket_id),
    title: stringValue(row.title),
    kind: stringValue(row.kind),
    status: stringValue(row.status) as TicketStatusValue,
    origin: stringValue(row.origin),
    summary: nullableString(row.summary),
    lastReportStatus: nullableString(row.last_report_status),
    humanAction: toHumanAction(row.human_action),
    createdAt: nullableString(row.created_at),
    updatedAt: nullableString(row.updated_at),
    endedAt: nullableString(row.ended_at),
  }
}

export function toReportView(raw: unknown): ReportView {
  const row = isRecord(raw) ? raw : {}
  return {
    id: numberValue(row.id),
    status: stringValue(row.status),
    summary: stringValue(row.summary),
    humanAction: toHumanAction(row.human_action),
    details: isRecord(row.details) ? row.details : null,
    createdAt: nullableString(row.created_at),
  }
}

function toMandateView(raw: unknown): MandateView | null {
  if (!isRecord(raw)) return null
  return {
    scope: stringValue(raw.scope),
    strength: stringValue(raw.strength),
    source: stringValue(raw.source),
    sender: nullableString(raw.sender),
    riskCeiling: stringValue(raw.risk_ceiling),
    createdAt: nullableString(raw.created_at),
  }
}

/** `TicketDetail` → 视图；汇报列表缺 `reports` 时读 `recent_reports`（§0 #4）。 */
export function toTicketDetailView(raw: unknown): TicketDetailView {
  const row = isRecord(raw) ? raw : {}
  const reports = Array.isArray(row.reports)
    ? row.reports
    : Array.isArray(row.recent_reports)
      ? row.recent_reports
      : []
  return {
    ticket: toTicketView(row.ticket),
    mandate: toMandateView(row.mandate),
    reports: reports.map(toReportView),
    workerSessionId: nullableString(row.worker_session_id),
    turnRunning: row.turn_running === true,
  }
}

/** `GET /api/agent/device-lease` 的 `{held, lease?, waiters}` → 视图。 */
export function toLeaseView(raw: unknown): LeaseView {
  const row = isRecord(raw) ? raw : {}
  const lease = isRecord(row.lease) ? row.lease : null
  return {
    held: row.held === true,
    ticketId: lease ? nullableString(lease.ticket_id) : null,
    holderAgent: lease ? stringValue(lease.holder_agent) : '',
    purpose: lease ? stringValue(lease.purpose) : '',
    expiresAt: lease ? nullableString(lease.expires_at) : null,
    waiters: numberValue(row.waiters),
  }
}

/** 运行节点；`run_id` 缺了取 `id`、`agent_name` 缺了取 `agent`，children 递归。 */
export function toRunNode(raw: unknown): RunNode {
  const row = isRecord(raw) ? raw : {}
  return {
    runId: stringValue(row.run_id) || stringValue(row.id),
    agent: stringValue(row.agent_name) || stringValue(row.agent),
    kind: stringValue(row.kind),
    status: stringValue(row.status),
    finishReason: nullableString(row.finish_reason),
    steps: numberValue(row.steps),
    promptTokens: numberValue(row.prompt_tokens),
    cachedTokens: numberValue(row.cached_tokens),
    completionTokens: numberValue(row.completion_tokens),
    costMicros: numberValue(row.cost_micros),
    currency: nullableString(row.currency),
    startedAt: nullableString(row.started_at),
    finishedAt: nullableString(row.finished_at),
    children: Array.isArray(row.children) ? row.children.map(toRunNode) : [],
  }
}

/** 微单位成本 → 金额（§4.6.5）：CNY 写 ¥、其他写币种代码，0 写「—」。 */
export function formatCost(micros: number, currency: string | null): string {
  if (!Number.isFinite(micros) || micros <= 0) return '—'
  const amount = micros / 1_000_000
  const text = amount.toFixed(4).replace(/0+$/, '').replace(/\.$/, '') || '0'
  const code = (currency ?? '').toUpperCase()
  if (code === 'CNY') return `¥${text}`
  return code ? `${text} ${code}` : text
}

const TWO_DIGITS = (value: number) => String(value).padStart(2, '0')

/** 运行时长；没结束按 `now` 算（可注入，便于测试）。 */
export function formatDuration(startedAt: string | null, finishedAt: string | null, now: number = Date.now()): string {
  if (!startedAt) return '—'
  const started = Date.parse(startedAt)
  const finished = finishedAt ? Date.parse(finishedAt) : now
  if (!Number.isFinite(started) || !Number.isFinite(finished) || finished < started) return '—'
  const totalSeconds = Math.floor((finished - started) / 1000)
  if (totalSeconds < 60) return `${totalSeconds} 秒`
  const totalMinutes = Math.floor(totalSeconds / 60)
  if (totalMinutes < 60) return `${totalMinutes} 分 ${TWO_DIGITS(totalSeconds % 60)} 秒`
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  return minutes ? `${hours} 小时 ${TWO_DIGITS(minutes)} 分` : `${hours} 小时`
}

const dateTimeFormat = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false,
})

/** 页面上的时间；读不出时原样返回。 */
export function formatDateTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : dateTimeFormat.format(date)
}
