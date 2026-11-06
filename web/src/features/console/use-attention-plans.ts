import type { PlanView } from '@/features/tickets/workspace-api'
import type { ShownError } from '@/features/agent/types'
import type { PlanApprovalInput } from '@/features/tickets/workspace-api'
import type { TicketView } from './api'

/**
 * 指挥台「待你处理」里的计划批准来源（spec `docs/v3/specs/console-plan-approval.md` §4.3）。
 *
 * 只在工单 `waiting`、`human_action.type=decision` 且 `ref_id` 指向一份待批准计划时出现；
 * 工单页的 `PlanPanel` 保留为第二入口。
 */
export interface PlanAttentionView {
  ticket: TicketView
  plan: PlanView
  workerSessionId: string
}

/** `useAttentionPlans` 的返回值：待批准计划项、被抑制的工单 id 与加载态。 */
export interface PlanAttentionSource {
  items: PlanAttentionView[]
  /**
   * 计划已经不再待批准（刚批准/退回，Worker 还没汇报清 `human_action`）的工单 id。
   * 这些工单暂时不出工单卡，避免出现一张点了就 409 的死卡。
   */
  suppressedTicketIds: ReadonlySet<string>
  isPending: boolean
  refetch: () => void
}

/** 计划批准/退回的决定出口；与确认卡的 `ConfirmationResolver` 同形。 */
export interface PlanResolver {
  resolve: (workerSessionId: string, ticketId: string, input: PlanApprovalInput) => void
  busyFor: (workerSessionId: string) => 'approve' | 'reject' | null
  error: ShownError | null
  clearError: () => void
}
