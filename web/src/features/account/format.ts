import { toApiError } from '@/api/errors'
import type { AccountSummaryEntry, OperatorRow } from './api'

/** 账号数据页的纯格式化：职业、精英化、专精与模组摘要、字段来源、同步时间与过期文案。 */

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

/** 本机仓库分类码（`item_index.json` 的 `classifyType`）→ 中文展示名；已是中文则原样保留。 */
const DEPOT_CATEGORY_CN: Record<string, string> = {
  MATERIAL: '养成材料',
  NORMAL: '基础物品',
  CONSUME: '消耗品',
  MEMENTO: '纪念品',
  NONE: '其他',
}

export function depotCategoryLabel(category: string | null | undefined): string {
  const trimmed = category?.trim()
  if (!trimmed) return '未分类'
  return DEPOT_CATEGORY_CN[trimmed.toUpperCase()] ?? trimmed
}

/** 筛选下拉的选项（§7.5「按职业筛选」）；顺序照游戏里的职业表。 */
export const PROFESSIONS: readonly string[] = ['先锋', '近卫', '狙击', '重装', '医疗', '辅助', '术师', '特种']

/**
 * 字段来源的展示名（M47 §8.70）：干员数据现在只有一个写入者，所以卡面上实际只会出现
 * 「森空岛」。其余三个取值留着是为了老库/老响应不至于渲染成裸英文。
 */
export const SOURCE_LABELS: Record<string, string> = {
  skland: '森空岛',
  recognition: '识别',
  manual: '手动',
  agent: 'Agent',
}

export const SOURCE_HINTS: Record<string, string> = {
  skland: '由森空岛账号数据同步写入',
  recognition: '由内核干员识别写入（该通道 M47 已下线）',
  manual: '在账号数据页手动改过（该通道 M47 已下线）',
  agent: '由 Agent 改过（该通道 M47 已下线）',
}

export function sourceLabel(value: string | undefined | null): string | null {
  if (!value) return null
  return SOURCE_LABELS[value] ?? value
}

export function rarityLabel(rarity: number): string {
  return Number.isFinite(rarity) && rarity > 0 ? `${rarity}★` : '—'
}

export function professionLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return PROFESSION_CN[value.toUpperCase()] ?? value
}

/**
 * 未识别（M35）：练度六列读到 `null` 时页面写的词。M47 起干员数据的唯一来源是森空岛
 * 同步，某一项没给或越界就是「未识别」——不要折算成 0 级 / 技能 1 级 / 无专精。
 */
export const UNKNOWN_TEXT = '未识别'

/** 精英化：`null` / `undefined` ＝ 未识别，不许折算成「精零」。 */
export function eliteLabel(elite: number | null | undefined): string {
  if (elite === null || elite === undefined) return UNKNOWN_TEXT
  if (elite === 0) return '精零'
  if (elite === 1) return '精一'
  if (elite === 2) return '精二'
  return `精英化 ${elite}`
}

/**
 * 数值型练度列（等级 / 潜能 / 技能等级）：有值写数字，`null` / `undefined` 写「未识别」。
 * 这三列以前直接渲染数字，得先过这一层，页面上才不会出现空白或裸 `null`。
 */
export function trainingValueText(value: number | null | undefined): string {
  return value === null || value === undefined ? UNKNOWN_TEXT : String(value)
}

/**
 * 专精列：`S1 3 · S2 1`；没有专精显示「—」。
 * `null` / `undefined` ＝ 未识别，写「未识别」——**空对象或全 0 槽是已知的「无专精」**，
 * 与「不知道」必须区分开（M35 规格 §4.9）。
 */
export function masteryText(masteries: Record<string, number> | null | undefined): string {
  if (masteries === null || masteries === undefined) return UNKNOWN_TEXT
  const parts = ['1', '2', '3']
    .map((slot) => [slot, Number(masteries[slot] ?? 0)] as const)
    .filter(([, level]) => level > 0)
    .map(([slot, level]) => `S${slot} ${level}`)
  return parts.length ? parts.join(' · ') : '—'
}

/** 模组列：`X2 · Y1`；没有模组显示「—」；`null` / `undefined` ＝ 未识别（同上）。 */
export function moduleText(modules: Record<string, number> | null | undefined): string {
  if (modules === null || modules === undefined) return UNKNOWN_TEXT
  const parts = Object.entries(modules)
    .filter(([, level]) => Number(level) > 0)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([type, level]) => `${type}${level}`)
  return parts.length ? parts.join(' · ') : '—'
}

/** 一行的字段来源集合（去重，森空岛优先），用于卡片上的来源标记。 */
export function rowSources(row: OperatorRow): string[] {
  const order = ['skland', 'recognition', 'manual', 'agent']
  const seen = new Set<string>(Object.values(row.field_sources ?? {}))
  return order.filter((source) => seen.has(source))
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

/**
 * 把任意值渲染成紧凑文本：字符串原样，数值/布尔转字符串，别的用一行 JSON。
 * 后端字段缺失、类型换了形状时卡片只降级不抛错，所以这里吞掉 `JSON.stringify`
 * 的循环引用异常，再退回 `String()`。
 */
export function compactText(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') return String(value)
  try {
    const text = JSON.stringify(value)
    if (typeof text === 'string') return text
  } catch {
    // 循环引用等：换 String() 再试。
  }
  try {
    return String(value)
  } catch {
    return '—'
  }
}

/** 宽松取数：数字、数字字符串都认；`null`/空串/布尔/`NaN` 一律 null。 */
export function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || typeof value === 'boolean') return null
  if (typeof value === 'string' && !value.trim()) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

/**
 * 一个养成目标的人话描述：精二 · 技能 7 · S2 3 · 模组 X2。
 * 字段一个都读不出时返回「不改变养成目标」；非对象输入也不抛错。
 */
export function upgradeTargetText(target: unknown): string {
  if (!target || typeof target !== 'object' || Array.isArray(target)) return '不改变养成目标'
  const row = target as Record<string, unknown>
  const parts: string[] = []
  if (typeof row.elite === 'number') parts.push(eliteLabel(row.elite))
  if (typeof row.skill_level === 'number') parts.push(`技能 ${row.skill_level}`)
  const masteries = ['1', '2', '3'].flatMap((slot) => {
    const level = Number((row.masteries as Record<string, unknown> | undefined)?.[slot] ?? 0)
    return level > 0 ? [`S${slot} ${level}`] : []
  })
  if (masteries.length) parts.push(masteries.join(' · '))
  const modules = (Array.isArray(row.modules) ? row.modules : []).flatMap((module) => {
    if (!module || typeof module !== 'object' || Array.isArray(module)) return []
    const entry = module as Record<string, unknown>
    if (typeof entry.type !== 'string') return []
    const level = Number(entry.level ?? 0)
    return level > 0 ? [`模组 ${entry.type}${level}`] : []
  })
  if (modules.length) parts.push(modules.join(' '))
  return parts.length ? parts.join(' · ') : '不改变养成目标'
}

const PLAN_STATUS_LABELS: Record<string, string> = {
  active: '进行中',
  completed: '已完成',
  cancelled: '已取消',
}

/** 养成计划状态文案（§7.7 的 active/completed/cancelled）；没见过的状态原样显示。 */
export function planStatusLabel(status: string | null | undefined): string {
  if (!status) return '未知'
  return PLAN_STATUS_LABELS[status] ?? status
}

/** 计划的一行关卡（`progress.stages` 与预演 `preview.stages` 是同一形状）。 */
export interface PlanStageSummary {
  /** 关卡代号（`stage_code`），读不出时退回 `stage_id` / `stage`。 */
  label: string
  runs: number | null
  /** 这条关卡掉的材料名。 */
  items: string[]
  sanity: number | null
}

/**
 * 关卡行：`{stage_code, stage_id, runs, sanity, items:[{item_name}]}`。
 * 形状不认识的行降级成一行文本，不丢信息也不抛错。
 */
export function parsePlanStages(value: unknown): PlanStageSummary[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((row) => {
    if (!row || typeof row !== 'object' || Array.isArray(row)) {
      const text = compactText(row)
      return text === '—' ? [] : [{ label: text, runs: null, items: [], sanity: null }]
    }
    const stage = row as Record<string, unknown>
    const items = (Array.isArray(stage.items) ? stage.items : []).map((item) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return compactText(item)
      const entry = item as Record<string, unknown>
      return compactText(entry.item_name ?? entry.name ?? item)
    })
    return [
      {
        label: compactText(stage.stage_code ?? stage.stage_id ?? stage.stage ?? '未知关卡'),
        runs: finiteNumber(stage.runs),
        items,
        sanity: finiteNumber(stage.sanity),
      },
    ]
  })
}

/** 关卡与场次一行话：「1-7 ×5、CE-6 ×12」；空列表返回 null。 */
export function planStagesText(stages: readonly PlanStageSummary[]): string | null {
  const parts = stages.map((stage) => (stage.runs === null ? stage.label : `${stage.label} ×${stage.runs}`))
  return parts.length ? parts.join('、') : null
}

/** 剩余场次合计（各关卡 `runs` 之和）；一个都读不出时 null。 */
export function planRunsTotal(stages: readonly PlanStageSummary[]): number | null {
  const runs = stages.flatMap((stage) => (stage.runs === null ? [] : [stage.runs]))
  return runs.length ? runs.reduce((sum, value) => sum + value, 0) : null
}

/**
 * `progress.missing` / `requirements`：`{固源岩: 12}` → 「固源岩 ×12、聚酸酯 ×3」。
 * 数量为 0 的条目跳过（已经备齐），没有剩余需求时返回 null。
 */
export function missingMaterialsText(missing: unknown): string | null {
  if (!missing || typeof missing !== 'object' || Array.isArray(missing)) return null
  const parts = Object.entries(missing as Record<string, unknown>).flatMap(([name, count]) => {
    const amount = finiteNumber(count)
    if (amount !== null && amount <= 0) return []
    return [`${name} ×${compactText(count)}`]
  })
  return parts.length ? parts.join('、') : null
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
export function recognitionText(entry: AccountSummaryEntry, prefix = '识别于'): string {
  if (!entry.recognized) return '尚未识别'
  const at = formatDateTime(entry.recognized_at) ?? '未知时间'
  const days = entry.stale_days
  const ago = days === null || days === undefined ? '' : days <= 0 ? '（今天）' : `（${days} 天前）`
  const stale = entry.stale ? `，已超过 ${entry.stale_after_days} 天` : ''
  return `${prefix} ${at}${ago}${stale}`
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

/**
 * 顶部资源栏类道具（M44 §3.1.6）：识别值只有千位以上有效，页面上不能当精确值展示。
 *
 * 这三件是主界面顶部资源栏上的东西，内核给的数**后三位被截断/取整**（2026-10-12 实测
 * 4797534 → 4797000、242920 → 242000），所以数量前标「约」；其余道具是在仓库页签里逐件
 * 识别的，数量是准的，不标。判定写成纯函数是为了单测能直接钉住这三个 id。
 */
const APPROXIMATE_COUNT_ITEM_IDS: ReadonlySet<string> = new Set([
  '4001', // 龙门币
  '4002', // 至纯源石
  '4003', // 合成玉
])

/** 这件道具的数量是不是「约」值（顶部资源栏那三件）。 */
export function isApproximateCountItem(itemId: string): boolean {
  return APPROXIMATE_COUNT_ITEM_IDS.has(itemId.trim())
}

/** 近似值的说明文案，挂在数量的 `title` 上：说得清为什么是「约」，不是含糊其辞。 */
export function approximateCountHint(itemId: string): string | null {
  return isApproximateCountItem(itemId) ? '上游识别只给到千位，后三位不准确' : null
}

