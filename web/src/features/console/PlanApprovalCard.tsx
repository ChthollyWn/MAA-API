import { useId, useState } from 'react'
import { Link } from 'react-router'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import type { PlanApprovalInput, PlanView } from '@/features/tickets/workspace-api'
import {
  ITEM_STATUS_TONES,
  PLAN_ITEM_STATUS_LABELS,
} from '@/features/tickets/plan-format'
import type { TicketView } from './api'

/** 退回原因的字符上限，与 `POST .../plan/approval` 的 `reason` 上限一致（M75 §4.3）。 */
export const PLAN_REJECT_REASON_MAX = 200

export interface PlanApprovalCardProps {
  ticket: TicketView
  plan: PlanView
  workerSessionId: string
  busy: 'approve' | 'reject' | null
  onResolve: (workerSessionId: string, ticketId: string, input: PlanApprovalInput) => void
}

/**
 * 指挥台「待你处理」里的计划卡（spec `console-plan-approval` §4.3）：
 * 标题、步骤与来源工单，待批准时就地批准/退回。工单页的计划面板仍是第二入口。
 */
export function PlanApprovalCard({
  ticket,
  plan,
  workerSessionId,
  busy,
  onResolve,
}: PlanApprovalCardProps) {
  const titleId = useId()
  const reasonId = useId()
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const pending = busy !== null
  const canReject = reason.trim().length > 0 && !pending

  const closeReject = () => {
    setRejecting(false)
    setReason('')
  }

  return (
    <article aria-labelledby={titleId} className="overflow-hidden rounded-xl border bg-card shadow-card">
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h3 id={titleId} className="text-[0.9375rem] font-semibold leading-6">计划待批准</h3>
          <Badge variant="warning">待批准</Badge>
          <Link
            to={`/tickets/${ticket.id}`}
            className="ml-auto text-footnote font-medium text-primary-text underline decoration-primary-text/40 underline-offset-2 hover:decoration-primary-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            打开工单
          </Link>
        </div>
        <p className="break-words text-sm font-medium">{plan.title}</p>
        {plan.items.length === 0 ? (
          <p className="text-footnote text-muted-foreground">还没有步骤</p>
        ) : (
          <ol aria-label="计划步骤" className="space-y-2">
            {plan.items.map((item, index) => (
              <li key={item.id} className="flex items-start gap-2.5">
                <span
                  aria-hidden="true"
                  className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-surface-2 text-[0.6875rem] font-medium text-muted-foreground"
                >
                  {index + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="break-words text-sm">{item.title}</p>
                  {item.note ? (
                    <p className="mt-0.5 whitespace-pre-wrap break-words text-footnote text-muted-foreground">
                      {item.note}
                    </p>
                  ) : null}
                </div>
                <Badge variant={ITEM_STATUS_TONES[item.status] ?? 'neutral'}>
                  {PLAN_ITEM_STATUS_LABELS[item.status] ?? item.status}
                </Badge>
              </li>
            ))}
          </ol>
        )}
      </div>
      <div className="grid grid-cols-2 gap-2 border-t bg-surface-2/40 p-3">
        <Button
          type="button"
          variant="outline"
          disabled={pending}
          loading={busy === 'reject'}
          aria-expanded={rejecting}
          onClick={() => (rejecting ? closeReject() : setRejecting(true))}
        >
          退回计划
        </Button>
        <Button
          type="button"
          disabled={pending}
          loading={busy === 'approve'}
          onClick={() => onResolve(workerSessionId, ticket.id, { approved: true })}
        >
          批准计划
        </Button>
      </div>
      {rejecting && !pending ? (
        <div className="space-y-2 border-t px-4 py-3">
          <label htmlFor={reasonId} className="block text-footnote font-medium text-muted-foreground">
            退回原因
          </label>
          <Textarea
            id={reasonId}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="写清要改哪一步、改成什么"
            maxLength={PLAN_REJECT_REASON_MAX}
            className="min-h-20"
          />
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant="destructive-soft"
              disabled={!canReject}
              onClick={() => onResolve(workerSessionId, ticket.id, { approved: false, reason: reason.trim() })}
            >
              提交退回
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={closeReject}>
              取消
            </Button>
          </div>
        </div>
      ) : null}
    </article>
  )
}
