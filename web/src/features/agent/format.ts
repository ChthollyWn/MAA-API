import { toApiError } from '@/api/errors'
import type { AgentSession, JsonRecord, ShownError } from './types'

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

/** 与 `maa_api/services/webpush_service.py` 的动作表同名；认不出的工具只显示原名。 */
const TOOL_LABELS: Record<string, string> = {
  get_system_status: '读取系统状态',
  get_device_status: '读取设备状态',
  list_devices: '列出设备',
  get_screenshot: '截取设备画面',
  get_versions: '读取版本信息',
  get_logs: '读取日志',
  resolve_stage: '解析关卡',
  get_drop_stats: '读取掉落统计',
  list_task_types: '读取任务类型',
  get_pipeline: '读取流水线',
  list_pipelines: '列出流水线',
  get_queue: '读取队列',
  submit_pipeline: '提交任务流水线',
  stop_pipeline: '停止流水线',
  set_task_params: '修改任务参数',
  cancel_queued: '取消排队中的任务',
  list_schedules: '列出定时任务',
  create_schedule: '创建定时任务',
  update_schedule: '修改定时任务',
  delete_schedule: '删除定时任务',
  list_copilots: '列出作业',
  upload_copilot: '保存作业',
  set_infrast_plan: '设置基建排班',
  list_custom_tasks: '列出自定义任务',
  register_custom_task: '登记自定义任务',
  remove_custom_task: '移除自定义任务',
  check_updates: '检查更新',
  update_core: '更新 MaaCore 内核',
  update_resource: '更新资源',
  update_game: '更新游戏',
  restart_core: '重启 MaaCore 内核',
  reconnect_device: '重连设备',
  back_to_home: '返回游戏主界面',
  trigger_screencap: '截取游戏画面',
  click: '在游戏画面点击',
  swipe: '在游戏画面滑动',
  long_press: '在游戏画面长按',
  input_text: '向游戏输入文本',
  key_event: '发送按键事件',
  check_confirmation: '查询确认状态',
  run_task: '执行任务',
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
