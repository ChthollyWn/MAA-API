import { Fragment, useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, Cpu, ListOrdered, RotateCw, Server, Smartphone, type LucideIcon } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { StatusDot } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Skeleton } from '@/components/ui/skeleton'
import { coreLabels, coreTone, deviceLabels, deviceTone, serviceUptime, type Tone } from '@/features/dashboard/format'
import type { useDashboard } from '@/features/dashboard/use-dashboard'
import { cn } from '@/lib/utils'

type Dashboard = ReturnType<typeof useDashboard>

interface StatusRowProps {
  icon: LucideIcon
  label: string
  value: string
  tone: Tone
  details: ReactNode[]
  to?: string
}

function StatusRow({ icon: Icon, label, value, tone, details, to }: StatusRowProps) {
  const body = (
    <>
      <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-surface-2 text-muted-foreground">
        <Icon aria-hidden="true" className="size-[1.125rem]" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-[0.9375rem] font-medium leading-5">{label}</span>
        <span className="mt-0.5 block break-words text-footnote text-muted-foreground">
          {details.filter(Boolean).map((detail, index) => (
            <Fragment key={index}>
              {index > 0 ? <span aria-hidden="true"> · </span> : null}
              <span>{detail}</span>
            </Fragment>
          ))}
        </span>
      </span>
      <span className="flex shrink-0 items-center gap-1.5 text-sm font-medium">
        <StatusDot tone={tone} pulse={tone === 'warning'} />
        {value}
      </span>
      {to ? <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground/70" /> : null}
    </>
  )
  const className = 'flex min-h-16 w-full items-center gap-3 px-4 py-3 text-left'
  if (to) {
    return <Link to={to} className={cn(className, 'transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring')}>{body}</Link>
  }
  return <div className={className}>{body}</div>
}

/** 首页的运行状态：设备、内核、服务、队列各一行，状态与关键数字一眼可见。 */
export function StatusOverview({ dashboard }: { dashboard: Dashboard }) {
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
  const deviceState = device?.state
  const canReconnect = deviceState === 'disconnected' || deviceState === 'unavailable'
  const retrying = deviceState === 'connecting' || deviceState === 'reconnecting'
  const loading = dashboard.healthQuery.isPending && !health

  return (
    <section aria-labelledby="runtime-status-title" className="space-y-2">
      <h2 id="runtime-status-title" className="px-1 text-footnote font-medium text-muted-foreground">运行状态</h2>
      {healthError ? (
        <InlineAlert
          tone="danger"
          action={<Button onClick={() => void dashboard.healthQuery.refetch()} size="sm" variant="outline">重试连接</Button>}
        >
          {health ? `状态更新失败：${toApiError(healthError).message}` : `无法连接服务：${toApiError(healthError).message}`}
        </InlineAlert>
      ) : null}

      {loading ? (
        <div role="status" aria-label="正在读取运行状态" className="space-y-px overflow-hidden rounded-xl border bg-card shadow-card">
          {[0, 1, 2, 3].map((index) => (
            <div key={index} className="flex items-center gap-3 px-4 py-3.5">
              <Skeleton className="size-9 rounded-lg" />
              <div className="flex-1 space-y-2"><Skeleton className="h-3.5 w-16" /><Skeleton className="h-3 w-32" /></div>
            </div>
          ))}
        </div>
      ) : (
        <div className="divide-y overflow-hidden rounded-xl border bg-card shadow-card">
          <StatusRow
            icon={Smartphone}
            label="设备"
            value={device ? deviceLabels[device.state] ?? `未知（${device.state}）` : '不可用'}
            tone={deviceTone(deviceState)}
            details={device
              ? [
                  device.address || '未配置 ADB 地址',
                  retrying ? `正在连接（第 ${device.retry.attempt} / ${device.retry.max} 次）` : null,
                  device.last_error && typeof device.last_error.message === 'string' ? `最近错误：${device.last_error.message}` : null,
                ]
              : ['设备状态尚未装配或暂不可用']}
          />
          <StatusRow
            icon={Cpu}
            label="内核"
            value={core ? coreLabels[core.state] ?? `未知（${core.state}）` : '不可用'}
            tone={coreTone(core?.state)}
            details={core ? [`PID ${core.pid ?? '—'} · 世代 ${core.generation}`] : ['内核运行状态尚未装配或暂不可用']}
          />
          <StatusRow
            icon={Server}
            label="服务"
            value={health ? (health.status === 'ok' ? '运行正常' : health.status) : '不可用'}
            tone={health ? (health.status === 'ok' ? 'success' : 'warning') : 'danger'}
            details={health ? [`版本 ${health.version}`, serviceUptime(health.started_at, now)] : ['服务状态不可用']}
          />
          <StatusRow
            icon={ListOrdered}
            label="队列"
            to="/tasks/queue"
            value={queue ? (queue.paused ? '已暂停' : '运行中') : '—'}
            tone={queue ? (queue.paused ? 'warning' : 'success') : 'neutral'}
            details={queue
              ? [`待处理 ${queue.pending} 条 · 正在运行 ${queue.running} 条`]
              : [dashboard.queueQuery.error ? `队列更新失败：${toApiError(dashboard.queueQuery.error).message}` : '正在读取队列…']}
          />
        </div>
      )}

      {dashboard.queueQuery.error && queue ? (
        <InlineAlert tone="warning" action={<Button onClick={() => void dashboard.queueQuery.refetch()} size="sm" variant="outline">重试读取队列</Button>}>
          队列更新失败：{toApiError(dashboard.queueQuery.error).message}
        </InlineAlert>
      ) : null}

      {canReconnect ? (
        <InlineAlert
          tone="warning"
          title={deviceState === 'unavailable' ? '自动重试已结束' : '设备未连接'}
          action={(
            <Button size="sm" loading={reconnect.isPending} onClick={() => reconnect.mutate()}>
              {reconnect.isPending ? null : <RotateCw aria-hidden="true" />}
              {reconnect.isPending ? '正在发送请求' : '重新连接设备'}
            </Button>
          )}
        >
          请确认模拟器已启动，再发起连接。
        </InlineAlert>
      ) : null}
      {reconnect.error ? <InlineAlert tone="danger">重连失败：{toApiError(reconnect.error).message}</InlineAlert> : null}
      {reconnect.isSuccess ? <InlineAlert tone="success">已发送重连请求，设备状态会自动更新。</InlineAlert> : null}
    </section>
  )
}
