import { useCallback, useMemo } from 'react'
import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query'
import { keys } from '@/api/keys'
import { toast } from '@/components/ui/toaster'
import { toShownError } from '@/features/agent/format'
import type { ShownError } from '@/features/agent/types'
import {
  planQueryOptions,
  submitPlanApproval,
  type PlanApprovalInput,
  type PlanView,
} from '@/features/tickets/workspace-api'
import { ticketDetailQuery, type TicketView } from './api'

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

/** 计划只可能出现在 `waiting` 工单的 `decision` 动作上（M75 §4.5）。 */
function isPlanCandidate(ticket: TicketView): boolean {
  return ticket.status === 'waiting'
    && ticket.humanAction?.type === 'decision'
    && Boolean(ticket.humanAction.ref_id)
}

interface PlanCandidate {
  ticket: TicketView
  /** 非空：`isPlanCandidate` 已保证。 */
  planRefId: string
}

/**
 * 计划数据源：对 waiting + decision 的工单先读一次详情拿 `worker_session_id`，再读
 * 该会话当前的计划。只对这类工单多发请求，数量很少；读不到计划时不出计划项，
 * 由调用方按现状回落工单卡。
 */
export function useAttentionPlans(tickets: readonly TicketView[]): PlanAttentionSource {
  const candidates = useMemo<PlanCandidate[]>(
    () => tickets.flatMap((ticket) => {
      const planRefId = ticket.humanAction?.ref_id
      return isPlanCandidate(ticket) && planRefId ? [{ ticket, planRefId }] : []
    }),
    [tickets],
  )
  const details = useQueries({
    queries: candidates.map((candidate) => ticketDetailQuery(candidate.ticket.id)),
  })
  const sessionIds = candidates.map(
    (_candidate, index) => details[index]?.data?.workerSessionId ?? null,
  )
  const plans = useQueries({
    queries: sessionIds.map((sessionId) => planQueryOptions(sessionId)),
  })

  // `useQueries` 每次 render 都返回新数组，这段不做 memo：数据量很小，且 memo 不会命中。
  const items: PlanAttentionView[] = []
  const suppressedTicketIds = new Set<string>()
  candidates.forEach((candidate, index) => {
    const workerSessionId = details[index]?.data?.workerSessionId
    const plan = plans[index]?.data
    if (!workerSessionId || !plan) return
    // 只认当前 human_action 指向的这份计划；对不上时仍按现状显示工单卡。
    if (plan.id !== candidate.planRefId) return
    if (plan.status === 'awaiting_approval') {
      items.push({ ticket: candidate.ticket, plan, workerSessionId })
    } else {
      suppressedTicketIds.add(candidate.ticket.id)
    }
  })

  // disabled 查询（`planQueryOptions(null)`）的 `isPending` 恒为 true，不能算加载中，
  // 否则没有 Worker 会话的 decision 工单会让「待你处理」永远停在「正在读取…」。
  const isPending = candidates.length > 0
    && [...details, ...plans].some((query) => query.isPending && query.fetchStatus !== 'idle')
  const refetch = useCallback(() => {
    for (const query of details) void query.refetch()
    for (const query of plans) void query.refetch()
  }, [details, plans])

  return { items, suppressedTicketIds, isPending, refetch }
}

interface ApprovalVariables {
  workerSessionId: string
  ticketId: string
  input: PlanApprovalInput
}

function invalidateAfterDecision(
  queryClient: ReturnType<typeof useQueryClient>,
  variables: ApprovalVariables,
): Promise<unknown> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: keys.agent.plan(variables.workerSessionId) }),
    queryClient.invalidateQueries({ queryKey: keys.agent.tickets.detail(variables.ticketId) }),
    queryClient.invalidateQueries({ queryKey: keys.agent.tickets.lists() }),
  ])
}

/**
 * 批准/退回计划的出口（spec §4.6）：成功后把响应写回计划缓存并失效工单查询，
 * 让指挥台立刻撤下计划卡；失败（含 409 已不再待批准）同样重取计划与工单。
 */
export function usePlanApprovalResolver(): PlanResolver {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: (variables: ApprovalVariables) =>
      submitPlanApproval(variables.workerSessionId, variables.input),
    onSuccess: (next, variables) => {
      queryClient.setQueryData(keys.agent.plan(variables.workerSessionId), next)
      void queryClient.invalidateQueries({ queryKey: keys.agent.tickets.detail(variables.ticketId) })
      void queryClient.invalidateQueries({ queryKey: keys.agent.tickets.lists() })
      toast.success(variables.input.approved ? '已批准计划' : '已退回计划')
    },
    onError: (_error, variables) => {
      void invalidateAfterDecision(queryClient, variables)
    },
  })

  return {
    resolve: (workerSessionId, ticketId, input) =>
      mutation.mutate({ workerSessionId, ticketId, input }),
    busyFor: (workerSessionId) => mutation.isPending
      && mutation.variables?.workerSessionId === workerSessionId
      ? (mutation.variables.input.approved ? 'approve' : 'reject')
      : null,
    error: mutation.error ? toShownError(mutation.error) : null,
    clearError: () => mutation.reset(),
  }
}
