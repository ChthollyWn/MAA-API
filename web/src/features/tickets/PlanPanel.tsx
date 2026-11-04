import { useId, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ClipboardList } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { Textarea } from '@/components/ui/textarea'
import { toast } from '@/components/ui/toaster'
import { formatDateTime } from './format'
import {
  planQueryOptions,
  submitPlanApproval,
  type PlanApprovalInput,
} from './workspace-api'

/** 计划状态的中文（M75 §4.6）。未知取值原样显示，避免把新枚举写丢。 */
export const PLAN_STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  awaiting_approval: '待批准',
  active: '进行中',
  done: '已完成',
  abandoned: '已放弃',
}

/** 步骤状态的中文（M75 §4.6）。 */
export const PLAN_ITEM_STATUS_LABELS: Record<string, string> = {
  pending: '待开始',
  in_progress: '进行中',
  done: '已完成',
  cancelled: '已取消',
}

const PLAN_STATUS_TONES: Record<string, 'neutral' | 'primary' | 'success' | 'warning' | 'danger'> = {
  draft: 'neutral',
  awaiting_approval: 'warning',
  active: 'primary',
  done: 'success',
  abandoned: 'neutral',
}

const ITEM_STATUS_TONES: Record<string, 'neutral' | 'primary' | 'success' | 'warning' | 'danger'> = {
  pending: 'neutral',
  in_progress: 'primary',
  done: 'success',
  cancelled: 'neutral',
}

export interface PlanPanelProps {
  /** 工单的 Worker 会话 id；没有会话时整块不渲染。 */
  workerSessionId: string | null
}

/** 工单侧栏的计划块（M75 §4.6）：标题、状态、步骤，待批准时批准或退回。 */
export function PlanPanel({ workerSessionId }: PlanPanelProps) {
  if (!workerSessionId) return null
  return <PlanContent workerSessionId={workerSessionId} />
}

function PlanContent({ workerSessionId }: { workerSessionId: string }) {
  const queryClient = useQueryClient()
  const plan = useQuery(planQueryOptions(workerSessionId))
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const reasonId = useId()

  const approval = useMutation({
    mutationFn: (input: PlanApprovalInput) => submitPlanApproval(workerSessionId, input),
    onSuccess: (next, input) => {
      queryClient.setQueryData(keys.agent.plan(workerSessionId), next)
      setRejecting(false)
      setReason('')
      toast.success(input.approved ? '已批准计划' : '已退回计划')
    },
    onError: (error) => toast.error(`处理计划失败：${toApiError(error).message}`),
  })

  const data = plan.data
  const awaiting = data?.status === 'awaiting_approval'
  const pending = approval.isPending

  return (
    <Section
      title="计划"
      description={data ? PLAN_STATUS_LABELS[data.status] ?? data.status : undefined}
    >
      {plan.isPending ? (
        <LoadingState label="正在读取计划…" />
      ) : plan.error ? (
        <ErrorState
          message={`读取计划失败：${toApiError(plan.error).message}`}
          onRetry={() => void plan.refetch()}
          retrying={plan.isFetching}
        />
      ) : !data ? (
        <EmptyState
          icon={ClipboardList}
          title="还没有计划"
          description="多步任务会先写一份计划"
        />
      ) : (
        <article aria-label="当前计划" className="overflow-hidden rounded-xl border bg-card shadow-card">
          <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
            <h3 className="min-w-0 flex-1 break-words text-[0.9375rem] font-semibold">{data.title}</h3>
            <Badge variant={PLAN_STATUS_TONES[data.status] ?? 'neutral'}>
              {PLAN_STATUS_LABELS[data.status] ?? data.status}
            </Badge>
          </div>
          {data.items.length === 0 ? (
            <p className="px-4 py-3 text-footnote text-muted-foreground">还没有步骤</p>
          ) : (
            <ol aria-label="计划步骤" className="space-y-2.5 px-4 py-3">
              {data.items.map((item, index) => (
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
          {data.approved_at ? (
            <p className="border-t px-4 py-2 text-footnote text-muted-foreground">
              {data.approved_by ? `由 ${data.approved_by} ` : ''}
              批准于 {formatDateTime(data.approved_at)}
            </p>
          ) : null}
          {awaiting ? (
            <div className="space-y-2 border-t px-4 py-3">
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  loading={pending}
                  disabled={pending}
                  onClick={() => approval.mutate({ approved: true })}
                >
                  批准
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={pending}
                  onClick={() => setRejecting((open) => !open)}
                >
                  退回
                </Button>
              </div>
              {rejecting ? (
                <div className="space-y-2">
                  <label htmlFor={reasonId} className="block text-footnote font-medium text-muted-foreground">
                    退回原因
                  </label>
                  <Textarea
                    id={reasonId}
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    placeholder="写清要改哪一步、改成什么"
                    disabled={pending}
                    className="min-h-20"
                  />
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="destructive-soft"
                      loading={pending}
                      disabled={pending || reason.trim().length === 0}
                      onClick={() => approval.mutate({ approved: false, reason: reason.trim() })}
                    >
                      提交退回
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={pending}
                      onClick={() => {
                        setRejecting(false)
                        setReason('')
                      }}
                    >
                      取消
                    </Button>
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}
        </article>
      )}
    </Section>
  )
}
