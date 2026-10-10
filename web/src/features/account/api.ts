import { api } from '@/api/client'
import { toApiError } from '@/api/errors'

/**
 * 「账号数据」页的请求层（规格 §7.5、附录 C 的 `/api/account/*`）。
 *
 * 这六个端点还没进生成类型 `web/src/types/api.d.ts`（主 agent 会跑 `pnpm gen:api`），
 * 形状按规格冻结，先在本地声明；请求仍走 `api` 客户端，鉴权（`X-Token`）与请求号
 * 由它的中间件照旧加。生成类型落地后，把下面的 `AccountEndpoints` 转写删掉，
 * 直接 `api.GET('/api/account/summary')` 即可，函数签名保持不变。
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
  elite: number
  level: number
  potential: number
  skill_level: number
  /** 技能序号（"1"/"2"/"3"）→ 专精 0–3。 */
  masteries: Record<string, number>
  /** 模组类型（X/Y/D/A）→ 等级 1–3。 */
  modules: Record<string, number>
  /** 字段名 → 来源；识别不覆盖技能等级、专精与模组。 */
  field_sources: Record<string, FieldSource>
  recognized_at: string | null
  updated_at: string | null
  /** 冻结形状没列职业，但列表按职业筛选；后端带上时显示，缺失显示「—」。 */
  profession?: string | null
}

export interface OperatorListPayload {
  operators: OperatorRow[]
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

interface Result<T> {
  data?: T
  error?: unknown
  response: Response
}

interface AccountEndpoints {
  GET: {
    (path: '/api/account/summary', init?: { signal?: AbortSignal }): Promise<Result<AccountSummaryRaw>>
    (path: '/api/account/operators', init?: { params?: { query?: OperatorListQuery }; signal?: AbortSignal }): Promise<Result<OperatorListPayload>>
    (path: '/api/account/operator-diffs', init?: { signal?: AbortSignal }): Promise<Result<{ items: OperatorDiff[]; total?: number }>>
    (path: '/api/account/depot', init?: { params?: { query?: DepotListQuery }; signal?: AbortSignal }): Promise<Result<DepotListPayload>>
  }
  PATCH: (
    path: '/api/account/operators/{char_id}',
    init: { params: { path: { char_id: string } }; body: OperatorPatch },
  ) => Promise<Result<OperatorRow>>
  DELETE: (
    path: '/api/account/operator-diffs',
    init?: { body?: { ids?: number[] } },
  ) => Promise<Result<{ dismissed: number }>>
}

const accountApi = api as unknown as AccountEndpoints

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

export async function fetchOperators(query: OperatorListQuery, signal?: AbortSignal): Promise<OperatorListPayload> {
  const { data, error, response } = await accountApi.GET('/api/account/operators', {
    params: { query },
    signal,
  })
  if (error || !data) throw toApiError(error, response)
  return { operators: data.operators ?? [], total: data.total ?? 0, limit: data.limit ?? query.limit, offset: data.offset ?? query.offset }
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

export async function fetchDepot(query: DepotListQuery, signal?: AbortSignal): Promise<DepotListPayload> {
  const { data, error, response } = await accountApi.GET('/api/account/depot', { params: { query }, signal })
  if (error || !data) throw toApiError(error, response)
  return { items: data.items ?? [], total: data.total ?? 0, categories: data.categories ?? [] }
}
