import { toolLabel } from '@/features/agent/format'

/** 代理定义页显示用的字段（列表项与详情都满足）。 */
export interface NamedSpec {
  name: string
  title: string
}

/** 列表按种类分组的固定顺序（§4.6.5）；空组由 `groupSpecs` 丢掉。 */
export const SPEC_KIND_GROUPS = [
  { kind: 'orchestrator', title: '总控' },
  { kind: 'worker', title: 'Worker' },
  { kind: 'subagent', title: '子代理' },
  { kind: 'background', title: '后台代理' },
] as const

export interface SpecGroup<T extends { kind: string }> {
  kind: string
  title: string
  specs: T[]
}

/** 把列表项按 `kind` 分成四组；没有定义的组不返回，调用方直接按顺序渲染。 */
export function groupSpecs<T extends { kind: string }>(specs: readonly T[]): SpecGroup<T>[] {
  return SPEC_KIND_GROUPS
    .map(({ kind, title }) => ({ kind, title, specs: specs.filter((spec) => spec.kind === kind) }))
    .filter((group) => group.specs.length > 0)
}

/** 种类中文名；服务端新增种类时原样显示。 */
export const KIND_LABELS: Record<string, string> = {
  orchestrator: '总控',
  worker: 'Worker',
  subagent: '子代理',
  background: '后台代理',
}

export function kindLabel(value: string): string {
  return KIND_LABELS[value] ?? value
}

/** 设备级别（§4.6.5）。 */
export const DEVICE_LABELS: Record<string, string> = {
  none: '不使用设备',
  read: '只读设备画面',
  exclusive: '独占设备',
}

export function deviceLabel(value: string): string {
  return DEVICE_LABELS[value] ?? value
}

/** 风险上限，与工单委托卡同一套文案（§4.6.5）。 */
export const RISK_CEILING_LABELS: Record<string, string> = {
  none: '只做无需确认的操作',
  consume: '最高到消耗类操作',
  destructive: '最高到破坏类操作',
}

export function riskCeilingLabel(value: string): string {
  return RISK_CEILING_LABELS[value] ?? value
}

/** 定义里的 title 是给人看的名字；缺了就退回代码名。 */
export function agentTitle(spec: NamedSpec): string {
  return spec.title.trim() || spec.name
}

export interface ModelRoleBinding {
  role: string
  profile: string | null
  model: string | null
}

/** 实际模型：`档案 {profile} · {model}`，角色没有绑定时写「未设置」。 */
export function actualModelLabel(roles: readonly ModelRoleBinding[], role: string): string {
  const binding = roles.find((item) => item.role === role)
  if (!binding || (!binding.profile && !binding.model)) return '未设置'
  return `档案 ${binding.profile ?? '—'} · ${binding.model ?? '—'}`
}

export { toolLabel }
