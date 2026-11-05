import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Radar } from 'lucide-react'
import { Page } from '@/components/layout/Page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ListGroup } from '@/components/ui/list'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { ErrorAlert } from '@/features/agent/StatusAlerts'
import { toShownError } from '@/features/agent/format'
import { toApiError } from '@/api/errors'
import {
  ALERT_SEVERITY_LABELS,
  ALERT_TARGET_LABELS,
  WATCHDOG_MODE_LABELS,
  alertCheckLabel,
  alertSeverityTone,
  formatWatchdogTime,
  invalidateWatchdogAlerts,
  resolveWatchdogAlert,
  type AlertOut,
} from './api'
import { watchdogAlertsQuery, watchdogStatusQuery } from './queries'

const card = 'rounded-xl border bg-card shadow-card'

/** 告警对象的一段：种类 + id；没有目标 id 时只写种类。 */
function targetText(alert: AlertOut): string {
  const kind = ALERT_TARGET_LABELS[alert.target_kind] ?? alert.target_kind
  return alert.target_id ? `${kind}「${alert.target_id}」` : kind
}

/**
 * 「更多 › 值守」（M83 §4.6）。
 *
 * 上方是模式与上次巡检时间，下方是未解除告警。每行有「解除」按钮；解除后
 * 失效 `watchdogAlerts` 整片，值守页与待你处理一起刷新。空列表显示
 * 「没有未解除的告警」。
 */
export default function WatchdogPage() {
  const queryClient = useQueryClient()
  const status = useQuery(watchdogStatusQuery)
  const alerts = useQuery(watchdogAlertsQuery)

  const resolve = useMutation({
    mutationFn: (alertId: number) => resolveWatchdogAlert(alertId),
    onSuccess: () => invalidateWatchdogAlerts(queryClient),
    // 404 说明告警已经在别处解除；同样重取，避免把旧行留在列表里。
    onError: () => invalidateWatchdogAlerts(queryClient),
  })

  const items = alerts.data?.items ?? []

  const summary = status.isPending ? (
    <div className={card}><LoadingState label="正在读取值守概况…" /></div>
  ) : status.error ? (
    <div className={card}>
      <ErrorState
        title="值守概况读取失败"
        message={`读取值守概况失败：${toApiError(status.error).message}`}
        onRetry={() => void status.refetch()}
        retrying={status.isFetching}
      />
    </div>
  ) : status.data ? (
    <section aria-label="值守概况" className={`${card} flex flex-wrap items-center gap-x-8 gap-y-3 px-4 py-3`}>
      <div className="space-y-0.5">
        <p className="text-footnote text-muted-foreground">模式</p>
        <p className="text-sm font-medium">
          <Badge variant={status.data.mode === 'auto' ? 'warning' : 'neutral'}>
            {WATCHDOG_MODE_LABELS[status.data.mode]}
          </Badge>
        </p>
      </div>
      <div className="space-y-0.5">
        <p className="text-footnote text-muted-foreground">上次巡检</p>
        <p className="text-sm font-medium">
          {status.data.last_run_at ? formatWatchdogTime(status.data.last_run_at) : '还没有巡检'}
        </p>
      </div>
    </section>
  ) : null

  let content
  if (alerts.isPending) {
    content = <div className={card}><LoadingState label="正在读取告警…" /></div>
  } else if (alerts.error && items.length === 0) {
    content = (
      <div className={card}>
        <ErrorState
          title="告警读取失败"
          message={`读取告警失败：${toApiError(alerts.error).message}`}
          onRetry={() => void alerts.refetch()}
          retrying={alerts.isFetching}
        />
      </div>
    )
  } else if (items.length === 0) {
    content = (
      <div className={card}>
        <EmptyState icon={Radar} title="没有未解除的告警" description="检查发现异常时会出现在这里，条件消失后也会自动解除。" />
      </div>
    )
  } else {
    content = (
      <ListGroup title="未解除的告警">
        {items.map((alert) => (
          <div key={alert.id} data-watchdog-alert-id={alert.id} className="flex items-start gap-3 px-4 py-3">
            <div className="min-w-0 flex-1 space-y-1">
              <p className="flex flex-wrap items-center gap-2">
                <Badge variant={alertSeverityTone(alert.severity)}>
                  {ALERT_SEVERITY_LABELS[alert.severity] ?? alert.severity}
                </Badge>
                <span className="break-words text-sm font-medium text-foreground">{alert.message}</span>
              </p>
              <p className="break-words text-footnote text-muted-foreground">
                {alertCheckLabel(alert.check)} · {targetText(alert)} · {formatWatchdogTime(alert.created_at)}
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              loading={resolve.isPending && resolve.variables === alert.id}
              onClick={() => resolve.mutate(alert.id)}
            >
              解除
            </Button>
          </div>
        ))}
      </ListGroup>
    )
  }

  return (
    <Page title="值守" back="/more">
      <div className="space-y-4 pb-4">
        {resolve.error ? (
          <ErrorAlert
            error={toShownError(resolve.error)}
            onDismiss={() => resolve.reset()}
          />
        ) : null}
        {summary}
        {content}
      </div>
    </Page>
  )
}
