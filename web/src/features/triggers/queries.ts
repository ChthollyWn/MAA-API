import { infiniteQueryOptions, queryOptions, type QueryClient } from '@tanstack/react-query'
import { keys } from '@/api/keys'
import {
  fetchAgentSpecs,
  fetchObservations,
  fetchTriggerRules,
  fetchTriggers,
  TRIGGER_PAGE_SIZE,
} from './api'

/** 事件规则页的共享查询（M81 §4.6）；写入后由页面失效 `triggerRules` 整片。 */
export const triggerRulesQuery = queryOptions({
  queryKey: keys.agent.triggerRules(),
  queryFn: fetchTriggerRules,
  staleTime: 15_000,
  retry: false,
})

/** 代理定义列表：编辑器只列 `kind=worker`，与技能页共用 `keys.agent.specs.all()`。 */
export const triggerSpecsQuery = queryOptions({
  queryKey: keys.agent.specs.all(),
  queryFn: fetchAgentSpecs,
  staleTime: Infinity,
  retry: false,
})

/** 观察记录列表；`reviewed` 决定「待审阅 / 已审阅」，页码交给 infinite query 追加。 */
export function observationsInfiniteQuery(reviewed: boolean) {
  return infiniteQueryOptions({
    queryKey: keys.agent.observations({ reviewed }),
    queryFn: ({ pageParam, signal }) => fetchObservations({ reviewed }, pageParam, signal),
    initialPageParam: 1,
    getNextPageParam: (last) => {
      const page = last.page ?? 1
      const size = last.size ?? TRIGGER_PAGE_SIZE
      return page * size < last.total ? page + 1 : undefined
    },
    retry: false,
  })
}

/** 事件记录列表；按 `id` 倒序分页，滚到底再追加。 */
export function triggersInfiniteQuery() {
  return infiniteQueryOptions({
    queryKey: keys.agent.triggers(),
    queryFn: ({ pageParam, signal }) => fetchTriggers({}, pageParam, signal),
    initialPageParam: 1,
    getNextPageParam: (last) => {
      const page = last.page ?? 1
      const size = last.size ?? TRIGGER_PAGE_SIZE
      return page * size < last.total ? page + 1 : undefined
    },
    retry: false,
  })
}

/** 写入、恢复默认、删除后让规则列表（含统计与建议）重取。 */
export function invalidateTriggerRules(queryClient: QueryClient): Promise<unknown> {
  return queryClient.invalidateQueries({ queryKey: keys.agent.triggerRules() })
}
