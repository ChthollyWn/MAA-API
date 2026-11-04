import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import type { components } from '@/types/api'

/**
 * 回溯与分叉的请求设施（M80 §4.6）。
 *
 * 两个端点都返回 `error_responses(...)` 的错误信封，失败一律转成 `ApiError`，
 * 由调用方按 §4.6 的 toast 文案展示。
 */
export type RewindRequest = components['schemas']['RewindRequest']
export type RewindResult = components['schemas']['RewindResultOut']
export type RewindSideEffect = components['schemas']['RewindSideEffectOut']
export type ForkRequest = components['schemas']['ForkRequest']
export type TicketDetailWire = components['schemas']['TicketDetail']

/** `POST /api/agent/sessions/{session_id}/rewind`：`dry_run` 只计算不写库。 */
export async function rewindSession(sessionId: string, body: RewindRequest): Promise<RewindResult> {
  const { data, error, response } = await api.POST('/api/agent/sessions/{session_id}/rewind', {
    params: { path: { session_id: sessionId } },
    body,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

/** `POST /api/agent/tickets/{ticket_id}/fork`：在 `at_seq`（含）处分叉出新工单。 */
export async function forkTicket(ticketId: string, body: ForkRequest): Promise<TicketDetailWire> {
  const { data, error, response } = await api.POST('/api/agent/tickets/{ticket_id}/fork', {
    params: { path: { ticket_id: ticketId } },
    body,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}
