import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Activity, Check, CircleHelp, RotateCw, Server, Smartphone } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { type useDashboard } from '@/features/dashboard/use-dashboard'

type Dashboard = ReturnType<typeof useDashboard>

const coreLabels: Record<string, string> = {
  stopped: '已停止',
  starting: '正在启动',
  ready: '运行中',
  crashed: '已崩溃',
  restarting: '正在重启',
  failed: '启动失败',
}

const deviceLabels: Record<string, string> = {
  disconnected: '未连接',
  connecting: '正在连接',
  connected: '已连接',
  reconnecting: '正在重连',
  unavailable: '连接不可用',
}

function formatDuration(seconds: number): string {
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
        ? `${minutes} 分钟 ${rest} 秒`
        : `${rest} 秒`
}

function serviceUptime(startedAt: string | null | undefined, now: number): string {
  if (!startedAt) return '运行时长暂不可用'
  const timestamp = Date.parse(startedAt)
  if (!Number.isFinite(timestamp)) return '运行时长暂不可用'
  return `运行 ${formatDuration((now - timestamp) / 1_000)}`
}

export function RuntimeStatus({ dashboard }: { dashboard: Dashboard }) {
  const queryClient = useQueryClient()
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [])

  const reconnect = useMutation({
    mutationFn: async () => {
      const { error, response } = await api.POST('/api/device/reconnect', { body: {} })
      if (error) throw toApiError(error, response)
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: keys.system.health() })
    },
  })

  const health = dashboard.healthQuery.data
  const core = health?.core
  const device = health?.device
  const queue = dashboard.queue
  const healthError = dashboard.healthQuery.error
  const reconnectError = reconnect.error ? toApiError(reconnect.error).message : null
  const deviceState = device?.state ?? null
  const canReconnect = deviceState === 'disconnected' || deviceState === 'unavailable'

  return (
    <section aria-label="运行状态" className="space-y-3">
      {healthError ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warning/50 px-3 py-2 text-sm" role="alert">
          <span>{health ? `状态更新失败：${toApiError(healthError).message}` : `无法连接服务：${toApiError(healthError).message}`}</span>
          <Button onClick={() => void dashboard.healthQuery.refetch()} size="sm" variant="outline">重试连接</Button>
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-center gap-2 pb-2">
            <Server aria-hidden="true" className="size-4 text-muted-foreground" />
            <CardTitle className="text-sm">服务</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            {dashboard.healthQuery.isPending && !health ? (
              <p className="text-sm text-muted-foreground" role="status">正在读取服务状态…</p>
            ) : health ? (
              <>
                <p className="font-medium">{health.status === 'ok' ? '运行正常' : `状态：${health.status}`}</p>
                <p className="text-sm text-muted-foreground">版本 {health.version}</p>
                <p className="text-sm text-muted-foreground">{serviceUptime(health.started_at, now)}</p>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">服务状态不可用，请检查服务后重试。</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center gap-2 pb-2">
            <Activity aria-hidden="true" className="size-4 text-muted-foreground" />
            <CardTitle className="text-sm">MaaCore 内核</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            {dashboard.healthQuery.isPending && !health ? (
              <p className="text-sm text-muted-foreground" role="status">正在读取内核状态…</p>
            ) : core ? (
              <>
                <p className="font-medium">{coreLabels[core.state] ?? `未知状态（${core.state}）`}</p>
                <p className="text-sm text-muted-foreground">PID {core.pid ?? '—'} · 世代 {core.generation}</p>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">内核运行状态尚未装配或暂不可用。</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center gap-2 pb-2">
            <Smartphone aria-hidden="true" className="size-4 text-muted-foreground" />
            <CardTitle className="text-sm">设备</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {device ? (
              <>
                <p className="font-medium">{deviceLabels[device.state] ?? `未知状态（${device.state}）`}</p>
                <p className="break-all text-sm text-muted-foreground">{device.address || '未配置 ADB 地址'}</p>
                {device.state === 'disconnected' || device.state === 'unavailable' ? (
                  <p className="text-sm text-muted-foreground">
                    {device.state === 'unavailable'
                      ? '自动重试已结束。请确认模拟器已启动，再手动重新连接。'
                      : '请确认模拟器已启动，再发起连接。'}
                  </p>
                ) : null}
                {['connecting', 'reconnecting'].includes(device.state) ? (
                  <p className="text-sm text-muted-foreground">
                    正在连接（第 {device.retry.attempt} / {device.retry.max} 次）
                  </p>
                ) : null}
                {canReconnect ? (
                  <Button disabled={reconnect.isPending} onClick={() => reconnect.mutate()} size="sm" variant="outline">
                    <RotateCw aria-hidden="true" />{reconnect.isPending ? '正在发送请求' : '重新连接设备'}
                  </Button>
                ) : null}
                {reconnectError ? <p className="text-sm text-destructive" role="alert">重连失败：{reconnectError}</p> : null}
                {reconnect.isSuccess ? <p className="text-sm" role="status">已发送重连请求，设备状态会自动更新。</p> : null}
                {device.last_error && typeof device.last_error.message === 'string' ? (
                  <p className="text-xs text-muted-foreground">最近错误：{device.last_error.message}</p>
                ) : null}
              </>
            ) : dashboard.healthQuery.isPending ? (
              <p className="text-sm text-muted-foreground" role="status">正在读取设备状态…</p>
            ) : (
              <p className="text-sm text-muted-foreground">设备状态尚未装配或暂不可用。</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center gap-2 pb-2">
            <CircleHelp aria-hidden="true" className="size-4 text-muted-foreground" />
            <CardTitle className="text-sm">任务队列</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            {dashboard.queueQuery.error ? (
              <p className="text-sm text-destructive" role="alert">队列更新失败：{toApiError(dashboard.queueQuery.error).message}</p>
            ) : null}
            {queue ? (
              <>
                <p className="font-medium">{queue.paused ? '队列已暂停' : '队列运行中'}</p>
                <p className="text-sm text-muted-foreground">待处理 {queue.pending} 条 · 正在运行 {queue.running} 条</p>
              </>
            ) : dashboard.queueQuery.isPending ? (
              <p className="text-sm text-muted-foreground" role="status">正在读取队列…</p>
            ) : (
              <p className="text-sm text-muted-foreground">队列状态暂不可用。</p>
            )}
            {dashboard.queueQuery.error ? (
              <Button onClick={() => void dashboard.queueQuery.refetch()} size="sm" variant="outline">重试读取队列</Button>
            ) : null}
            {queue && dashboard.queueQuery.isFetching ? <p className="text-xs text-muted-foreground"><Check aria-hidden="true" className="mr-1 inline size-3" />正在同步</p> : null}
          </CardContent>
        </Card>
      </div>
    </section>
  )
}
