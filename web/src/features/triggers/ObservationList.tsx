import { useState } from 'react'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { MessageSquareText } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Disclosure } from '@/components/ui/disclosure'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { EmptyState, ErrorState, LoadingState, Spinner } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import {
  describeTriggerError,
  reviewObservation,
  type Observation,
  type ObservationVerdict,
} from './api'
import {
  formatDateTime,
  observationDecision,
  workerTitle,
} from './format'
import {
  observationsInfiniteQuery,
  triggerRulesQuery,
  triggerSpecsQuery,
} from './queries'

const card = 'rounded-xl border bg-card shadow-card'

const SCOPE_OPTIONS = [
  { value: 'pending', label: '待审阅' },
  { value: 'reviewed', label: '已审阅' },
] as const

function verdictLabel(verdict: Observation['verdict']): string | null {
  if (verdict === 'good') return '判断对了'
  if (verdict === 'bad') return '不该处理'
  return null
}

interface ObservationCardProps {
  observation: Observation
  ruleTitle: string
  workerLabel: (worker: string | null | undefined) => string
  busy: boolean
  onVerdict: (verdict: ObservationVerdict) => void
}

function ObservationCard({ observation, ruleTitle, workerLabel, busy, onVerdict }: ObservationCardProps) {
  const decision = observationDecision(observation)
  const marked = verdictLabel(observation.verdict)
  return (
    <li data-observation-id={observation.id} className={`${card} space-y-3 p-4`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{ruleTitle}</Badge>
        {marked ? (
          <Badge variant={observation.verdict === 'good' ? 'success' : 'warning'}>{marked}</Badge>
        ) : null}
        <span className="text-footnote text-muted-foreground">{formatDateTime(observation.created_at)}</span>
      </div>

      <p className="break-words text-[0.9375rem] font-medium leading-5">{observation.trigger_summary}</p>

      {decision.kind === 'accept' ? (
        <div className="space-y-2 rounded-lg bg-surface-2 px-3 py-2.5">
          <p className="text-footnote font-medium text-muted-foreground">本来会建的工单</p>
          <p className="break-words text-sm">
            {workerLabel(decision.worker)}
            {decision.title ? ` · ${decision.title}` : ''}
          </p>
          {decision.brief || decision.scope ? (
            <Disclosure variant="plain" title="说明与范围">
              <div className="space-y-2 text-sm">
                {decision.brief ? (
                  <p className="whitespace-pre-wrap break-words">
                    <span className="text-muted-foreground">说明：</span>
                    {decision.brief}
                  </p>
                ) : null}
                {decision.scope ? (
                  <p className="whitespace-pre-wrap break-words">
                    <span className="text-muted-foreground">范围：</span>
                    {decision.scope}
                  </p>
                ) : null}
              </div>
            </Disclosure>
          ) : null}
        </div>
      ) : (
        <p className="rounded-lg bg-surface-2 px-3 py-2.5 text-sm text-muted-foreground">
          研判建议停止正在进行的工单。
        </p>
      )}

      {decision.reason ? (
        <p className="break-words text-sm">
          <span className="text-muted-foreground">研判理由：</span>
          {decision.reason}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant={observation.verdict === 'good' ? 'default' : 'outline'}
          disabled={busy}
          onClick={() => onVerdict('good')}
        >
          判断对了
        </Button>
        <Button
          type="button"
          size="sm"
          variant={observation.verdict === 'bad' ? 'default' : 'outline'}
          disabled={busy}
          onClick={() => onVerdict('bad')}
        >
          不该处理
        </Button>
        {busy ? <Spinner className="text-muted-foreground" /> : null}
      </div>
    </li>
  )
}

/** 「待审阅」分页签：待审阅 / 已审阅两档，标「判断对了 / 不该处理」可改判。 */
export function ObservationList({ onViewRules }: { onViewRules: () => void }) {
  const queryClient = useQueryClient()
  const [reviewed, setReviewed] = useState(false)
  const observations = useInfiniteQuery(observationsInfiniteQuery(reviewed))
  const rules = useQuery(triggerRulesQuery)
  const specs = useQuery(triggerSpecsQuery)
  const [busyId, setBusyId] = useState<number | null>(null)

  const items = observations.data?.pages.flatMap((page) => page.items) ?? []
  const ruleTitles = new Map((rules.data?.items ?? []).map((rule) => [rule.id, rule.title]))

  const mark = async (observation: Observation, verdict: ObservationVerdict) => {
    setBusyId(observation.id)
    try {
      await reviewObservation(observation.id, verdict)
      toast.success('已标记')
    } catch (caught) {
      toast.error(describeTriggerError(caught))
    } finally {
      setBusyId(null)
      await queryClient.invalidateQueries({ queryKey: keys.agent.observationsAll() })
    }
  }

  let content
  if (observations.isPending) {
    content = <LoadingState label="正在读取观察记录…" />
  } else if (observations.isError) {
    content = (
      <div className={card}>
        <ErrorState
          title="观察记录读取失败"
          message={toApiError(observations.error).message}
          onRetry={() => void observations.refetch()}
          retrying={observations.isFetching}
        />
      </div>
    )
  } else if (items.length === 0) {
    content = (
      <div className={card}>
        <EmptyState
          icon={MessageSquareText}
          title="还没有观察记录"
          description="把规则切到观察档就会开始记录"
          action={(
            <Button type="button" variant="secondary" size="sm" onClick={onViewRules}>
              查看规则
            </Button>
          )}
        />
      </div>
    )
  } else {
    content = (
      <>
        <ul className="space-y-3">
          {items.map((observation) => (
            <ObservationCard
              key={observation.id}
              observation={observation}
              ruleTitle={ruleTitles.get(observation.rule_id) ?? observation.rule_id}
              workerLabel={(worker) => workerTitle(worker, specs.data ?? [])}
              busy={busyId === observation.id}
              onVerdict={(verdict) => void mark(observation, verdict)}
            />
          ))}
        </ul>
        {observations.hasNextPage ? (
          <div className="flex justify-center pt-1">
            <Button
              type="button"
              variant="outline"
              size="sm"
              loading={observations.isFetchingNextPage}
              onClick={() => void observations.fetchNextPage()}
            >
              {observations.isFetchingNextPage ? '正在加载…' : '加载更多'}
            </Button>
          </div>
        ) : null}
      </>
    )
  }

  return (
    <div className="space-y-3">
      <SegmentedControl
        ariaLabel="审阅状态"
        value={reviewed ? 'reviewed' : 'pending'}
        onValueChange={(next) => setReviewed(next === 'reviewed')}
        options={SCOPE_OPTIONS}
        fullWidth
        size="sm"
      />
      {content}
    </div>
  )
}
