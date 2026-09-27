import { queryOptions } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'

/** `/api/system/health` 的共享查询：路由守卫、登录页、外壳与首页共用同一份缓存。 */
export const healthQueryOptions = queryOptions({
  queryKey: keys.system.health(),
  queryFn: async () => {
    const { data, error, response } = await api.GET('/api/system/health')
    if (error) throw toApiError(error, response)
    return data
  },
  staleTime: 30_000,
})
