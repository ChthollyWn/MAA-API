import { queryOptions } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'

/**
 * 定时任务列表的共享查询，返回 `ScheduleView[]`。
 * key 挂在 `['schedules']` 之下：对 `['schedules']` 的失效会一并刷新它。
 */
export const schedulesQuery = queryOptions({
  queryKey: ['schedules', 'list'] as const,
  queryFn: async () => {
    const { data, error, response } = await api.GET('/api/schedules')
    if (error) throw toApiError(error, response)
    return data?.items ?? []
  },
  staleTime: 30_000,
})
