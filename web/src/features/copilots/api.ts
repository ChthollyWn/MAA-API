import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'

/**
 * 「作业」页的请求层（规格 §9.10、§9.5）。
 *
 * 只有列表端点已经存在（`GET /api/resources/copilots`，M17 加的，返回不含作业
 * JSON 的元数据）。详情 / 删除 / 保留三个端点后端还没有（docs/05 §6.13 写明「资源的
 * 写入侧没有 HTTP 端点，只在 Agent 侧以工具形式提供」），这里按下面的形状先冻结：
 *
 * - `GET    /api/resources/copilots/{asset_id}`         → 资源 wire + `content`
 * - `DELETE /api/resources/copilots/{asset_id}`         → `{id, removed}`
 * - `POST   /api/resources/copilots/{asset_id}/retain`  → `{retain}` → 更新后的资源
 *
 * 撤销作业授权（`DELETE /api/agent/sessions/{session_id}/copilot-grant`）是规格 §9.6
 * 冻结的端点，后端还没实现。这些新增端点落地、且主 agent 跑过 `pnpm gen:api` 之后，
 * 把下面的 `CopilotsEndpoints` 转写删掉，直接 `api.GET('/api/resources/copilots/…')`
 * 即可，导出函数的签名不用改。
 */

/** 作业来源（规格 §9.5 的 `meta.source`）。 */
export type CopilotSource = 'user' | 'site' | 'ai'

/** 一次尝试的结果（规格 §9.7 的表）；后端以后可能加值，因此渲染层都按 string 兜底。 */
export type CopilotOutcome =
  | 'three_star'
  | 'two_star'
  | 'clear'
  | 'failed'
  | 'support_unavailable'
  | 'sanity_insufficient'
  | 'not_started'
  | 'unknown'
  | 'error'
  | 'interrupted'

/** 判定方：内核识别 / 模型看截图判定（§9.7）。 */
export type CopilotJudge = 'core' | 'model'

/** `meta.attempts` 里的一次尝试；字段名对齐附录 E 的 `copilot_attempt`。 */
export interface CopilotAttempt {
  id: string
  outcome: CopilotOutcome | string
  judgedBy: CopilotJudge | string
  /** 计入授权次数（§9.7）；字段缺失时为 null，页面不显示。 */
  counted: boolean | null
  /** `finished_at`，缺了退回 `started_at`。 */
  at: string | null
  evidence: string | null
}

/** 作业资源的元数据（§9.5）：老资源可能只有 `stage_name` / `title`。 */
export interface CopilotMeta {
  source: CopilotSource
  /** `meta.source` 缺失时按「用户上传」处理（老资源由 `upload_copilot` 写入）。 */
  sourceKnown: boolean
  /** 草稿的关卡标识就是作业 JSON 的 `stage_name`（例如 `1-7`）。 */
  stageId: string | null
  title: string | null
  note: string | null
  parentId: string | null
  siteId: string | null
  /** 作业站作业的来源链接；作业站客户端算不出链接时为空（§9.4「查不到就不给链接」）。 */
  siteUrl: string | null
  sessionId: string | null
  retain: boolean
  attempts: CopilotAttempt[]
}

export interface CopilotAsset {
  id: string
  kind: string
  name: string
  description: string | null
  meta: CopilotMeta
  enabled: boolean
  created_at: string | null
  updated_at: string | null
}

/** 详情多带一份作业 JSON（查看 / 下载用）。 */
export interface CopilotDetail extends CopilotAsset {
  content: unknown
}

export interface CopilotListPayload {
  items: CopilotAsset[]
  total: number
  page: number
  size: number
}

export interface CopilotListQuery {
  page: number
  size: number
  q?: string
}

/** 会话里生效中的作业授权（规格 §9.6；与 `atomic_grant` 并列）。 */
export interface CopilotGrantView {
  grantId: string | null
  stageId: string | null
  stageCode: string | null
  attemptsTotal: number
  attemptsUsed: number
  remainingAttempts: number
  success: string | null
  support: string | null
  status: string | null
  expiresAt: string | null
}

/** 列表每页的条数；后端 `size` 上限 200。 */
export const COPILOT_PAGE_SIZE = 50

interface Result<T> {
  data?: T
  error?: unknown
  response: Response
}

interface CopilotsEndpoints {
  GET: {
    (
      path: '/api/resources/copilots',
      init?: { params?: { query: CopilotListQuery }; signal?: AbortSignal },
    ): Promise<Result<unknown>>
    (
      path: '/api/resources/copilots/{asset_id}',
      init: { params: { path: { asset_id: string } }; signal?: AbortSignal },
    ): Promise<Result<unknown>>
  }
  DELETE: {
    (
      path: '/api/resources/copilots/{asset_id}',
      init: { params: { path: { asset_id: string } } },
    ): Promise<Result<{ id?: string; removed?: boolean }>>
    (
      path: '/api/agent/sessions/{session_id}/copilot-grant',
      init: { params: { path: { session_id: string } } },
    ): Promise<Result<unknown>>
  }
  POST: {
    (
      path: '/api/resources/copilots/{asset_id}/retain',
      init: { params: { path: { asset_id: string } }; body: { retain: boolean } },
    ): Promise<Result<unknown>>
  }
}

const copilotsApi = api as unknown as CopilotsEndpoints

/** 与 `keys.all`（`['api']`）同根，方便整片失效。 */
const copilotRoot = [...keys.all, 'copilots'] as const

export const copilotKeys = {
  all: () => copilotRoot,
  lists: () => [...copilotRoot, 'list'] as const,
  /** 列表的整片前缀；`invalidateQueries` 会连带命中所有带筛选参数的键。 */
  list: (filters?: Readonly<Record<string, unknown>>) => [...copilotRoot, 'list', filters ?? {}] as const,
  detail: (assetId: string) => [...copilotRoot, 'detail', assetId] as const,
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function text(value: unknown): string | null {
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return null
}

function flag(value: unknown): boolean {
  return value === true || value === 'true'
}

function count(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

const SOURCES: readonly CopilotSource[] = ['user', 'site', 'ai']

export function isCopilotSource(value: unknown): value is CopilotSource {
  return typeof value === 'string' && (SOURCES as readonly string[]).includes(value)
}

/**
 * `meta.attempts`：认不出的条目直接丢掉，字段名尽量宽容。
 *
 * 结果键同时认 `outcome` / `result` / `status`、判定方认 `judged_by` / `judge`、
 * 时间认 `finished_at` / `started_at` / `at` —— 服务的清理逻辑
 * （`services/retention_service.py` 的 `COPILOT_ATTEMPT_RESULT_KEYS`）就是这么宽，
 * 页面跟着它走，免得写入侧换个键名这里就整条丢掉。
 */
export function parseAttempts(value: unknown): CopilotAttempt[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((entry, index): CopilotAttempt[] => {
    const row = asRecord(entry)
    if (!row) return []
    const outcome = text(row.outcome) ?? text(row.result) ?? text(row.status)
    if (!outcome) return []
    return [
      {
        id: text(row.id) ?? text(row.attempt_id) ?? `attempt-${index + 1}`,
        outcome,
        judgedBy: text(row.judged_by) ?? text(row.judge) ?? 'core',
        counted: typeof row.counted === 'boolean' ? row.counted : null,
        at: text(row.finished_at) ?? text(row.started_at) ?? text(row.at) ?? text(row.time),
        evidence: text(row.evidence),
      },
    ]
  })
}

/** 资源 wire → 页面用的元数据；缺项一律给安全的默认值，永不抛异常。 */
export function normalizeCopilotAsset(value: unknown): CopilotAsset | null {
  const row = asRecord(value)
  if (!row) return null
  const id = text(row.id)
  const name = text(row.name)
  if (!id || !name) return null
  const meta = asRecord(row.meta) ?? {}
  const sourceKnown = isCopilotSource(meta.source)
  return {
    id,
    kind: text(row.kind) ?? 'copilot',
    name,
    description: text(row.description),
    meta: {
      source: sourceKnown ? (meta.source as CopilotSource) : 'user',
      sourceKnown,
      stageId: text(meta.stage_id) ?? text(meta.stage_name),
      title: text(meta.title),
      note: text(meta.note),
      parentId: text(meta.parent_id),
      siteId: text(meta.site_id),
      // 作业站导入时写 `site_link`（草稿服务）；另外两种是兜底的旧键名。
      siteUrl: text(meta.site_link) ?? text(meta.site_url) ?? text(meta.link),
      sessionId: text(meta.session_id),
      retain: flag(meta.retain),
      attempts: parseAttempts(meta.attempts),
    },
    enabled: row.enabled !== false,
    created_at: text(row.created_at),
    updated_at: text(row.updated_at),
  }
}

function normalizeList(value: unknown, query: CopilotListQuery): CopilotListPayload {
  const row = asRecord(value) ?? {}
  const items = Array.isArray(row.items) ? row.items : []
  return {
    items: items.flatMap((item) => {
      const asset = normalizeCopilotAsset(item)
      return asset ? [asset] : []
    }),
    total: count(row.total) ?? items.length,
    page: count(row.page) ?? query.page,
    size: count(row.size) ?? query.size,
  }
}

export async function fetchCopilots(query: CopilotListQuery, signal?: AbortSignal): Promise<CopilotListPayload> {
  const { data, error, response } = await copilotsApi.GET('/api/resources/copilots', {
    params: { query },
    signal,
  })
  if (error || data === undefined) throw toApiError(error, response)
  return normalizeList(data, query)
}

/** 详情：列表里没有作业 JSON，查看与下载都靠这一次请求。 */
export async function fetchCopilotDetail(assetId: string, signal?: AbortSignal): Promise<CopilotDetail> {
  const { data, error, response } = await copilotsApi.GET('/api/resources/copilots/{asset_id}', {
    params: { path: { asset_id: assetId } },
    signal,
  })
  if (error || data === undefined) throw toApiError(error, response)
  const row = asRecord(data)
  const asset = row ? normalizeCopilotAsset(row) : null
  if (!row || !asset) throw new Error('作业详情读不出来，请刷新列表后重试。')
  return { ...asset, content: 'content' in row ? row.content : null }
}

export async function deleteCopilot(assetId: string): Promise<boolean> {
  const { data, error, response } = await copilotsApi.DELETE('/api/resources/copilots/{asset_id}', {
    params: { path: { asset_id: assetId } },
  })
  if (error) throw toApiError(error, response)
  return data?.removed !== false
}

/**
 * 「保留」＝写入 `meta.retain`（规格 §9.11）：被保留的作业不会被每日清理删掉，
 * 可以再取消保留。返回更新后的资源，页面直接用返回值刷新那一行。
 */
export async function setCopilotRetain(assetId: string, retain: boolean): Promise<CopilotAsset | null> {
  const { data, error, response } = await copilotsApi.POST('/api/resources/copilots/{asset_id}/retain', {
    params: { path: { asset_id: assetId } },
    body: { retain },
  })
  if (error) throw toApiError(error, response)
  return normalizeCopilotAsset(data)
}

/**
 * 撤销本会话的作业授权（规格 §9.6）。后端幂等：没有生效中的授权同样返回成功。
 */
export async function revokeCopilotGrant(sessionId: string): Promise<void> {
  const { error, response } = await copilotsApi.DELETE('/api/agent/sessions/{session_id}/copilot-grant', {
    params: { path: { session_id: sessionId } },
  })
  if (error) throw toApiError(error, response)
}

/**
 * 会话 wire 里的 `copilot_grant` → 授权条要显示的字段。
 *
 * 后端形状按规格 §9.6 冻结（与 `atomic_grant` 并列，字段沿用 `copilot_grant` 表）；
 * 不是 `active`、已过期或字段读不出关卡时返回 null，授权条那一格就不显示。
 */
export function normalizeCopilotGrant(value: unknown, now = Date.now()): CopilotGrantView | null {
  const row = asRecord(value)
  if (!row) return null
  if (row.granted === false) return null
  const status = text(row.status)
  if (status && status !== 'active') return null
  const expiresAt = text(row.expires_at)
  if (expiresAt) {
    const expires = Date.parse(expiresAt)
    if (Number.isFinite(expires) && expires <= now) return null
  }
  const attemptsTotal = count(row.attempts_total) ?? count(row.attempts) ?? 0
  const attemptsUsed = count(row.attempts_used) ?? count(row.used) ?? 0
  return {
    grantId: text(row.grant_id) ?? text(row.id),
    stageId: text(row.stage_id),
    stageCode: text(row.stage_code),
    attemptsTotal,
    attemptsUsed,
    remainingAttempts: Math.max(attemptsTotal - attemptsUsed, 0),
    success: text(row.success),
    support: text(row.support),
    status,
    expiresAt,
  }
}

/**
 * 下载作业 JSON：内容来自详情端点，在前端拼一个 Blob 存成文件 —— 这样不必再开一个
 * 下载端点，也不会把作业内容塞进 URL 或剪贴板。文件名取作业名，非法字符换成下划线。
 */
export function downloadCopilotJson(detail: Pick<CopilotDetail, 'name' | 'content'>): string {
  const filename = `${detail.name.replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 64) || 'copilot'}.json`
  const blob = new Blob([JSON.stringify(detail.content ?? null, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  try {
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    anchor.rel = 'noopener'
    document.body.append(anchor)
    anchor.click()
    anchor.remove()
  } finally {
    URL.revokeObjectURL(url)
  }
  return filename
}
