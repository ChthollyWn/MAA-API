import { infiniteQueryOptions, queryOptions } from '@tanstack/react-query'
import { keys } from '@/api/keys'
import {
  fetchGoal,
  fetchGoalPage,
  GOAL_PAGE_SIZE,
  type GoalStatus,
} from './api'

/** 目标列表；状态范围进 key，三个页签各有一份缓存。 */
export function goalsInfiniteQuery(statuses: readonly GoalStatus[]) {
  return infiniteQueryOptions({
    queryKey: keys.agent.goals({ status: [...statuses] }),
    queryFn: ({ pageParam, signal }) => fetchGoalPage(statuses, pageParam, signal),
    initialPageParam: 1,
    getNextPageParam: (last) => {
      const page = last.page ?? 1
      const size = last.size ?? GOAL_PAGE_SIZE
      return page * size < last.total ? page + 1 : undefined
    },
    retry: false,
  })
}

/**
 * 目标详情：规划工单汇报与复盘会异步改目标，详情页每 30 秒重取一次
 * （M82 §4.6）；`ticket_status` 只失效列表。
 */
export function goalDetailQuery(goalId: string) {
  return queryOptions({
    queryKey: keys.agent.goal(goalId),
    queryFn: ({ signal }) => fetchGoal(goalId, signal),
    retry: false,
    refetchInterval: 30_000,
  })
}
