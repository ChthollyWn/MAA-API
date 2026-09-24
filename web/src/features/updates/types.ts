export type UpdateTarget = 'core' | 'resource' | 'game'
export type ResourceChannel = 'ota' | 'repo' | 'all'
export type GameChannel = 'Official' | 'Bilibili'

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

export function recordIsActive(record: UpdateRecord | null | undefined): boolean {
  if (!record) return false
  return !['completed', 'complete', 'succeeded', 'success', 'done', 'failed', 'cancelled', 'canceled', 'skipped'].includes(record.status.toLowerCase())
}

export function recordCanCancel(record: UpdateRecord): boolean {
  return recordIsActive(record) && record.phase?.toLowerCase() === 'downloading'
}

export function errorMessage(value: unknown): string {
  if (value instanceof Error) return value.message
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') {
    const error = value as Record<string, unknown>
    const message = asText(error.message) ?? asText(error.detail) ?? asText(error.error)
    const code = asText(error.code)
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
