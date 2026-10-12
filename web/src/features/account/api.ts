import { api } from '@/api/client'
import { ApiError, toApiError } from '@/api/errors'
import type { components } from '@/types/api'

/**
 * 「账号数据」页的请求层（规格 §7.5、§7.7、附录 C 的 `/api/account/*`）。
 *
 * `/operators` 与 `/depot` 两个列表端点在路由上带 `response_model`（M34），形状由生成类型
 * `web/src/types/api.d.ts` 定死：读错键过不了 `pnpm typecheck`，所以它们**不在**下面的
 * `AccountEndpoints` 覆盖层里。其余端点（概要、单条与编辑、差异、养成计划）还没接响应
 * 模型，OpenAPI 里是自由对象，形状按规格在本地声明——往覆盖层里加回上面那两个端点，
 * 等于把这道护栏撤掉。请求仍走 `api` 客户端，鉴权（`X-Token`）与请求号由它的中间件照旧加。
 *
 * `/api/skland/*` 六个端点（M42）同理，另起一份 `SklandEndpoints` 覆盖层：它们当时还
 * 没进 `web/openapi.json`，形状按 M42 规格 §4.6 在本地声明。
 */

/** 字段来源：识别 / 手动 / Agent / 森空岛（§7.3、§7.4、M42 §4.5）。 */
export type FieldSource = 'recognition' | 'manual' | 'agent' | 'skland'

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

/**
 * 森空岛（Skland）绑定与同步（M42 规格 §4.6）。
 *
 * 六个端点当时还没进生成类型（`web/openapi.json` 要等下一次 `dump_openapi.py` +
 * `pnpm gen:api` 才有），所以路径与响应形状都在下面的 `SklandEndpoints` 覆盖层里声明，
 * 响应再过一层 `normalizeSkland*` 兜底：形状读不出来时页面按「未绑定 / 没有摘要」渲染，
 * 既不炸，也不编造数字。
 */

/** 森空岛角色（一个账号下的一个游戏角色）。 */
export interface SklandRole {
  uid: string
  app_code: string
  channel_master_id: string | null
  nickname: string | null
  level: number | null
  is_default: boolean
  synced_at: string | null
}

/** 一轮同步的摘要：`POST /api/skland/sync` 的响应，也是 `status.last_sync` 的形状。 */
export interface SklandSyncSummary {
  status: string
  role: SklandRole
  synced_at: string
  total: number
  created: number
  updated: number
  unchanged: number
  /** 整轮完整成功时被置成「未拥有」的干员数（§4.5 护栏 ③）。 */
  ownership_cleared: number
  /** 与既有 manual / agent 值不同、记进 `AccountOperatorDiff` 的处数。 */
  diffs: number
  /** 越界被丢弃的专精 / 模组值处数（§4.5 护栏 ②）。 */
  dropped_invalid: number
  duration_ms: number
}

/** `GET /api/skland/status` 与 `POST /api/skland/login/manual` 的响应。 */
export interface SklandStatus {
  bound: boolean
  /** `false` ＝ `config.yaml` 里没配 `skland.credential_key`；这时连二维码都不该显示。 */
  key_configured: boolean
  account: SklandRole | null
  roles: SklandRole[]
  last_sync_at: string | null
  last_sync: SklandSyncSummary | null
  /** 上一轮同步 / 定时任务的失败原因（成功或没跑过时为 `null`）。 */
  last_error: string | null
  /** 两次同步之间的最短间隔（秒）；冷却内 `POST /api/skland/sync` 回 429。 */
  min_sync_interval_seconds: number
  /** 上游干员数低于这个值时整轮不写（后端护栏，页面只做说明）。 */
  min_chars: number
  auto_sync_enabled: boolean
  auto_sync_interval_minutes: number
}

/** `POST /api/skland/login/qrcode` 的响应：`qr_svg` 是完整的 SVG 字符串。 */
export interface SklandQrSession {
  session_id: string
  qr_svg: string
  payload: string
  expires_at: string
  /** 轮询周期（毫秒），由后端定；页面照它轮询（§4.8）。 */
  poll_interval_ms: number
}

/** 扫码状态四态（§4.6）：只有 `waiting` 需要继续轮询。 */
export type SklandQrState = 'waiting' | 'succeeded' | 'expired' | 'failed'

/** `GET /api/skland/login/qrcode/{session_id}` 的响应。 */
export interface SklandQrPoll {
  state: SklandQrState
  message: string | null
  account: SklandRole | null
  roles: SklandRole[]
}

/** `POST /api/skland/login/manual` 的请求体：两项都必填，绑前后端会验一次。 */
export interface SklandManualCredentials {
  cred: string
  token: string
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

/**
 * `/api/skland/*` 的覆盖层（M42 §4.6）：六个端点都按规格在本地声明路径与响应形状。
 * 键名与后端契约逐字对齐（`qr_svg`、`poll_interval_ms`、`ownership_cleared`…）。
 */
interface SklandEndpoints {
  GET: {
    (path: '/api/skland/status', init?: { signal?: AbortSignal }): Promise<Result<SklandStatus>>
    /** 轮询扫码状态；会话不存在时后端回 404（`SKLAND_QR_SESSION_NOT_FOUND`）。 */
    (path: '/api/skland/login/qrcode/{session_id}', init: { params: { path: { session_id: string } }; signal?: AbortSignal }): Promise<Result<SklandQrPoll>>
  }
  POST: {
    /** 生成扫码会话；没配 `skland.credential_key` 时后端回 409。 */
    (path: '/api/skland/login/qrcode', init?: { signal?: AbortSignal }): Promise<Result<SklandQrSession>>
    /** 手动兜底绑定：body 两项必填，凭据不对回 401 `SKLAND_LOGIN_FAILED`。 */
    (path: '/api/skland/login/manual', init: { body: SklandManualCredentials }): Promise<Result<SklandStatus>>
    /** 同步练度（**同步执行**，秒级）：忙 409、冷却 429、上游失败 502。 */
    (path: '/api/skland/sync'): Promise<Result<SklandSyncSummary>>
  }
  DELETE: {
    /** 解绑：删凭据与角色，**不动**已同步的练度。 */
    (path: '/api/skland/binding'): Promise<Result<{ bound: boolean }>>
  }
}

/** 覆盖层在前、生成类型在后：没覆盖的路径走 `typeof api` 的签名（`/operators`、`/depot`）。 */
const accountApi = api as unknown as AccountEndpoints & typeof api

/** skland 六个端点的覆盖层；`/api/skland` 不进 `AccountEndpoints`，另起一份更好读。 */
const sklandApi = api as unknown as SklandEndpoints & typeof api

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
  /**
   * 森空岛绑定与同步状态（M42 §4.6）：整份状态一个键，扫码成功 / 同步 / 解绑后失效重取。
   * 端点根是 `/api/skland`，但缓存键挂在账号页这一片下面（`keys.all` 是 `['api']`，
   * 这一页只有这一处在用，方便整片失效）。
   */
  sklandStatus: () => [...accountRoot, 'skland', 'status'] as const,
  /** 当前这张二维码：一次只有一个会话，重新生成走 `refetch`。 */
  sklandQr: () => [...accountRoot, 'skland', 'qrcode'] as const,
  /** 某个扫码会话的轮询结果；`session_id` 进键，换一张二维码自然换缓存。 */
  sklandQrPoll: (sessionId: string) => [...accountRoot, 'skland', 'qrcode', sessionId, 'poll'] as const,
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

/**
 * 森空岛六个端点的请求层（M42 §4.6）。
 *
 * 端点还没进生成类型，路径与形状由上面的 `SklandEndpoints` 覆盖层声明；下面每个
 * `normalizeSkland*` 再做一层运行时兜底，读不出来的形状一律落安全默认：状态按
 * 「未绑定、密钥当作配好了」（真没配时后端的 409 会说明原因）、扫码状态落 `failed`
 * （既不假装成功，也不无限轮询）、摘要读不出就返回 `null`（页面只说「同步完成」，
 * 不把空对象渲染成「本轮 0 名」）。
 */

const EMPTY_SKLAND_ROLE: SklandRole = {
  uid: '',
  app_code: '',
  channel_master_id: null,
  nickname: null,
  level: null,
  is_default: false,
  synced_at: null,
}

/**
 * 角色兜底：`uid` 是主键，读不出来就当这条不存在（返回 `null`）——不渲染一个空壳角色。
 * 数字形态的 `uid` 也认（上游可能给数字），转成字符串。
 */
export function normalizeSklandRole(value: unknown): SklandRole | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const raw = value as Partial<SklandRole>
  const uid = typeof raw.uid === 'string'
    ? raw.uid
    : typeof raw.uid === 'number' && Number.isFinite(raw.uid) ? String(raw.uid) : ''
  if (!uid) return null
  const text = (item: unknown) => (typeof item === 'string' && item ? item : null)
  return {
    uid,
    app_code: typeof raw.app_code === 'string' ? raw.app_code : '',
    channel_master_id: text(raw.channel_master_id),
    nickname: text(raw.nickname),
    level: typeof raw.level === 'number' && Number.isFinite(raw.level) ? raw.level : null,
    is_default: raw.is_default === true,
    synced_at: text(raw.synced_at),
  }
}

/** 角色列表兜底：认不出的条目直接丢掉（连 `uid` 都没有的行没有任何可展示的信息）。 */
export function normalizeSklandRoles(value: unknown): SklandRole[] {
  if (!Array.isArray(value)) return []
  return value.map(normalizeSklandRole).filter((role): role is SklandRole => role !== null)
}

/**
 * 同步摘要兜底：**不像摘要就返回 `null`**（连 `status` 与任一计数都没有）。
 *
 * 返回 `null` 时页面只写「同步完成」并刷新状态，不把空对象渲染成「本轮 0 名、没有改动」——
 * 那正是 M35 修过的「把不知道写成具体值」。
 */
export function normalizeSklandSyncSummary(value: unknown): SklandSyncSummary | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const raw = value as Partial<SklandSyncSummary>
  const counters = ['total', 'created', 'updated', 'unchanged', 'ownership_cleared', 'diffs', 'dropped_invalid', 'duration_ms'] as const
  const readable = typeof raw.status === 'string' || counters.some((key) => typeof raw[key] === 'number')
  if (!readable) return null
  const count = (item: unknown) => (typeof item === 'number' && Number.isFinite(item) ? item : 0)
  return {
    status: typeof raw.status === 'string' ? raw.status : '',
    // 摘要是 `last_sync` 那一栏的形状，页面只用计数；角色读不出来就留空壳，不整条丢掉。
    role: normalizeSklandRole(raw.role) ?? { ...EMPTY_SKLAND_ROLE },
    synced_at: typeof raw.synced_at === 'string' ? raw.synced_at : '',
    total: count(raw.total),
    created: count(raw.created),
    updated: count(raw.updated),
    unchanged: count(raw.unchanged),
    ownership_cleared: count(raw.ownership_cleared),
    diffs: count(raw.diffs),
    dropped_invalid: count(raw.dropped_invalid),
    duration_ms: count(raw.duration_ms),
  }
}

/** 状态整体读不出来时的兜底（`key_configured` 看上一条注释）。 */
const SKLAND_STATUS_FALLBACK: SklandStatus = {
  bound: false,
  key_configured: true,
  account: null,
  roles: [],
  last_sync_at: null,
  last_sync: null,
  last_error: null,
  min_sync_interval_seconds: 0,
  min_chars: 0,
  auto_sync_enabled: false,
  auto_sync_interval_minutes: 0,
}

/**
 * 绑定状态响应兜底：缺项按 `false` / `0` / `null` 补，页面只读不炸。
 *
 * `bound` 与 `key_configured` 都**只认明确的真值**：读不出来时不假装已绑定（那会显示一个
 * 并不存在的账号），也不假装没配密钥（那会把用户堵在一条没有出口的死路上）。
 */
export function normalizeSklandStatus(value: unknown): SklandStatus {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { ...SKLAND_STATUS_FALLBACK }
  const raw = value as Partial<SklandStatus>
  const count = (item: unknown) => (typeof item === 'number' && Number.isFinite(item) ? item : 0)
  const text = (item: unknown) => (typeof item === 'string' && item ? item : null)
  return {
    bound: raw.bound === true,
    key_configured: raw.key_configured !== false,
    account: normalizeSklandRole(raw.account),
    roles: normalizeSklandRoles(raw.roles),
    last_sync_at: text(raw.last_sync_at),
    last_sync: normalizeSklandSyncSummary(raw.last_sync),
    last_error: text(raw.last_error),
    min_sync_interval_seconds: count(raw.min_sync_interval_seconds),
    min_chars: count(raw.min_chars),
    auto_sync_enabled: raw.auto_sync_enabled === true,
    auto_sync_interval_minutes: count(raw.auto_sync_interval_minutes),
  }
}

/** 轮询间隔兜底：响应里读不出来时按规格 §4.8 的 2 秒。 */
export const DEFAULT_SKLAND_POLL_INTERVAL_MS = 2_000

/** 扫码会话兜底：`session_id` / `qr_svg` 缺失时卡片按「没拿到二维码」处理（不渲染空图）。 */
export function normalizeSklandQrSession(value: unknown): SklandQrSession {
  const raw = (value && typeof value === 'object' && !Array.isArray(value) ? value : {}) as Partial<SklandQrSession>
  const interval = raw.poll_interval_ms
  return {
    session_id: typeof raw.session_id === 'string' ? raw.session_id : '',
    qr_svg: typeof raw.qr_svg === 'string' ? raw.qr_svg : '',
    payload: typeof raw.payload === 'string' ? raw.payload : '',
    expires_at: typeof raw.expires_at === 'string' ? raw.expires_at : '',
    poll_interval_ms: typeof interval === 'number' && Number.isFinite(interval) && interval > 0
      ? interval
      : DEFAULT_SKLAND_POLL_INTERVAL_MS,
  }
}

const SKLAND_QR_STATES: readonly SklandQrState[] = ['waiting', 'succeeded', 'expired', 'failed']

/**
 * 只认契约里的四态；读不出来落 `failed`：既不假装扫码成功，也不无限轮询下去，
 * 用户还能看到「重新生成二维码」。
 */
export function normalizeSklandQrState(value: unknown): SklandQrState {
  return typeof value === 'string' && (SKLAND_QR_STATES as readonly string[]).includes(value)
    ? (value as SklandQrState)
    : 'failed'
}

/** 轮询响应兜底：`message` / `account` / `roles` 缺了就缺着，页面按「没有」渲染。 */
export function normalizeSklandQrPoll(value: unknown): SklandQrPoll {
  const raw = (value && typeof value === 'object' && !Array.isArray(value) ? value : {}) as Partial<SklandQrPoll>
  return {
    state: normalizeSklandQrState(raw.state),
    message: typeof raw.message === 'string' && raw.message ? raw.message : null,
    account: normalizeSklandRole(raw.account),
    roles: normalizeSklandRoles(raw.roles),
  }
}

/**
 * 同步被 429 拒绝时把 `Retry-After` 并进错误里（§4.6）：`toApiError` 只保留状态码、
 * 错误码与响应体，响应头在别处读不到，所以这里就地取出来放进 `details`。
 */
function rateLimited(error: unknown, response: Response): ApiError {
  const failure = toApiError(error, response)
  if (failure.status !== 429) return failure
  const header = response.headers.get('Retry-After')
  const seconds = header === null ? Number.NaN : Number(header)
  if (!Number.isFinite(seconds) || seconds <= 0) return failure
  return new ApiError({
    message: failure.message,
    status: failure.status,
    code: failure.code,
    details: { retry_after_seconds: seconds },
    body: failure.body,
  })
}

/** 同步被 429 拒绝时「还要等几秒」；响应头缺失或状态不是 429 时为 `null`。 */
export function retryAfterSeconds(error: unknown): number | null {
  const failure = toApiError(error)
  if (failure.status !== 429 || !failure.details || typeof failure.details !== 'object') return null
  const value = (failure.details as { retry_after_seconds?: unknown }).retry_after_seconds
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null
}

/** 绑定与同步状态（`GET`）：卡片挂载时读一次，扫码成功 / 同步 / 解绑后再失效重取。 */
export async function fetchSklandStatus(signal?: AbortSignal): Promise<SklandStatus> {
  const { data, error, response } = await sklandApi.GET('/api/skland/status', { signal })
  if (error || !data) throw toApiError(error, response)
  return normalizeSklandStatus(data)
}

/** 生成扫码会话（`POST`）；没配 `skland.credential_key` 时后端回 409 `SKLAND_ENCRYPTION_KEY_INVALID`。 */
export async function startSklandQrLogin(): Promise<SklandQrSession> {
  const { data, error, response } = await sklandApi.POST('/api/skland/login/qrcode')
  if (error || !data) throw toApiError(error, response)
  return normalizeSklandQrSession(data)
}

/** 轮询扫码状态（`GET`）：`waiting` 时由卡片按 `poll_interval_ms` 再取一次。 */
export async function pollSklandQrLogin(sessionId: string, signal?: AbortSignal): Promise<SklandQrPoll> {
  const { data, error, response } = await sklandApi.GET('/api/skland/login/qrcode/{session_id}', {
    params: { path: { session_id: sessionId } },
    signal,
  })
  if (error || !data) throw toApiError(error, response)
  return normalizeSklandQrPoll(data)
}

/** 手动兜底绑定（`POST`）：返回绑定后的状态（同 `GET /status`）；凭据不对回 401。 */
export async function bindSklandManual(body: SklandManualCredentials): Promise<SklandStatus> {
  const { data, error, response } = await sklandApi.POST('/api/skland/login/manual', { body })
  if (error || !data) throw toApiError(error, response)
  return normalizeSklandStatus(data)
}

/** 同步一轮练度（`POST`，**同步执行**、秒级）：摘要形状读不出时返回 `null`，页面只刷新状态。 */
export async function syncSkland(): Promise<SklandSyncSummary | null> {
  const { data, error, response } = await sklandApi.POST('/api/skland/sync')
  if (error || !data) throw rateLimited(error, response)
  return normalizeSklandSyncSummary(data)
}

/** 解绑（`DELETE`）：删凭据与角色，**不动**已同步的练度（§4.4）。 */
export async function unbindSkland(): Promise<boolean> {
  const { data, error, response } = await sklandApi.DELETE('/api/skland/binding')
  if (error || !data) throw toApiError(error, response)
  return data.bound === true
}

