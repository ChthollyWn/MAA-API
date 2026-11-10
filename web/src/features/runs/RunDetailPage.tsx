import { useMemo, useState, type ReactNode } from 'react'
import { RefreshCw } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router'
import { toApiError } from '@/api/errors'
import { Page } from '@/components/layout/Page'
import { Section } from '@/components/ui/section'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ErrorState, LoadingState } from '@/components/ui/states'
import { FeedbackSection } from '@/features/insights/FeedbackSection'
import {
  RUN_POLL_MS, isLiveRunStatus, runDetailQueryOptions, runSpansQueryOptions, specsQueryOptions,
} from '@/features/runs/api'
import {
  agentTitle, finishReasonLabel, formatDateTime, formatDuration, formatMicros, formatSpecVersion,
  formatTokens, runDurationMs, runKindLabel, runStatusLabel, runStatusTone,
} from '@/features/runs/format'
import { RunTree } from '@/features/runs/RunTree'
import { SpanWaterfall } from '@/features/runs/SpanWaterfall'
import { SpanDetailSheet } from '@/features/runs/SpanDetailSheet'
import type { SpanRow } from '@/features/runs/timeline'
import { cn } from '@/lib/utils'

function queryError(value: unknown): string {
  return toApiError(value).message || '请求失败，请稍后重试。'
}

function SummaryItem({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 space-y-0.5">
      <dt className="text-footnote text-muted-foreground">{label}</dt>
      <dd className="break-words text-sm font-medium tabular-nums">{children}</dd>
    </div>
  )
}

export function RunDetailPage() {
  const { runId = '' } = useParams<{ runId: string }>()
  const detail = useQuery({
    ...runDetailQueryOptions(runId),
    refetchInterval: (query) => (isLiveRunStatus(query.state.data?.status) ? RUN_POLL_MS : false),
  })
  const live = isLiveRunStatus(detail.data?.status)
  const spans = useQuery({
    ...runSpansQueryOptions(runId),
    refetchInterval: live ? RUN_POLL_MS : false,
  })
  const specs = useQuery(specsQueryOptions)
  const titles = useMemo(() => new Map((specs.data ?? []).map((spec) => [spec.name, spec.title])), [specs.data])
  const [selected, setSelected] = useState<SpanRow | null>(null)

  const run = detail.data
  const refreshing = detail.isFetching || spans.isFetching
  const refresh = () => {
    void detail.refetch()
    void spans.refetch()
  }

  return (
    <Page
      title="运行详情"
      back="/more/runs"
      actions={(
        <Button variant="ghost" size="icon" aria-label="刷新" disabled={refreshing} onClick={refresh}>
          <RefreshCw aria-hidden="true" className={cn(refreshing && 'animate-spin')} />
        </Button>
      )}
    >
      <div className="space-y-6 pb-4">
        {detail.isPending ? (
          <div className="rounded-xl border bg-card shadow-card"><LoadingState label="正在读取运行详情…" /></div>
        ) : detail.error && !detail.data ? (
          <div className="rounded-xl border bg-card shadow-card">
            <ErrorState
              message={`读取运行详情失败：${queryError(detail.error)}`}
              onRetry={() => void detail.refetch()}
              retrying={detail.isFetching}
            />
          </div>
        ) : null}

        {detail.error && detail.data ? (
          <InlineAlert tone="warning" action={<Button variant="outline" size="sm" onClick={() => void detail.refetch()}>重试</Button>}>
            刷新失败：{queryError(detail.error)}
          </InlineAlert>
        ) : null}

        {run ? (
          <>
            <section aria-label="运行概要" className="rounded-xl border bg-card p-4 shadow-card">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-[0.9375rem] font-semibold">{agentTitle(run.agent_name, titles)}</p>
                  <p className="mt-0.5 text-footnote text-muted-foreground">
                    {runKindLabel(run.kind)} · {formatDateTime(run.started_at)}
                    {live ? ' · 数据每 3 秒更新' : ''}
                  </p>
                </div>
                <Badge variant={runStatusTone(run.status)}>{runStatusLabel(run.status)}</Badge>
              </div>

              <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
                <SummaryItem label="状态">{runStatusLabel(run.status)}</SummaryItem>
                <SummaryItem label="终止原因">{finishReasonLabel(run.finish_reason)}</SummaryItem>
                <SummaryItem label="模型">{run.model ?? '—'}</SummaryItem>
                <SummaryItem label="定义版本">{formatSpecVersion(run.spec_version)}</SummaryItem>
                <SummaryItem label="开始">{formatDateTime(run.started_at)}</SummaryItem>
                <SummaryItem label="结束">{formatDateTime(run.finished_at)}</SummaryItem>
                <SummaryItem label="步数">{run.steps} 步</SummaryItem>
                <SummaryItem label="耗时">{formatDuration(runDurationMs(run))}</SummaryItem>
                <SummaryItem label="输入 token">{formatTokens(run.prompt_tokens)}</SummaryItem>
                <SummaryItem label="缓存命中">{formatTokens(run.cached_tokens)}</SummaryItem>
                <SummaryItem label="输出 token">{formatTokens(run.completion_tokens)}</SummaryItem>
                <SummaryItem label="成本">{formatMicros(run.cost_micros, run.currency)}</SummaryItem>
              </dl>

              {run.error_message ? (
                <InlineAlert tone="danger" className="mt-4">{run.error_message}</InlineAlert>
              ) : null}
            </section>

            <FeedbackSection runId={run.run_id} />

            <Section title="运行树" description="点任意节点查看那次运行的详情">
              <RunTree run={run} currentRunId={run.run_id} titles={titles} />
            </Section>

            <Section
              title="运行追踪"
              hint="每个横条相对整次运行定位；点一行查看 span 的属性与耗时。"
            >
              {spans.isPending ? (
                <div className="rounded-xl border bg-card shadow-card"><LoadingState label="正在读取追踪记录…" /></div>
              ) : spans.error && !spans.data ? (
                <div className="rounded-xl border bg-card shadow-card">
                  <ErrorState
                    message={`读取追踪记录失败：${queryError(spans.error)}`}
                    onRetry={() => void spans.refetch()}
                    retrying={spans.isFetching}
                  />
                </div>
              ) : (
                <SpanWaterfall
                  spans={spans.data?.items ?? []}
                  truncated={spans.data?.truncated ?? false}
                  highlightRunId={run.run_id}
                  titles={titles}
                  onSelect={setSelected}
                />
              )}
            </Section>
          </>
        ) : null}
      </div>

      <SpanDetailSheet
        row={selected}
        onOpenChange={(open) => { if (!open) setSelected(null) }}
        currentRunId={run?.run_id ?? null}
        titles={titles}
      />
    </Page>
  )
}

export default RunDetailPage
