/** 模型角色；界面不出现代码名。 */
export const ROLE_LABELS: Record<string, string> = {
  orchestrator: '总控',
  worker: 'Worker',
  subagent: '子代理',
  summarizer: '摘要',
  title: '标题',
  trigger_eval: '事件研判',
  memory: '记忆',
  vision: '视觉',
  embedding: '向量',
}

export type CostRange = 'today' | '7d' | '30d'
export type CostGroupBy = 'day' | 'agent' | 'model' | 'role'

export interface CostBounds {
  since: string
  until: string
}

/** 本地零点；账单按本地自然日分组，换算成 ISO 带 Z 交给接口。 */
export function localMidnight(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate())
}

/** 范围 → [since, until)：今天从本地零点到现在，近 7/30 天各自包含今天。 */
export function costRange(range: CostRange, now: Date = new Date()): CostBounds {
  const days = range === 'today' ? 1 : range === '7d' ? 7 : 30
  const start = localMidnight(now)
  start.setDate(start.getDate() - (days - 1))
  return { since: start.toISOString(), until: now.toISOString() }
}

/** 「今日」卡片的 since：本地零点。 */
export function todaySince(now: Date = new Date()): string {
  return localMidnight(now).toISOString()
}

export function roleLabel(value: string): string {
  return ROLE_LABELS[value] ?? '未知角色'
}

/** 汇总的缓存命中率：命中 token / 输入 token；没有输入时算 0。 */
export function cacheHitRatio(groups: readonly { cached_tokens: number; prompt_tokens: number }[]): number {
  let prompt = 0
  let cached = 0
  for (const group of groups) {
    prompt += Math.max(group.prompt_tokens, 0)
    cached += Math.max(group.cached_tokens, 0)
  }
  return prompt > 0 ? Math.min(cached / prompt, 1) : 0
}

export function formatRatio(ratio: number): string {
  if (!Number.isFinite(ratio)) return '0%'
  const percent = Math.min(Math.max(ratio, 0), 1) * 100
  return `${Number.isInteger(percent) ? percent.toFixed(0) : percent.toFixed(1)}%`
}

export function totalCalls(groups: readonly { calls: number }[]): number {
  return groups.reduce((sum, group) => sum + Math.max(group.calls, 0), 0)
}
