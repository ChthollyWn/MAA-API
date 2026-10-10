import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import type { components } from '@/types/api'

/**
 * 「账号数据」页的请求层（规格 §7.5、§7.7、附录 C 的 `/api/account/*`）。
 *
 * `/operators` 与 `/depot` 两个列表端点在路由上带 `response_model`（M34），形状由生成类型
 * `web/src/types/api.d.ts` 定死：读错键过不了 `pnpm typecheck`，所以它们**不在**下面的
 * `AccountEndpoints` 覆盖层里。其余端点（概要、单条与编辑、差异、养成计划）还没接响应
 * 模型，OpenAPI 里是自由对象，形状按规格在本地声明——往覆盖层里加回上面那两个端点，
 * 等于把这道护栏撤掉。请求仍走 `api` 客户端，鉴权（`X-Token`）与请求号由它的中间件照旧加。
 */

/** 字段来源：识别 / 手动 / Agent（§7.3、§7.4）。 */
export type FieldSource = 'recognition' | 'manual' | 'agent'

/** `GET /api/account/summary` 里干员与仓库各占一份。 */
export interface AccountSummaryEntry {
  recognized: boolean
  total: number
  elite2: number
  recognized_at: string | null
  stale: boolean
  stale_days: number | null
  stale_after_days: number
}

export interface AccountSummary {
  operators: AccountSummaryEntry
  depot: AccountSummaryEntry
  diffs: number
}

export interface OperatorRow {
  char_id: string
  name: string
  rarity: number
  own: boolean
  /**
   * 练度六列（M35）：`null` ＝ **未识别**，既没有识别值也没有 manual / agent 写入。
   * 展示层必须写「未识别」，不许折算成 0 级 / 技能 1 级 / 无专精；内核 OperBox 只
   * 识别 `elite` / `level` / `potential`，技能等级、专精、模组只有人工或 Agent 写过才有值。
   */
  elite: number | null
  level: number | null
  potential: number | null
  skill_level: number | null
  /** 技能序号（"1"/"2"/"3"）→ 专精 0–3；`null` ＝ 未识别。 */
  masteries: Record<string, number> | null
  /** 模组类型（X/Y/D/A）→ 等级 1–3；`null` ＝ 未识别。 */
  modules: Record<string, number> | null
  /** 字段名 → 来源；识别不覆盖技能等级、专精与模组。 */
  field_sources: Record<string, FieldSource>
  recognized_at: string | null
  updated_at: string | null
  /** 冻结形状没列职业，但列表按职业筛选；后端带上时显示，缺失显示「—」。 */
  profession?: string | null
}

/** 生成类型里的干员行：`GET /api/account/operators` 的响应元素（路由的 `response_model`）。 */
export type OperatorRowWire = components['schemas']['AccountOperatorRow']

export interface OperatorListPayload {
  /** 路由 `response_model` 定的容器键（docs/05 §6.20）：列表统一叫 `items`。 */
  items: OperatorRow[]
  total: number
  limit: number
  offset: number
}

export interface OperatorListQuery {
  query?: string
  owned_only?: boolean
  rarity_min?: number
  profession?: string
  elite_min?: number
  limit: number
  offset: number
}

/** `PATCH /api/account/operators/{char_id}`：只含要改的字段。 */
export interface OperatorPatch {
  own?: boolean
  elite?: number
  level?: number
  potential?: number
  skill_level?: number
  masteries?: Record<string, number>
  modules?: Record<string, number>
}

export interface OperatorDiff {
  id: number
  char_id: string
  name: string
  field: string
  manual_value: unknown
  recognized_value: unknown
  created_at: string
}

export interface DepotItem {
  item_id: string
  name: string
  count: number
  category?: string | null
}

export interface DepotListPayload {
  items: DepotItem[]
  total: number
  categories: string[]
}

export interface DepotListQuery {
  category?: string
  query?: string
  limit: number
  offset: number
}

/** 养成计划的状态（`GET /api/account/upgrade-plans?status=`）。 */
export type UpgradePlanStatus = 'active' | 'completed' | 'cancelled'

/**
 * §4.7 的「待确认」项：某一维练度是「未识别」（`null`），所以那一维的材料增量没敢算。
 *
 * `GET /api/account/upgrade-plans` 的每条计划放在 `progress.unknowns`；M35 之前落库的旧
 * 行没有这个键，页面按空数组处理（不能因此整块不渲染）。
 */
export interface PlanUnknown {
  /** 干员名（不是 `char_id`：这一步只为了让人看懂是哪一位）。 */
  operator: string
  /** 没识别的练度字段名（`elite`/`level`/`potential`/`skill_level`/`masteries`/`modules`）。 */
  field: string
  /** 一句人话，例如「未识别，需先确认」。 */
  reason: string
}

/**
 * `start_upgrade_plan` 落库后的计划（服务层 `_plan_wire()`，规格 §7.7）。
 *
 * `progress` 按计划阶段长出不同的键——`stages`/`total_sanity`（启动时）、
 * `missing`（刷新后）、`days_estimated`/`replanned_at`/`closed_dropped`（重新排期后）、
 * `replan_error`（排期失败时）——所以这里留成宽松映射，页面按需收窄，缺项不炸。
 * M35 起还会带 `unknowns`（§4.7「待确认」）与 `notes`（人话说明）。
 */
export interface UpgradePlan {
  id: string
  name: string
  status: string
  targets: unknown
  requirements: unknown
  progress: Record<string, unknown> & { unknowns?: PlanUnknown[]; notes?: unknown }
  schedule_id: string | null
  session_id: string | null
  created_at: string | null
  updated_at: string | null
  completed_at: string | null
  /**
   * 人话说明。计划详情按 `progress.notes` 收；这里也认一份，因为启动/预览结果是在**顶层**
   * 给 `notes` 的（`UpgradePlanService.start/preview`），后端把哪一份透进计划行不影响渲染。
   */
  notes?: unknown
}

export interface UpgradePlanListPayload {
  items: UpgradePlan[]
  total: number
}

export interface UpgradePlanListQuery {
  status?: UpgradePlanStatus
}

interface Result<T> {
  data?: T
  error?: unknown
  response: Response
}

/**
 * 手写覆盖层：这几个端点还没接 `response_model`，生成的 OpenAPI 里响应是自由对象，
 * 形状只能在这里声明。
 *
 * `/operators` 与 `/depot` **故意不在这里**——它们在路由上有响应模型（M34），形状由
 * `web/src/types/api.d.ts` 定死，读错键会被 `pnpm typecheck` 抓住。往这里加回它们
 * 等于把那道护栏撤掉（M34 的空列表就是这么漏出去的）。
 */
interface AccountEndpoints {
  GET: {
    (path: '/api/account/summary', init?: { signal?: AbortSignal }): Promise<Result<AccountSummaryRaw>>
    (path: '/api/account/operator-diffs', init?: { signal?: AbortSignal }): Promise<Result<{ items: OperatorDiff[]; total?: number }>>
    (path: '/api/account/upgrade-plans', init?: { params?: { query?: UpgradePlanListQuery }; signal?: AbortSignal }): Promise<Result<UpgradePlanListPayload>>
    (path: '/api/account/upgrade-plans/{plan_id}', init: { params: { path: { plan_id: string } }; signal?: AbortSignal }): Promise<Result<UpgradePlan>>
  }
  PATCH: (
    path: '/api/account/operators/{char_id}',
    init: { params: { path: { char_id: string } }; body: OperatorPatch },
  ) => Promise<Result<OperatorRow>>
  DELETE: {
    (path: '/api/account/operator-diffs', init?: { body?: { ids?: number[] } }): Promise<Result<{ dismissed: number }>>
    /** 取消计划：后端删掉定时任务并把状态置成 `cancelled`，返回取消后的 wire。 */
    (path: '/api/account/upgrade-plans/{plan_id}', init: { params: { path: { plan_id: string } } }): Promise<Result<UpgradePlan>>
  }
}

/** 覆盖层在前、生成类型在后：没覆盖的路径走 `typeof api` 的签名（`/operators`、`/depot`）。 */
const accountApi = api as unknown as AccountEndpoints & typeof api

/** 与 `keys.all`（`['api']`）同根，方便整片失效；`web/src/api/keys.ts` 由主 agent 统一维护。 */
const accountRoot = ['api', 'account'] as const

export const accountKeys = {
  all: () => accountRoot,
  summary: () => [...accountRoot, 'summary'] as const,
  /** 干员列表的整片前缀；`invalidateQueries` 会连带命中所有带筛选参数的键。 */
  operatorsAll: () => [...accountRoot, 'operators'] as const,
  operators: (filters?: Readonly<Record<string, unknown>>) => [...accountRoot, 'operators', filters ?? {}] as const,
  diffs: () => [...accountRoot, 'operator-diffs'] as const,
  depotAll: () => [...accountRoot, 'depot'] as const,
  depot: (filters?: Readonly<Record<string, unknown>>) => [...accountRoot, 'depot', filters ?? {}] as const,
  /** 养成计划列表的整片前缀；`invalidateQueries` 会连带命中将来带筛选参数的键。 */
  upgradePlansAll: () => [...accountRoot, 'upgrade-plans'] as const,
  upgradePlans: (filters?: Readonly<Record<string, unknown>>) => [...accountRoot, 'upgrade-plans', filters ?? {}] as const,
}

const EMPTY_SUMMARY_ENTRY: AccountSummaryEntry = {
  recognized: false,
  total: 0,
  elite2: 0,
  recognized_at: null,
  stale: false,
  stale_days: null,
  stale_after_days: 0,
}

/**
 * 后端 `/api/account/summary` 的形状是 `{box, depot}`（干员那半沿用服务层的键名：
 * `elite_two` 与 `recognized_at`），这里翻译成页面用的 `operators`/`elite2`/`recognized`。
 * `pnpm gen:api` 之后可以按生成的类型替换这层转写。
 */
interface AccountSummaryRaw {
  box?: { total?: number; elite_two?: number; recognized_at?: string | null; stale?: boolean; stale_days?: number; pending_diffs?: number }
  depot?: { total?: number; recognized_at?: string | null; stale?: boolean; stale_days?: number }
}

/** 概要缺项时用空值兜底，页面只读不炸。 */
export function normalizeSummary(value: AccountSummaryRaw | undefined): AccountSummary {
  const box = value?.box ?? {}
  const depot = value?.depot ?? {}
  return {
    operators: {
      ...EMPTY_SUMMARY_ENTRY,
      recognized: Boolean(box.recognized_at),
      total: box.total ?? 0,
      elite2: box.elite_two ?? 0,
      recognized_at: box.recognized_at ?? null,
      stale: box.stale ?? !box.recognized_at,
      // 后端的 `stale_days` 是**过期阈值**（`agent.box_stale_days`），页面用它写
      // 「数据已超过 N 天」，所以映射到 `stale_after_days`。
      stale_after_days: box.stale_days ?? 0,
    },
    depot: {
      ...EMPTY_SUMMARY_ENTRY,
      recognized: Boolean(depot.recognized_at),
      total: depot.total ?? 0,
      recognized_at: depot.recognized_at ?? null,
      stale: depot.stale ?? !depot.recognized_at,
      stale_after_days: depot.stale_days ?? 0,
    },
    diffs: box.pending_diffs ?? 0,
  }
}

export async function fetchAccountSummary(signal?: AbortSignal): Promise<AccountSummary> {
  const { data, error, response } = await accountApi.GET('/api/account/summary', { signal })
  if (error || !data) throw toApiError(error, response)
  return normalizeSummary(data)
}

/**
 * 干员行兜底：映射不到的干员在库里 `name` / `rarity` / `profession` 可以是空，
 * 页面对这三项按空值渲染。
 *
 * 练度六列**不再**兜成 0 / 1 / 空表（M35）：`null` ＝ 未识别，兜底会把「不知道」
 * 写成具体值，正是这一轮要修的 bug。后端少给字段时（`undefined`）与 `null` 同义，
 * 一起归一成 `null`，展示层只认这一种「未识别」。
 */
export function normalizeOperatorRow(row: OperatorRowWire): OperatorRow {
  return {
    ...row,
    name: row.name ?? '',
    rarity: row.rarity ?? 0,
    profession: row.profession ?? null,
    elite: row.elite ?? null,
    level: row.level ?? null,
    potential: row.potential ?? null,
    skill_level: row.skill_level ?? null,
    masteries: row.masteries ?? null,
    modules: row.modules ?? null,
    field_sources: (row.field_sources ?? {}) as Record<string, FieldSource>,
    recognized_at: row.recognized_at ?? null,
    updated_at: row.updated_at ?? null,
  }
}

export async function fetchOperators(query: OperatorListQuery, signal?: AbortSignal): Promise<OperatorListPayload> {
  const { data, error, response } = await accountApi.GET('/api/account/operators', {
    params: { query },
    signal,
  })
  if (error || !data) throw toApiError(error, response)
  // 容器键由路由的 `response_model` 定死（docs/05 §6.20）：写错键过不了 `pnpm typecheck`。
  return {
    items: (data.items ?? []).map(normalizeOperatorRow),
    total: data.total ?? 0,
    limit: data.limit ?? query.limit,
    offset: data.offset ?? query.offset,
  }
}

export async function patchOperator(charId: string, body: OperatorPatch): Promise<OperatorRow> {
  const { data, error, response } = await accountApi.PATCH('/api/account/operators/{char_id}', {
    params: { path: { char_id: charId } },
    body,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

export async function fetchOperatorDiffs(signal?: AbortSignal): Promise<OperatorDiff[]> {
  const { data, error, response } = await accountApi.GET('/api/account/operator-diffs', { signal })
  if (error || !data) throw toApiError(error, response)
  return data.items ?? []
}

/** 传 `ids` 只标记这几条；不传（或空数组）＝全部标记已知（规格 §7.4）。 */
export async function dismissOperatorDiffs(ids?: number[]): Promise<number> {
  const { data, error, response } = ids?.length
    ? await accountApi.DELETE('/api/account/operator-diffs', { body: { ids } })
    : await accountApi.DELETE('/api/account/operator-diffs')
  if (error) throw toApiError(error, response)
  return data?.dismissed ?? 0
}

/** 仓库行兜底：映射不到的物品名为空，页面按 `item_id` 展示（服务层也这么兜）。 */
function normalizeDepotItem(row: components['schemas']['AccountDepotRow']): DepotItem {
  return {
    item_id: row.item_id,
    name: row.name ?? row.item_id,
    count: row.count,
    category: row.category ?? null,
  }
}

export async function fetchDepot(query: DepotListQuery, signal?: AbortSignal): Promise<DepotListPayload> {
  const { data, error, response } = await accountApi.GET('/api/account/depot', { params: { query }, signal })
  if (error || !data) throw toApiError(error, response)
  // `categories` 由后端按整份快照给（M34）：分页或筛选时下拉不会缩水。
  return {
    items: (data.items ?? []).map(normalizeDepotItem),
    total: data.total ?? 0,
    categories: data.categories ?? [],
  }
}

/** 缺项兜底：后端少给一个字段时页面照常渲染，而不是整块炸掉。 */
export function normalizeUpgradePlan(value: UpgradePlan): UpgradePlan {
  return {
    id: typeof value.id === 'string' ? value.id : '',
    name: typeof value.name === 'string' && value.name ? value.name : '未命名计划',
    status: typeof value.status === 'string' ? value.status : 'unknown',
    targets: value.targets ?? [],
    requirements: value.requirements ?? {},
    progress: value.progress && typeof value.progress === 'object' ? value.progress : {},
    schedule_id: value.schedule_id ?? null,
    session_id: value.session_id ?? null,
    created_at: value.created_at ?? null,
    updated_at: value.updated_at ?? null,
    completed_at: value.completed_at ?? null,
    notes: value.notes ?? null,
  }
}

/** 不带 `status` 时返回全部计划（进行中 / 已完成 / 已取消），列表里按状态分色展示。 */
export async function fetchUpgradePlans(
  query: UpgradePlanListQuery = {},
  signal?: AbortSignal,
): Promise<UpgradePlanListPayload> {
  const { data, error, response } = await accountApi.GET('/api/account/upgrade-plans', {
    params: { query },
    signal,
  })
  if (error || !data) throw toApiError(error, response)
  return { items: (data.items ?? []).map(normalizeUpgradePlan), total: data.total ?? data.items?.length ?? 0 }
}

/** 取消计划（`DELETE`）：后端删定时任务并把状态置成 `cancelled`，返回取消后的 wire。 */
export async function cancelUpgradePlan(planId: string): Promise<UpgradePlan> {
  const { data, error, response } = await accountApi.DELETE('/api/account/upgrade-plans/{plan_id}', {
    params: { path: { plan_id: planId } },
  })
  if (error || !data) throw toApiError(error, response)
  return normalizeUpgradePlan(data)
}
