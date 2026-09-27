import type { QueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { ScheduleView, ScheduleWrite } from './payload'
import { schedulesQuery } from './queries'

export const SCHEDULES_PATH = '/tasks/schedules'
export const NEW_SCHEDULE_PATH = `${SCHEDULES_PATH}/new`

export function schedulePath(id: string): string {
  return `${SCHEDULES_PATH}/${encodeURIComponent(id)}`
}

export async function saveSchedule(id: string | null, body: ScheduleWrite): Promise<ScheduleView> {
  const result = id
    ? await api.PUT('/api/schedules/{schedule_id}', { params: { path: { schedule_id: id } }, body })
    : await api.POST('/api/schedules', { body })
  if (result.error || !result.data) throw toApiError(result.error, result.response)
  return result.data
}

export async function setScheduleEnabled(id: string, enabled: boolean): Promise<ScheduleView> {
  const { data, error, response } = await api.PATCH('/api/schedules/{schedule_id}', { params: { path: { schedule_id: id } }, body: { enabled } })
  if (error || !data) throw toApiError(error, response)
  return data
}

export async function runSchedule(id: string): Promise<void> {
  const { error, response } = await api.POST('/api/schedules/{schedule_id}/run', { params: { path: { schedule_id: id } }, body: {} })
  if (error) throw toApiError(error, response)
}

export async function deleteSchedule(id: string): Promise<void> {
  const { error, response } = await api.DELETE('/api/schedules/{schedule_id}', { params: { path: { schedule_id: id } } })
  if (error) throw toApiError(error, response)
}

/** 在共享的定时任务列表缓存里替换、插入或删除一条，随后整表重新读取。 */
export function updateScheduleCache(queryClient: QueryClient, change: { saved: ScheduleView } | { removedId: string }): void {
  queryClient.setQueryData(schedulesQuery.queryKey, (rows) => {
    if (!rows) return rows
    if ('removedId' in change) return rows.filter((row) => row.id !== change.removedId)
    const exists = rows.some((row) => row.id === change.saved.id)
    return exists ? rows.map((row) => (row.id === change.saved.id ? change.saved : row)) : [change.saved, ...rows]
  })
  void queryClient.invalidateQueries({ queryKey: ['schedules'] })
}

/** 立即运行后刷新队列、当前流水线与定时任务（最近结果）。 */
export function refreshAfterRun(queryClient: QueryClient): Promise<unknown> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: keys.queue.all() }),
    queryClient.invalidateQueries({ queryKey: keys.pipelines.current() }),
    queryClient.invalidateQueries({ queryKey: ['schedules'] }),
  ])
}
