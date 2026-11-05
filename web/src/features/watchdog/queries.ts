import { queryOptions } from '@tanstack/react-query'
import { keys } from '@/api/keys'
import {
  fetchWatchdogAlerts,
  fetchWatchdogStatus,
  WATCHDOG_ALERT_SIZE,
  WATCHDOG_CRITICAL_SIZE,
  type AlertSeverity,
} from './api'

/** 未解除告警第一页的过滤条件；状态进 key，页面与待你处理各取所需。 */
function openAlertFilters(severity?: AlertSeverity): Readonly<Record<string, unknown>> {
  return {
    resolved: false,
    ...(severity === undefined ? {} : { severity }),
    page: 1,
    size: severity === undefined ? WATCHDOG_ALERT_SIZE : WATCHDOG_CRITICAL_SIZE,
  }
}

/** 值守概况：模式与上次巡检时间；巡检每 60 秒触发一次，页面 30 秒重取。 */
export const watchdogStatusQuery = queryOptions({
  queryKey: keys.agent.watchdogStatus(),
  queryFn: ({ signal }) => fetchWatchdogStatus(signal),
  staleTime: 15_000,
  refetchInterval: 30_000,
  retry: false,
})

/** 值守页的未解除告警，按 `created_at` 倒序（后端排序）。 */
export const watchdogAlertsQuery = queryOptions({
  queryKey: keys.agent.watchdogAlerts(openAlertFilters()),
  queryFn: ({ signal }) => fetchWatchdogAlerts({ resolved: false }, 1, signal),
  staleTime: 15_000,
  retry: false,
})

/** 待你处理只读严重且未解除的告警；与值守页共用 `watchdogAlerts` 前缀。 */
export const criticalWatchdogAlertsQuery = queryOptions({
  queryKey: keys.agent.watchdogAlerts(openAlertFilters('critical')),
  queryFn: ({ signal }) => fetchWatchdogAlerts({ resolved: false, severity: 'critical' }, 1, signal),
  staleTime: 15_000,
  retry: false,
})
