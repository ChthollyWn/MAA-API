export const coreLabels: Record<string, string> = {
  stopped: '已停止',
  starting: '正在启动',
  ready: '运行中',
  crashed: '已崩溃',
  restarting: '正在重启',
  failed: '启动失败',
}

export const deviceLabels: Record<string, string> = {
  disconnected: '未连接',
  connecting: '正在连接',
  connected: '已连接',
  reconnecting: '正在重连',
  unavailable: '连接不可用',
}

export const pipelineStatusLabels: Record<string, string> = {
  pending: '等待执行',
  queued: '排队中',
  running: '正在执行',
  completed: '已完成',
  failed: '执行失败',
  cancelled: '已取消',
  skipped: '已跳过',
}

export type Tone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger'

export function coreTone(state: string | undefined): Tone {
  if (state === 'ready') return 'success'
  if (state === 'crashed' || state === 'failed') return 'danger'
  if (state === 'starting' || state === 'restarting') return 'warning'
  return 'neutral'
}

export function deviceTone(state: string | undefined): Tone {
  if (state === 'connected') return 'success'
  if (state === 'connecting' || state === 'reconnecting') return 'warning'
  if (state === 'unavailable' || state === 'disconnected') return 'danger'
  return 'neutral'
}

export function pipelineTone(status: string): Tone {
  if (status === 'running') return 'primary'
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'cancelled' || status === 'skipped') return 'neutral'
  return 'warning'
}

export function formatDuration(seconds: number): string {
  const wholeSeconds = Math.max(0, Math.floor(seconds))
  const days = Math.floor(wholeSeconds / 86_400)
  const hours = Math.floor((wholeSeconds % 86_400) / 3_600)
  const minutes = Math.floor((wholeSeconds % 3_600) / 60)
  const rest = wholeSeconds % 60
  return days > 0
    ? `${days} 天 ${hours} 小时`
    : hours > 0
      ? `${hours} 小时 ${minutes} 分钟`
      : minutes > 0
        ? `${minutes} 分 ${rest} 秒`
        : `${rest} 秒`
}

export function serviceUptime(startedAt: string | null | undefined, now: number): string {
  if (!startedAt) return '运行时长暂不可用'
  const timestamp = Date.parse(startedAt)
  if (!Number.isFinite(timestamp)) return '运行时长暂不可用'
  return `已运行 ${formatDuration((now - timestamp) / 1_000)}`
}
