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
