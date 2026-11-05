import type {
  EventSource,
  Observation,
  SpecSummary,
  Trigger,
  TriggerMode,
  TriggerRule,
  TriggerRuleWrite,
} from './api'

/**
 * 事件规则页的展示规则与表单互转（M81 规格 §4.6）。
 *
 * 档位直接对应 `TriggerMode`：三段控件选「关闭」时写 `mode=off` 且 `enabled=false`，
 * 其余档位 `enabled=true`。覆盖行与内置完全一致时后端会删掉覆盖行（「恢复默认」）。
 */

export type TriggerTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'outline'

export const MODE_OPTIONS = [
  { value: 'off', label: '关闭' },
  { value: 'observe', label: '观察' },
  { value: 'auto', label: '自动' },
] as const satisfies readonly { value: TriggerMode; label: string }[]

export const MODE_LABELS: Record<TriggerMode, string> = {
  off: '关闭',
  observe: '观察',
  auto: '自动',
}

/** §4.6 逐字：两条诊断规则受总开关控制时的提示。 */
export const GATE_HINT = '受「自动诊断流水线与内核故障」设置控制'

export const SUGGEST_AUTO_LABEL = '可以改为自动'
export const SUGGEST_OBSERVE_LABEL = '建议改回观察'

const SOURCE_LABELS: Record<EventSource, string> = {
  web: '网页',
  im_telegram: 'Telegram',
  im_feishu: '飞书',
  im_dingtalk: '钉钉',
  im_qq: 'QQ',
  pipeline: '流水线',
  core: '内核',
  device: '设备',
  schedule: '定时',
  system: '系统',
  watchdog: 'Watchdog',
  goal: '目标',
}

/** 来源下拉的选项；顺序与 `EventSource` 一致，第一项是「任意」。 */
export const SOURCE_OPTIONS = (Object.keys(SOURCE_LABELS) as EventSource[]).map((value) => ({
  value,
  label: SOURCE_LABELS[value],
}))

/** 规则 id 的形状，与后端 `RULE_ID_PATTERN` 一致。 */
export const RULE_ID_PATTERN = /^[a-z0-9][a-z0-9-]{1,47}$/

const ROUTE_LABELS: Record<string, string> = {
  orchestrator: '交总控',
  ticket: '已建工单',
  observation: '观察记录',
  none: '只记录',
}

const ROUTE_TONES: Record<string, TriggerTone> = {
  orchestrator: 'primary',
  ticket: 'success',
  observation: 'warning',
  none: 'neutral',
}

const EVAL_LABELS: Record<string, string> = {
  pending: '研判中',
  accept: '该处理',
  stop: '建议停止',
  ignore: '忽略',
  unavailable: '研判不可用',
  skipped: '未研判',
}

const EVAL_TONES: Record<string, TriggerTone> = {
  pending: 'warning',
  accept: 'success',
  stop: 'warning',
  ignore: 'neutral',
  unavailable: 'danger',
  skipped: 'outline',
}

/** 来源为「任意」时返回「任意」；未知值原样显示。 */
export function sourceLabel(source: string | null | undefined): string {
  if (!source) return '任意'
  return SOURCE_LABELS[source as EventSource] ?? source
}

/** 规则卡片上三段控件当前选中的档位；停用的规则一律显示「关闭」。 */
export function ruleMode(rule: TriggerRule): TriggerMode {
  return rule.enabled ? rule.mode : 'off'
}

/** 规则来源徽章：内置 / 已修改（覆盖行）/ 自定义。 */
export function originBadge(rule: Pick<TriggerRule, 'origin' | 'modified'>): {
  label: string
  tone: TriggerTone
} {
  if (rule.origin === 'user') return { label: '自定义', tone: 'primary' }
  if (rule.origin === 'override' || rule.modified) return { label: '已修改', tone: 'warning' }
  return { label: '内置', tone: 'neutral' }
}

/** 事件去向徽章：不合法优先，其余按 `routed_to`。 */
export function routeBadge(trigger: Pick<Trigger, 'admission' | 'routed_to'>): {
  label: string
  tone: TriggerTone
} {
  if (trigger.admission === 'rejected') return { label: '不合法', tone: 'danger' }
  const route = trigger.routed_to ?? 'none'
  return { label: ROUTE_LABELS[route] ?? route, tone: ROUTE_TONES[route] ?? 'neutral' }
}

/** 研判状态徽章。 */
export function evalBadge(status: string): { label: string; tone: TriggerTone } {
  return { label: EVAL_LABELS[status] ?? status, tone: EVAL_TONES[status] ?? 'neutral' }
}

/** 建议标签：升档优先于降档（两者不会同时为真，这里只是定顺序）。 */
export function suggestionBadge(rule: Pick<TriggerRule, 'stats'>): {
  label: string
  tone: TriggerTone
} | null {
  if (rule.stats.suggest_auto) return { label: SUGGEST_AUTO_LABEL, tone: 'success' }
  if (rule.stats.suggest_observe) return { label: SUGGEST_OBSERVE_LABEL, tone: 'warning' }
  return null
}

const dateTimeFormat = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' })

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : dateTimeFormat.format(date)
}

/** 规则行的 30 天统计，一行。 */
export function formatStats(stats: TriggerRule['stats']): string {
  return [
    `30 天：观察 ${stats.observations_30d}`,
    `审阅 ${stats.reviewed_30d}（好 ${stats.good_30d} / 坏 ${stats.bad_30d}）`,
    `工单 ${stats.tickets_30d}（坏结局 ${stats.tickets_bad_30d}）`,
  ].join(' · ')
}

/** 执行代理显示名：取代理定义的 `title`，定义没加载时退回代码名。 */
export function workerTitle(worker: string | null | undefined, specs: readonly SpecSummary[]): string {
  if (!worker) return '未指定'
  return specs.find((spec) => spec.name === worker)?.title || worker
}

/** 观察记录里的「本来会做什么」：`decision` 是三段草稿 + 理由（§4.1）。 */
export interface ObservationDecision {
  kind: 'accept' | 'stop'
  worker: string | null
  title: string | null
  brief: string | null
  scope: string | null
  reason: string
}

export function observationDecision(observation: Pick<Observation, 'decision'>): ObservationDecision {
  const text = (value: unknown): string | null =>
    typeof value === 'string' && value.trim().length > 0 ? value : null
  return {
    kind: observation.decision.decision === 'stop' ? 'stop' : 'accept',
    worker: text(observation.decision.worker),
    title: text(observation.decision.title),
    brief: text(observation.decision.brief),
    scope: text(observation.decision.scope),
    reason: text(observation.decision.reason) ?? '',
  }
}

/** 编辑器的表单值；`conditions` 是 JSON 文本，提交前解析。 */
export interface RuleFormValues {
  id: string
  title: string
  source: EventSource | null
  kindPattern: string
  conditions: string
  mode: TriggerMode
  worker: string
  policy: string
  cooldown: string
  cron: string
}

export function emptyRuleForm(): RuleFormValues {
  return {
    id: '',
    title: '',
    source: null,
    kindPattern: '',
    conditions: '{}',
    mode: 'observe',
    worker: '',
    policy: '',
    cooldown: '0',
    cron: '',
  }
}

export function ruleFormValues(rule: TriggerRule): RuleFormValues {
  return {
    id: rule.id,
    title: rule.title,
    source: (rule.source as EventSource | null) ?? null,
    kindPattern: rule.kind_pattern,
    conditions: JSON.stringify(rule.conditions ?? {}, null, 2),
    mode: ruleMode(rule),
    worker: rule.worker ?? '',
    policy: rule.policy,
    cooldown: String(rule.cooldown_seconds),
    cron: rule.cron ?? '',
  }
}

/** 条件文本框 → 对象；不是 JSON 对象时返回错误文案。 */
export function parseConditions(text: string): { conditions?: Record<string, unknown>; error?: string } {
  if (text.trim().length === 0) return { conditions: {} }
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return { error: '条件必须是合法的 JSON' }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { error: '条件必须是 JSON 对象，例如 {"target": ["core", "resource"]}' }
  }
  return { conditions: parsed as Record<string, unknown> }
}

/** 表单值 → `PUT` 的完整生效内容（调用前已通过本地校验）。 */
export function buildRuleWrite(values: RuleFormValues): TriggerRuleWrite {
  return {
    title: values.title.trim(),
    source: values.source,
    kind_pattern: values.kindPattern.trim(),
    conditions: parseConditions(values.conditions).conditions ?? {},
    mode: values.mode,
    worker: values.worker.trim() ? values.worker.trim() : null,
    policy: values.policy,
    cooldown_seconds: Number(values.cooldown) || 0,
    cron: values.source === 'schedule' ? values.cron.trim() || null : null,
    enabled: values.mode !== 'off',
  }
}

/** 列表里切档位：写回规则的全部生效字段，只改档位。 */
export function ruleWrite(rule: TriggerRule, mode: TriggerMode): TriggerRuleWrite {
  return {
    title: rule.title,
    source: (rule.source as EventSource | null) ?? null,
    kind_pattern: rule.kind_pattern,
    conditions: rule.conditions,
    mode,
    worker: rule.worker,
    policy: rule.policy,
    cooldown_seconds: rule.cooldown_seconds,
    cron: rule.cron,
    enabled: mode !== 'off',
  }
}
