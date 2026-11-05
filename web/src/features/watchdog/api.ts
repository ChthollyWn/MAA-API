import type { QueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { formatDateTime } from '@/features/goals/format'
import type { components } from '@/types/api'

/**
 * 值守页的接口与展示规则（M83 规格 §4.3、§4.6）。
 *
 * 三个端点：概况、未解除告警分页与解除。告警列表固定读第一页；未解除告警由
 * 检查项与目标去重，条数不会随历史增长。解除后由 `watchdog_alert` 事件或页面
 * 自己的失效把列表与待你处理一起刷新。
 */
export type WatchdogStatus = components['schemas']['WatchdogStatusOut']
export type AlertOut = components['schemas']['AlertOut']
export type AlertPage = components['schemas']['AlertPage']
export type AlertSeverity = components['schemas']['AlertSeverity']
export type AlertTargetKind = components['schemas']['AlertTargetKind']

/** 未解除告警一页取 50 条；检查项按「检查 × 目标」去重，正常远小于这个数。 */
export const WATCHDOG_ALERT_SIZE = 50

/** 待你处理只收严重告警；条数与页面第一页同口径。 */
export const WATCHDOG_CRITICAL_SIZE = 50

export const WATCHDOG_MODE_LABELS: Record<WatchdogStatus['mode'], string> = {
  record: '只记录',
  auto: '自动处理',
}

export const ALERT_SEVERITY_LABELS: Record<AlertSeverity, string> = {
  info: '信息',
  warn: '警告',
  critical: '严重',
}

/** 告警对象沿用项目术语：运行、工单、设备、内核、队列。 */
export const ALERT_TARGET_LABELS: Record<AlertTargetKind, string> = {
  run: '运行',
  ticket: '工单',
  device: '设备',
  core: '内核',
  queue: '队列',
}

/** 检查项的中文名；不把 `pipeline_no_callback` 这类代码名摆到界面上（11 §11）。 */
const CHECK_LABELS: Record<string, string> = {
  run_heartbeat_stale: '运行心跳停滞',
  ticket_stale: '工单停滞',
  ticket_churn: '工单反复重试',
  pipeline_no_callback: '流水线无回调',
  core_not_ready: '内核未就绪',
  queue_stuck: '队列卡住',
  ticket_no_progress: '工单无进展',
  ticket_drift: '工单偏离委托',
}

export function alertCheckLabel(check: string): string {
  return CHECK_LABELS[check] ?? '其它检查'
}

/** 严重度的徽章颜色；未知取值退回中性。 */
export function alertSeverityTone(severity: string): 'neutral' | 'warning' | 'danger' {
  if (severity === 'critical') return 'danger'
  if (severity === 'warn') return 'warning'
  return 'neutral'
}

/** 时间沿用目标页的同款格式；读不出时原样显示。 */
export const formatWatchdogTime = formatDateTime

export async function fetchWatchdogStatus(signal?: AbortSignal): Promise<WatchdogStatus> {
  const { data, error, response } = await api.GET('/api/agent/watchdog', { signal })
  if (error || !data) throw toApiError(error, response)
  return data
}

export interface WatchdogAlertFilters {
  /** false（默认）只看未解除；true 只看已解除。 */
  resolved?: boolean
  severity?: AlertSeverity
}

export async function fetchWatchdogAlerts(
  filters: WatchdogAlertFilters = {},
  page = 1,
  signal?: AbortSignal,
): Promise<AlertPage> {
  const { data, error, response } = await api.GET('/api/agent/watchdog/alerts', {
    params: {
      query: {
        ...(filters.resolved === undefined ? {} : { resolved: filters.resolved }),
        ...(filters.severity === undefined ? {} : { severity: filters.severity }),
        page,
        size: WATCHDOG_ALERT_SIZE,
      },
    },
    signal,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

/** 解除一条告警；不存在时后端回 404 `WATCHDOG_ALERT_NOT_FOUND`。 */
export async function resolveWatchdogAlert(alertId: number): Promise<AlertOut> {
  const { data, error, response } = await api.POST('/api/agent/watchdog/alerts/{alert_id}/resolve', {
    params: { path: { alert_id: alertId } },
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

/** 解除后让值守页与待你处理的所有告警缓存一起重取。 */
export function invalidateWatchdogAlerts(queryClient: QueryClient): Promise<unknown> {
  return queryClient.invalidateQueries({ queryKey: keys.agent.watchdogAlertsAll() })
}
