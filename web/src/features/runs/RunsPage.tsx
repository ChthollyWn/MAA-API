import { useMemo } from 'react'
import { Activity, ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { toApiError } from '@/api/errors'
import { Page } from '@/components/layout/Page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import {
  RUN_PAGE_SIZE, isLiveRunStatus, runListQueryOptions, specsQueryOptions,
  type RunListFilters, type RunSummary,
} from '@/features/runs/api'
import {
  agentTitle, finishReasonLabel, formatDateTime, formatDuration, formatMicros, formatTokens,
  runDurationMs, runKindLabel, runStatusLabel, runStatusTone,
} from '@/features/runs/format'
import { cn } from '@/lib/utils'

function queryError(value: unknown): string {
  return toApiError(value).message || '请求失败，请稍后重试。'
}

function RunRow({ run, titles }: { run: RunSummary; titles: ReadonlyMap<string, string> }) {
  const duration = runDurationMs(run)
  return (
    <Link
      to={`/more/runs/${run.run_id}`}
      className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
    >
      <span className="min-w-0 flex-1 space-y-1">
        <span className="flex items-start justify-between gap-2">
          <span className="min-w-0 truncate text-sm font-semibold leading-6">{agentTitle(run.agent_name, titles)}</span>
          <Badge variant={runStatusTone(run.status)} className="mt-0.5">{runStatusLabel(run.status)}</Badge>
        </span>
        <span className="block text-footnote text-muted-foreground">
          {runKindLabel(run.kind)} · {formatDateTime(run.started_at)} · {duration === null ? '进行中' : formatDuration(duration)}
        </span>
        <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-footnote text-muted-foreground tabular-nums">
          <span>{run.steps} 步</span>
          <span>{formatTokens(run.prompt_tokens + run.completion_tokens)} token</span>
          <span>{formatMicros(run.cost_micros, run.currency)}</span>
          {run.finish_reason ? <span>{finishReasonLabel(run.finish_reason)}</span> : null}
          {isLiveRunStatus(run.status) ? <span className="text-primary-text">仍在进行</span> : null}
        </span>
      </span>
      <ChevronRight aria-hidden="true" className="mt-1 size-4 shrink-0 text-muted-foreground/70" />
    </Link>
  )
}

export function RunsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const ticketId = searchParams.get('ticket_id') ?? undefined
  const page = Math.max(1, Number(searchParams.get('page') ?? 1) || 1)
  const filters = useMemo<RunListFilters>(() => ({
    root_only: true,
    page,
    size: RUN_PAGE_SIZE,
    ...(ticketId ? { ticket_id: ticketId } : {}),
  }), [page, ticketId])

  const runs = useQuery({ ...runListQueryOptions(filters), placeholderData: keepPreviousData })
  const specs = useQuery(specsQueryOptions)
  const titles = useMemo(() => new Map((specs.data ?? []).map((spec) => [spec.name, spec.title])), [specs.data])

  const items = runs.data?.items ?? []
  const total = runs.data?.total
  const pageCount = Math.max(1, Math.ceil((total ?? 0) / RUN_PAGE_SIZE))

  const goToPage = (next: number) => {
    const params = new URLSearchParams(searchParams)
    if (next <= 1) params.delete('page')
    else params.set('page', String(next))
    setSearchParams(params)
    if (document.scrollingElement) document.scrollingElement.scrollTop = 0
  }

  const refresh = () => {
    void runs.refetch()
    void specs.refetch()
  }

  return (
    <Page
      title="运行追踪"
      back="/more"
      actions={(
        <Button variant="ghost" size="icon" aria-label="刷新" disabled={runs.isFetching} onClick={refresh}>
          <RefreshCw aria-hidden="true" className={cn(runs.isFetching && 'animate-spin')} />
        </Button>
      )}
    >
      <div className="space-y-3 pb-4">
        {runs.isPending ? (
          <div className="rounded-xl border bg-card shadow-card"><LoadingState label="正在读取运行记录…" /></div>
        ) : runs.error && !runs.data ? (
          <div className="rounded-xl border bg-card shadow-card">
            <ErrorState
              message={`读取运行记录失败：${queryError(runs.error)}`}
              onRetry={() => void runs.refetch()}
              retrying={runs.isFetching}
            />
          </div>
        ) : items.length === 0 ? (
          <div className="rounded-xl border bg-card shadow-card">
            {ticketId ? (
              <EmptyState
                icon={Activity}
                title="还没有运行记录"
                description="这个工单还没有运行记录"
                action={<Button variant="outline" size="sm" onClick={() => setSearchParams(new URLSearchParams())}>查看全部运行</Button>}
              />
            ) : (
              <EmptyState
                icon={Activity}
                title="还没有运行记录"
                description="去 Agent 页发一条消息"
                action={<Button asChild size="sm"><Link to="/agent">去 Agent 页</Link></Button>}
              />
            )}
          </div>
        ) : (
          <ul
            aria-label="运行记录"
            aria-busy={runs.isPlaceholderData || undefined}
            className={cn('divide-y overflow-hidden rounded-xl border bg-card shadow-card transition-opacity', runs.isPlaceholderData && 'opacity-60')}
          >
            {items.map((run) => (
              <li key={run.run_id}>
                <RunRow run={run} titles={titles} />
              </li>
            ))}
          </ul>
        )}

        {total !== undefined && total > RUN_PAGE_SIZE ? (
          <nav aria-label="运行分页" className="flex items-center justify-between gap-3 pt-1">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => goToPage(page - 1)}>
              <ChevronLeft aria-hidden="true" />上一页
            </Button>
            <span className="text-sm text-muted-foreground tabular-nums">第 {page} / {pageCount} 页</span>
            <Button variant="outline" size="sm" disabled={page >= pageCount} onClick={() => goToPage(page + 1)}>
              下一页<ChevronRight aria-hidden="true" />
            </Button>
          </nav>
        ) : null}
      </div>
    </Page>
  )
}

export default RunsPage
