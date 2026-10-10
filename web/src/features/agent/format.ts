import { toApiError } from '@/api/errors'
import type { AgentQuestionOption, AgentSession, JsonRecord, ShownError } from './types'

export const LLM_SETTINGS_PATH = '/more/settings/agent'

export function isRecord(value: unknown): value is JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export function stringValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

export function parseJson(value: string | null | undefined): unknown {
  if (!value) return null
  try { return JSON.parse(value) as unknown } catch { return value }
}

export function pretty(value: unknown): string {
  try { return JSON.stringify(value, null, 2) ?? String(value) }
  catch { return String(value) }
}

export function sessionTitle(session: Pick<AgentSession, 'title' | 'incident_key'>): string {
  if (session.title?.trim()) return session.title
  return session.incident_key ? '自动诊断会话' : '新会话'
}

/**
 * 会话累计 token 的展示：**大于等于 0.1M 一律按百万读**（用户要求的统一单位），
 * 小于 0.1M 用 K，再小就原样显示，避免出现 `0.001M` 这种读不出量级的写法。
 *
 * 每个量级最多两位有效数字并去掉多余的 `.0`：`510195` → `0.5M`、`1240000` → `1.2M`、
 * `240000000` → `240M`。换算会损失精度，所以只用于概览；精确值仍可从会话详情的
 * `total_tokens` 读到。
 */
export function formatTokenCount(value: unknown): string {
  const tokens = typeof value === 'number' && Number.isFinite(value) ? Math.max(value, 0) : 0
  const scaled = (divisor: number, suffix: string) => {
    const amount = tokens / divisor
    // 两位有效数字：0.51M 与 240M 都读得出量级。只在小数形式下去尾零，
    // 否则 240 会被当成「末尾多余的 0」削成 24。
    const text = amount >= 10 ? amount.toFixed(0) : amount.toFixed(1).replace(/\.0$/, '')
    return `${text}${suffix}`
  }
  if (tokens >= 100_000) return scaled(1_000_000, 'M')
  if (tokens >= 1_000) return scaled(1_000, 'K')
  return String(Math.trunc(tokens))
}

export function expiryLabel(value?: string | null): string {
  if (!value) return '有效期未知'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : `有效至 ${new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' }).format(date)}`
}

/** 会话列表里的更新时间：今天只显示时刻，今年显示月日，更早显示完整日期。 */
export function formatSessionTime(value: string | null | undefined, now = new Date()): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  if (date.toDateString() === now.toDateString()) {
    return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(date)
  }
  if (date.getFullYear() === now.getFullYear()) {
    return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' }).format(date)
  }
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short' }).format(date)
}

/** 会话授权的剩余时间；已经过期时返回 null。 */
export function grantRemainingLabel(expiresAt: string | null | undefined, now: number): string | null {
  if (!expiresAt) return '有效期未知'
  const expires = Date.parse(expiresAt)
  if (!Number.isFinite(expires)) return '有效期未知'
  const minutes = Math.ceil((expires - now) / 60_000)
  if (minutes <= 0) return null
  if (minutes < 60) return `剩余 ${minutes} 分钟`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `剩余 ${hours} 小时 ${rest} 分钟` : `剩余 ${hours} 小时`
}

/**
 * 提问卡的剩余时间：秒级倒计时（用户要在 10 分钟内点完，只给「分钟」看不出在走）。
 * 已经过期或没有有效期时返回 null，由调用方切换成超时态。
 */
export function questionRemainingLabel(expiresAt: string | null | undefined, now: number): string | null {
  if (!expiresAt) return null
  const expires = Date.parse(expiresAt)
  if (!Number.isFinite(expires)) return null
  const remaining = expires - now
  if (remaining <= 0) return null
  const seconds = Math.ceil(remaining / 1000)
  const minutes = Math.floor(seconds / 60)
  return minutes > 0 ? `剩余 ${minutes} 分 ${seconds % 60} 秒` : `剩余 ${seconds} 秒`
}

/** SSE 的 options 来自模型给的 JSON：进卡片前收敛成 label/description 两个字符串。 */
export function parseQuestionOptions(value: unknown): AgentQuestionOption[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (!isRecord(item) || typeof item.label !== 'string' || !item.label) return []
    return [{ label: item.label, description: typeof item.description === 'string' ? item.description : null }]
  })
}

/** 与 `maa_api/services/webpush_service.py` 的动作表同名；认不出的工具只显示原名。 */
const TOOL_LABELS: Record<string, string> = {
  get_system_status: '读取系统状态',
  get_device_status: '读取设备状态',
  list_devices: '列出设备',
  get_screenshot: '截取设备画面',
  get_versions: '读取版本信息',
  get_logs: '读取日志',
  read_knowledge: '查阅知识库',
  resolve_stage: '解析关卡',
  get_activity_stages: '查当期活动',
  get_stage: '查关卡情报',
  search_prts: '搜索 PRTS',
  get_prts_page: '读 PRTS 页面',
  get_operator: '查干员',
  get_enemy: '查敌人',
  get_item: '查材料',
  get_drop_rates: '查掉落率',
  get_operator_box: '查我的干员',
  get_gacha_pools: '查卡池',
  get_story: '查剧情',
  get_roguelike_info: '查集成战略',
  get_building_skills: '查基建技能',
  get_infrast_candidates: '查基建候选干员',
  list_infrast_plans: '查基建方案',
  update_user_profile: '修改用户档案',
  get_depot: '查仓库',
  update_operator_box: '修改干员数据',
  plan_upgrade: '算养成规划',
  start_upgrade_plan: '启动养成计划',
  list_upgrade_plans: '查养成计划',
  cancel_upgrade_plan: '停止养成计划',
  get_drop_stats: '读取掉落统计',
  list_task_types: '读取任务类型',
  get_pipeline: '读取流水线',
  list_pipelines: '列出流水线',
  wait_pipeline: '等待流水线结束',
  get_queue: '读取队列',
  submit_pipeline: '提交流水线',
  stop_pipeline: '停止流水线',
  set_task_params: '修改任务参数',
  cancel_queued: '取消排队的流水线',
  list_schedules: '列出定时任务',
  create_schedule: '新建定时任务',
  update_schedule: '修改定时任务',
  delete_schedule: '删除定时任务',
  list_copilots: '列出作业',
  upload_copilot: '保存作业',
  set_infrast_plan: '设置基建排班',
  list_custom_tasks: '列出自定义任务',
  register_custom_task: '登记自定义任务',
  remove_custom_task: '移除自定义任务',
  check_updates: '检查更新',
  update_core: '更新内核',
  update_resource: '更新资源',
  update_game: '更新游戏',
  restart_core: '重启内核',
  reconnect_device: '重连设备',
  back_to_home: '返回游戏主界面',
  trigger_screencap: '截取游戏画面',
  click: '在游戏画面点击',
  swipe: '在游戏画面滑动',
  long_press: '在游戏画面长按',
  input_text: '向游戏输入文本',
  key_event: '发送按键事件',
  check_confirmation: '查询确认状态',
  ask_user: '向你提问',
  send_screenshot: '发送截图',
  annotate_screenshot: '圈选截图',
  send_item_icons: '发送物品图标',
  send_stage_map: '发送关卡示意图',
  send_chart: '发送图表',
}

export function toolLabel(name: string): string | undefined {
  return TOOL_LABELS[name]
}

// ---------------------------------------------------------------------------
// 来源信封（规格 §3.4、§4.2）：工具结果里的 `sources` → 工具卡上的来源标签
// ---------------------------------------------------------------------------

/** 前端只为这些主机生成外链（规格 §4.2、附录 D）；其余主机只显示文字。 */
export const SOURCE_LINK_HOSTS: readonly string[] = [
  'prts.wiki',
  'prts.plus',
  'zoot.plus',
  'penguin-stats.io',
  'penguin-stats.cn',
  'github.com',
]

/** 一次结果最多渲染的来源标签数；信封里的 `sources` 正常只有几条。 */
export const SOURCE_TAG_LIMIT = 8

/** 来源标签的渲染输入；字段名按信封闭环，但只留渲染要用的（规格 §3.4）。 */
export interface ToolSource {
  kind: string
  label: string
  title: string
  /** 已过白名单的外链；主机不在白名单、协议不是 https 或字段缺失时是 null。 */
  url: string | null
  /** ISO 时间串，展示时走 `formatFetchedAt`；解析不了就原样显示。 */
  fetchedAt: string
  cached: boolean
  stale: boolean
}

/**
 * 外链白名单（规格 §4.2、附录 D）：只认 https 与六个主机及其子域。
 * 其余主机只显示文字，这里返回 null，不生成可点链接。
 */
export function sourceLinkHref(value: unknown): string | null {
  if (typeof value !== 'string' || !value.trim()) return null
  let parsed: URL
  try {
    parsed = new URL(value)
  } catch {
    return null
  }
  if (parsed.protocol !== 'https:') return null
  const host = parsed.hostname.toLowerCase()
  const allowed = SOURCE_LINK_HOSTS.some((candidate) => host === candidate || host.endsWith(`.${candidate}`))
  return allowed ? value : null
}

/** 工具结果体：`{result: {...}}` / `{error: {...}}`（与 media.ts 的 `resultBody` 同口径）。 */
function toolResultBody(input: unknown): JsonRecord | null {
  if (!isRecord(input)) return null
  if ('result' in input) return isRecord(input.result) ? input.result : null
  return input
}

function sourceText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

/**
 * 解析一次工具结果里的来源信封（规格 §3.4）。
 *
 * 与媒体卡同一套纪律：只做「解析 + 防御」，认不出的条目直接丢掉，永不抛异常。
 * 最差的结果是没有标签，不渲染总比白屏好。`url` 只保留白名单主机的地址（`sourceLinkHref`）。
 */
export function parseToolSources(result: unknown): ToolSource[] {
  const raw = toolResultBody(result)?.sources
  if (!Array.isArray(raw)) return []
  const sources: ToolSource[] = []
  for (const entry of raw) {
    if (sources.length >= SOURCE_TAG_LIMIT) break
    if (!isRecord(entry)) continue
    const kind = sourceText(entry.kind)
    const label = sourceText(entry.label) || kind
    if (!label) continue
    sources.push({
      kind,
      label,
      title: sourceText(entry.title),
      url: sourceLinkHref(entry.url),
      fetchedAt: sourceText(entry.fetched_at),
      cached: entry.cached === true,
      stale: entry.stale === true,
    })
  }
  return sources
}

/** 外服提示（规格 §4.3、§5.12）：结果里的 `region_note`，空串视为没有。 */
export function toolRegionNote(result: unknown): string | null {
  return sourceText(toolResultBody(result)?.region_note) || null
}

const fetchedAtFormat = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' })

/** 来源标签上的抓取时间：本地时区、精确到分钟（规格 §4.2）。解析不了就原样显示。 */
export function formatFetchedAt(value: string): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : fetchedAtFormat.format(date)
}

export function toShownError(error: unknown, response?: Response): ShownError {
  const apiError = toApiError(error, response)
  return { message: apiError.message || '请求失败，请稍后重试。', code: apiError.code }
}

const LLM_SETTINGS_CODES = new Set(['LLM_NOT_CONFIGURED', 'LLM_ENCRYPTION_KEY_INVALID', 'LLM_REQUEST_FAILED'])
const LLM_SETTINGS_TEXT = /LLM|密钥|模型配置|base_url|api[_ ]?key|function calling/i

/** LLM 配置、密钥或模型能力相关的错误，需要给出「前往设置检查 LLM 配置」。 */
export function needsLlmSettings(error: ShownError | null | undefined): boolean {
  if (!error) return false
  return (error.code !== undefined && LLM_SETTINGS_CODES.has(error.code)) || LLM_SETTINGS_TEXT.test(error.message)
}
