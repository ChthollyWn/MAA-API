import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { Badge, type StatusTone } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InlineAlert } from '@/components/ui/inline-alert'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { accountKeys, cancelUpgradePlan, fetchUpgradePlans, type PlanUnknown, type UpgradePlan } from './api'
import {
  finiteNumber,
  fieldLabel,
  formatDateTime,
  missingMaterialsText,
  parsePlanStages,
  planRunsTotal,
  planStagesText,
  planStatusLabel,
  UNKNOWN_TEXT,
  upgradeTargetText,
} from './format'

/** 状态 → 徽章配色（§7.7）；没见过的状态落到中性色，文案原样显示。 */
const STATUS_TONE: Record<string, StatusTone> = {
  active: 'primary',
  completed: 'success',
  cancelled: 'neutral',
}

interface PlanTarget {
  name: string
  goal: string
}

/** `targets` 是目标数组（`{operator, elite, skill_level, masteries, modules}`）；整行读不出就跳过。 */
export function planTargets(value: unknown): PlanTarget[] {
  return (Array.isArray(value) ? value : []).flatMap((row) => {
    if (typeof row === 'string') return row ? [{ name: row, goal: '' }] : []
    if (!row || typeof row !== 'object' || Array.isArray(row)) return []
    const target = row as Record<string, unknown>
    const name =
      typeof target.operator === 'string' ? target.operator : typeof target.name === 'string' ? target.name : null
    if (!name) return []
    return [{ name, goal: upgradeTargetText(target) }]
  })
}

/** `progress.unknowns` 的一条（§4.7）；三字段缺一个就跳过，别把半条渲染进页面。 */
export function planUnknowns(value: unknown): PlanUnknown[] {
  return (Array.isArray(value) ? value : []).flatMap((row) => {
    if (!row || typeof row !== 'object' || Array.isArray(row)) return []
    const entry = row as Record<string, unknown>
    const operator = typeof entry.operator === 'string' ? entry.operator.trim() : ''
    const field = typeof entry.field === 'string' ? entry.field.trim() : ''
    const reason = typeof entry.reason === 'string' ? entry.reason.trim() : ''
    if (!operator || !field) return []
    return [{ operator, field, reason: reason || '未识别，需先确认' }]
  })
}

/**
 * `progress.unknowns` 之前落库的旧行兜底（§4.7）：从 `notes` 里挑出提到「未识别」的那句。
 * 只挑这一句，不把整份 `notes` 铺出来；`progress.notes` 与顶层 `notes` 两种落点都认。
 */
export function planUnknownNotes(plan: UpgradePlan): string[] {
  const progress = plan.progress ?? {}
  const raw = [
    ...(Array.isArray(progress.notes) ? progress.notes : [progress.notes]),
    ...(Array.isArray(plan.notes) ? plan.notes : [plan.notes]),
  ]
  const picked = raw
    .filter((item): item is string => typeof item === 'string')
    .map((item) => item.trim())
    .filter((item) => item.includes(UNKNOWN_TEXT))
  return Array.from(new Set(picked))
}

function planProgress(plan: UpgradePlan) {
  const progress = plan.progress ?? {}
  const stages = parsePlanStages(progress.stages)
  const runs = planRunsTotal(stages)
  const stageText = planStagesText(stages)
  const days = finiteNumber(progress.days_estimated)
  const sanity = finiteNumber(progress.total_sanity)
  const replannedAt = typeof progress.replanned_at === 'string' ? formatDateTime(progress.replanned_at) : null
  const replanError =
    typeof progress.replan_error === 'string' && progress.replan_error.trim() ? progress.replan_error : null
  const scheduleNote =
    typeof progress.schedule_note === 'string' && progress.schedule_note.trim() ? progress.schedule_note : null
  return {
    /** 缺口材料：`{材料名: 数量}` → 「固源岩 ×12、聚酸酯 ×3」。 */
    missing: missingMaterialsText(progress.missing),
    stages: runs === null ? stageText : `${stageText ?? ''}${stageText ? ' · ' : ''}共 ${runs} 场`,
    usage: [
      days === null ? null : `预计 ${days} 天`,
      sanity === null ? null : `共 ${sanity} 理智`,
    ]
      .filter((item): item is string => item !== null)
      .join(' · '),
    replannedAt,
    replanError,
    scheduleNote,
    /** §4.7「待确认」：未识别的维度（`progress.unknowns`），旧行没有就是空数组。 */
    unknowns: planUnknowns(progress.unknowns),
    /** `unknowns` 缺失时的兜底：`notes` 里提「未识别」的那句。 */
    unknownNotes: planUnknownNotes(plan),
  }
}

function PlanCard({
  plan,
  cancelling,
  onCancel,
}: {
  plan: UpgradePlan
  cancelling: boolean
  onCancel: (plan: UpgradePlan) => void
}) {
  const targets = planTargets(plan.targets)
  const progress = planProgress(plan)
  const createdAt = formatDateTime(plan.created_at)
  const updatedAt = formatDateTime(plan.updated_at)
  const completedAt = plan.status === 'completed' ? formatDateTime(plan.completed_at) : null
  return (
    <li className="space-y-2.5 rounded-xl border bg-card p-4 shadow-card">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-0.5">
          <h3 className="break-words text-[0.9375rem] font-semibold leading-6">{plan.name}</h3>
          <p className="text-footnote text-muted-foreground">
            {[
              createdAt ? `创建于 ${createdAt}` : null,
              updatedAt ? `更新于 ${updatedAt}` : null,
              completedAt ? `完成于 ${completedAt}` : null,
            ]
              .filter((item): item is string => item !== null)
              .join(' · ') || '时间未知'}
          </p>
        </div>
        <Badge variant={STATUS_TONE[plan.status] ?? 'neutral'}>{planStatusLabel(plan.status)}</Badge>
      </div>

      {targets.length ? (
        <ul aria-label="养成目标" className="divide-y overflow-hidden rounded-lg border">
          {targets.map((target, index) => (
            <li key={`${target.name}-${index}`} className="flex items-center gap-3 p-2.5 text-footnote">
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{target.name}</span>
              {target.goal ? <span className="shrink-0 text-muted-foreground">{target.goal}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}

      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-footnote">
        <dt className="text-muted-foreground">缺失材料</dt>
        <dd className="break-words">{progress.missing ?? '无'}</dd>
        <dt className="text-muted-foreground">剩余场次</dt>
        <dd className="break-words tabular-nums">{progress.stages ?? '—'}</dd>
        {progress.usage ? (
          <>
            <dt className="text-muted-foreground">计划用量</dt>
            <dd className="tabular-nums">{progress.usage}</dd>
          </>
        ) : null}
        <dt className="text-muted-foreground">定时任务</dt>
        <dd className="break-all font-mono text-[0.8125rem]">{plan.schedule_id ?? '未创建'}</dd>
        {progress.replannedAt ? (
          <>
            <dt className="text-muted-foreground">重新排期</dt>
            <dd className="tabular-nums">{progress.replannedAt}</dd>
          </>
        ) : null}
      </dl>

      {progress.replanError ? (
        <InlineAlert tone="warning">重新排期失败：{progress.replanError}</InlineAlert>
      ) : null}
      {progress.scheduleNote ? <p className="text-footnote text-muted-foreground">{progress.scheduleNote}</p> : null}

      {/* §4.7「待确认」：未识别的维度算不进材料增量，这里逐条说清是哪一位的哪一维。 */}
      {progress.unknowns.length || progress.unknownNotes.length ? (
        <InlineAlert tone="warning" title="待确认">
          {progress.unknowns.length ? (
            <ul aria-label="待确认项" className="space-y-0.5">
              {progress.unknowns.map((item, index) => (
                <li key={`${item.operator}-${item.field}-${index}`} className="break-words">
                  {item.operator} · {fieldLabel(item.field)} · {item.reason}
                </li>
              ))}
            </ul>
          ) : (
            progress.unknownNotes.map((item, index) => (
              <p key={index} className="break-words">
                {item}
              </p>
            ))
          )}
        </InlineAlert>
      ) : null}

      {plan.status === 'active' ? (
        <div className="flex justify-end">
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-label={`取消养成计划「${plan.name}」`}
            loading={cancelling}
            onClick={() => onCancel(plan)}
          >
            取消计划
          </Button>
        </div>
      ) : null}
    </li>
  )
}

/**
 * 「账号数据」页的养成计划区块（规格 §7.7）：列出 Agent 启动的刷关计划——状态、目标、
 * 缺的材料、剩余场次与定时任务——并允许就地取消（二次确认后 `DELETE`，再重新拉列表）。
 */
export function UpgradePlansSection() {
  const queryClient = useQueryClient()
  const [confirm, confirmElement] = useConfirm()
  const [actionError, setActionError] = useState<string | null>(null)
  const plans = useQuery({
    queryKey: accountKeys.upgradePlans(),
    queryFn: ({ signal }) => fetchUpgradePlans({}, signal),
    retry: false,
  })

  const cancel = useMutation({
    mutationFn: (planId: string) => cancelUpgradePlan(planId),
    onMutate: () => setActionError(null),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: accountKeys.upgradePlansAll() })
    },
    onError: (error) => {
      setActionError(toApiError(error).message || '取消养成计划失败，请稍后重试。')
    },
  })

  const requestCancel = async (plan: UpgradePlan) => {
    const approved = await confirm({
      title: `取消养成计划「${plan.name}」？`,
      description: '取消后会删掉这条计划的定时任务，已经刷到的材料不会回退；需要的时候可以重新启动一条计划。',
      confirmText: '取消计划',
      cancelText: '先不取消',
      tone: 'danger',
    })
    if (!approved) return
    cancel.mutate(plan.id)
  }

  const items = plans.data?.items ?? []
  return (
    <section aria-labelledby="upgrade-plans-title" className="space-y-3">
      <header className="flex items-center gap-2 px-1">
        <h2 id="upgrade-plans-title" className="text-headline font-semibold">养成计划</h2>
        {items.length ? <Badge variant="neutral" className="tabular-nums">{items.length}</Badge> : null}
      </header>
      {actionError ? <InlineAlert tone="danger">{actionError}</InlineAlert> : null}
      {plans.isPending ? (
        <LoadingState label="正在读取养成计划…" />
      ) : plans.error ? (
        <ErrorState
          title="读取养成计划失败"
          message={`读取养成计划失败：${toApiError(plans.error).message}`}
          onRetry={() => void plans.refetch()}
          retrying={plans.isFetching}
        />
      ) : items.length === 0 ? (
        <EmptyState
          icon={CalendarClock}
          title="还没有养成计划"
          description="让 Agent 用 start_upgrade_plan 计划一次刷关，开始后计划与它的每日定时任务会显示在这里。"
        />
      ) : (
        <ul aria-label="养成计划列表" className="space-y-2">
          {items.map((plan) => (
            <PlanCard
              key={plan.id || plan.name}
              plan={plan}
              cancelling={cancel.isPending && cancel.variables === plan.id}
              onCancel={(target) => void requestCancel(target)}
            />
          ))}
        </ul>
      )}
      {confirmElement}
    </section>
  )
}

export default UpgradePlansSection
