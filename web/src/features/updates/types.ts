import type { StatusTone } from '@/components/ui/badge'

export type UpdateTarget = 'core' | 'resource' | 'game'
export type ResourceChannel = 'ota' | 'repo' | 'all'
export type GameChannel = 'Official' | 'Bilibili'
export type ReloadMode = 'wait' | 'force' | 'defer'

export interface UpdateChannelState {
  channel?: string
  current?: string | null
  latest?: string | null
  available?: boolean | null
  error?: string | null
  last_checked_at?: string | null
  last_synced_at?: string | null
  remote_size?: number | null
  [key: string]: unknown
}

export interface UpdateTargetState {
  target: UpdateTarget
  current?: string | null
  latest?: string | null
  available?: boolean | null
  error?: string | null
  reload_pending?: boolean
  channels?: Partial<Record<ResourceChannel | GameChannel, UpdateChannelState>>
}

export interface UpdateRecord {
  id: string
  target: UpdateTarget
  status: string
  phase?: string | null
  percent?: number | null
  progress?: number | null
  bytes_downloaded?: number | null
  bytes_done?: number | null
  bytes_total?: number | null
  bytes_per_second?: number | null
  speed?: number | null
  eta_seconds?: number | null
  eta?: number | null
  current_version?: string | null
  target_version?: string | null
  from_version?: string | null
  to_version?: string | null
  error_code?: string | null
  error_message?: string | null
  created_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  log?: unknown
  [key: string]: unknown
}

export interface UpdateStatusResponse {
  updates?: Partial<Record<UpdateTarget, UpdateTargetState>>
  checked_at?: string | null
  cached?: boolean
  running?: UpdateRecord | null
}

export interface UpdateHistoryResponse {
  items?: UpdateRecord[]
  total?: number
}

/** 选项面板里的选择；游戏渠道在卡片上选。 */
export interface UpdateSelection {
  resourceChannel: ResourceChannel
  reloadMode: ReloadMode
  gameChannel: GameChannel
}

export interface UpdateOptions extends UpdateSelection {
  forceInterrupt: boolean
}

export const targetLabels: Record<UpdateTarget, string> = {
  core: 'MAA 内核',
  resource: '活动资源',
  game: '游戏本体',
}

export const gameChannelLabels: Record<GameChannel, string> = {
  Official: '官服',
  Bilibili: 'B 服',
}

/** 中文后面紧跟拉丁字母开头的名称时补一个空格，例如「更新 MAA 内核」。 */
export function withLeadingSpace(label: string): string {
  return /^[A-Za-z0-9]/.test(label) ? ` ${label}` : label
}

export function asText(value: unknown): string | null {
  if (typeof value === 'string' && value.trim()) return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return null
}

export function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function formatDate(value: unknown): string {
  if (typeof value !== 'string' || value.length === 0) return '暂无记录'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  }).format(date)
}

export function formatBytes(value: unknown): string | null {
  const bytes = asNumber(value)
  if (bytes === null || bytes < 0) return null
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let amount = bytes
  let unit = -1
  do {
    amount /= 1024
    unit += 1
  } while (amount >= 1024 && unit < units.length - 1)
  return `${amount.toFixed(amount >= 10 ? 0 : 1)} ${units[unit]}`
}

export function formatDuration(value: unknown): string | null {
  const seconds = asNumber(value)
  if (seconds === null || seconds < 0) return null
  const total = Math.ceil(seconds)
  if (total < 60) return `${total} 秒`
  const minutes = Math.ceil(total / 60)
  if (minutes < 60) return `${minutes} 分钟`
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`
}

export function phaseLabel(phase: unknown): string {
  const labels: Record<string, string> = {
    pending: '排队等待开始',
    running: '正在更新',
    checking: '正在检查版本',
    downloading: '正在下载',
    verifying: '正在校验文件',
    waiting_idle: '等待任务空闲',
    applying: '正在应用更新',
    restarting: '正在重启服务',
    done: '更新完成',
    failed: '更新失败',
    cancelled: '已取消',
  }
  return typeof phase === 'string' ? labels[phase.toLowerCase()] ?? phase : '等待更新进度'
}

/** 已结束记录里的阶段名：说明停在哪一步，而不是「正在…」。 */
export function phaseStepLabel(phase: unknown): string | null {
  const labels: Record<string, string> = {
    checking: '检查版本',
    downloading: '下载',
    verifying: '校验文件',
    waiting_idle: '等待任务空闲',
    applying: '应用更新',
    restarting: '重启服务',
    done: '完成',
    failed: '失败',
    cancelled: '已取消',
  }
  return typeof phase === 'string' && phase ? labels[phase.toLowerCase()] ?? phase : null
}

export function recordStatus(status: string): { label: string; tone: StatusTone } {
  const value = status.toLowerCase()
  const labels: Record<string, string> = {
    pending: '等待中',
    running: '执行中',
    success: '已完成',
    succeeded: '已完成',
    completed: '已完成',
    complete: '已完成',
    done: '已完成',
    failed: '失败',
    cancelled: '已取消',
    canceled: '已取消',
    skipped: '已跳过',
  }
  const label = labels[value] ?? status
  if (value === 'failed') return { label, tone: 'danger' }
  if (label === '已完成') return { label, tone: 'success' }
  if (value === 'pending' || value === 'running') return { label, tone: 'primary' }
  return { label, tone: 'neutral' }
}

export function recordVersion(record: UpdateRecord): string | null {
  const from = asText(record.from_version) ?? asText(record.current_version)
  const to = asText(record.to_version) ?? asText(record.target_version)
  if (from && to) return `${from} → ${to}`
  return to ?? from
}

export function recordLogText(log: unknown): string | null {
  if (log === undefined || log === null || log === '') return null
  if (typeof log === 'string') return log
  if (Array.isArray(log) && log.every((line) => typeof line === 'string')) return log.join('\n')
  return JSON.stringify(log, null, 2)
}

export function recordIsActive(record: UpdateRecord | null | undefined): boolean {
  if (!record) return false
  return !['completed', 'complete', 'succeeded', 'success', 'done', 'failed', 'cancelled', 'canceled', 'skipped'].includes(record.status.toLowerCase())
}

export function recordCanCancel(record: UpdateRecord): boolean {
  return recordIsActive(record) && record.phase?.toLowerCase() === 'downloading'
}

export function recordIsFailed(record: UpdateRecord): boolean {
  return record.status.toLowerCase() === 'failed'
}

/** 状态芯片：有可用更新优先显示「可更新」，部分通道检查失败时由通道行各自说明。 */
export function availabilityStatus(available: boolean | null | undefined, error?: unknown): { label: string; tone: StatusTone } {
  if (available === true) return { label: '可更新', tone: 'primary' }
  if (error || available === null) return { label: '检查失败', tone: 'warning' }
  if (available === false) return { label: '已最新', tone: 'success' }
  return { label: '尚未检查', tone: 'neutral' }
}

/** 所选范围或渠道是否有更新：true 可更新、false 已最新、null 检查失败、undefined 尚未检查。 */
export function selectionAvailability(target: UpdateTarget, state: UpdateTargetState | undefined, selection: UpdateSelection): boolean | null | undefined {
  if (target === 'resource') {
    return selection.resourceChannel === 'all' ? state?.available : state?.channels?.[selection.resourceChannel]?.available
  }
  if (target === 'game') return state?.channels?.[selection.gameChannel]?.available ?? state?.available
  return state?.available
}

export function selectionError(target: UpdateTarget, state: UpdateTargetState | undefined, selection: UpdateSelection): string | null | undefined {
  if (target === 'resource') {
    return selection.resourceChannel === 'all' ? state?.error : state?.channels?.[selection.resourceChannel]?.error
  }
  if (target === 'game') return state?.channels?.[selection.gameChannel]?.error ?? state?.error
  return state?.error
}

export function canTrigger(target: UpdateTarget, state: UpdateTargetState | undefined, selection: UpdateSelection): boolean {
  if (target === 'game') return state !== undefined
  if (target === 'resource') return selectionAvailability(target, state, selection) === true || state?.reload_pending === true
  // 内核子进程停止时版本检查可能返回 null；docs/07 §7 要求此时仍能安装或更新 stable 内核。
  return state !== undefined && state.available !== false
}

export function actionLabel(target: UpdateTarget, state: UpdateTargetState | undefined, selection: UpdateSelection): string {
  if (target === 'core') return state?.latest ? `更新至 ${state.latest}` : '安装 stable 内核'
  if (target === 'resource') {
    if (state?.reload_pending && selection.reloadMode === 'force') return '立即生效'
    if (selection.resourceChannel === 'ota') return '更新 OTA 资源'
    if (selection.resourceChannel === 'repo') return '更新资源仓库'
    return '更新活动资源'
  }
  return selectionAvailability(target, state, selection) === false ? '重新安装' : '下载并安装'
}

export function interruptsPipeline(target: UpdateTarget, options: UpdateOptions): boolean {
  return options.forceInterrupt || (target === 'resource' && options.reloadMode === 'force')
}

/** 会中断流水线时，确认按钮直接写明后果。 */
export function interruptActionLabel(target: UpdateTarget, state: UpdateTargetState | undefined, selection: UpdateSelection): string {
  if (target === 'core') return `中断流水线并${state?.latest ? '更新' : '安装'}`
  if (target === 'resource') return `中断流水线并${state?.reload_pending && selection.reloadMode === 'force' ? '立即生效' : '更新'}`
  return `中断流水线并${selectionAvailability(target, state, selection) === false ? '重新安装' : '安装'}`
}

/** 卡片主操作打开面板时的初始选择：资源已下载待生效且没有新版本时，默认「立即生效」。 */
export function initialSelection(target: UpdateTarget, state: UpdateTargetState | undefined, gameChannel: GameChannel): UpdateSelection {
  const reloadOnly = target === 'resource' && state?.reload_pending === true && state.available !== true
  return { resourceChannel: 'all', reloadMode: reloadOnly ? 'force' : 'wait', gameChannel }
}

export function errorMessage(value: unknown): string {
  if (value instanceof Error) return value.message
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') {
    const error = value as Record<string, unknown>
    const envelope = error.error && typeof error.error === 'object' ? error.error as Record<string, unknown> : null
    const message = asText(error.message) ?? asText(error.detail) ?? asText(envelope?.message) ?? asText(error.error)
    const code = asText(error.code) ?? asText(envelope?.code)
    if (message && code) return `${message}（${code}）`
    if (message) return message
    if (code) return `请求失败（${code}）`
    try {
      return JSON.stringify(value)
    } catch {
      return '请求失败，请稍后重试。'
    }
  }
  return '请求失败，请稍后重试。'
}
