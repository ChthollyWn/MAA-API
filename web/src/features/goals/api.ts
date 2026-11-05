import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import type { components } from '@/types/api'

/**
 * 目标页的接口层（M82 规格 §4.3、§4.6）。
 *
 * 四个端点与 §4.3 一一对应：分页列表、新建、详情与改状态。错误统一转成
 * `ApiError`，页面只负责把 message 原样展示或弹 Toast。
 */
export type GoalDetail = components['schemas']['GoalDetail']
export type GoalPage = components['schemas']['GoalPage']
export type GoalSummary = components['schemas']['GoalSummary']
export type GoalCreate = components['schemas']['GoalCreate']
export type GoalUpdate = components['schemas']['GoalUpdate']
export type GoalStatus = components['schemas']['GoalStatus']
export type Milestone = components['schemas']['MilestoneOut']
export type GoalTicket = components['schemas']['GoalTicketOut']

/** 列表每页条数；与工单、事件记录同口径。 */
export const GOAL_PAGE_SIZE = 20

/** 一页目标；多值 `status` 直接传数组，openapi-fetch 序列化成重复参数。 */
export async function fetchGoalPage(
  statuses: readonly GoalStatus[],
  page = 1,
  signal?: AbortSignal,
): Promise<GoalPage> {
  const { data, error, response } = await api.GET('/api/agent/goals', {
    params: {
      query: { status: [...statuses], page, size: GOAL_PAGE_SIZE },
    },
    signal,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

/** 一个目标的详情；不存在报 404 `GOAL_NOT_FOUND`。 */
export async function fetchGoal(goalId: string, signal?: AbortSignal): Promise<GoalDetail> {
  const { data, error, response } = await api.GET('/api/agent/goals/{goal_id}', {
    params: { path: { goal_id: goalId } },
    signal,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

/** 新建目标；成功时后端同时建了规划工单与复盘 job。 */
export async function createGoal(body: GoalCreate): Promise<GoalDetail> {
  const { data, error, response } = await api.POST('/api/agent/goals', { body })
  if (error || !data) throw toApiError(error, response)
  return data
}

/** 暂停、恢复或取消目标。 */
export async function updateGoal(goalId: string, body: GoalUpdate): Promise<GoalDetail> {
  const { data, error, response } = await api.PUT('/api/agent/goals/{goal_id}', {
    params: { path: { goal_id: goalId } },
    body,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

/** 错误提示：优先服务端 message，空时给一句兜底。 */
export function describeGoalError(error: unknown): string {
  const failure = toApiError(error)
  return failure.message.trim() || '操作失败，请稍后重试。'
}
