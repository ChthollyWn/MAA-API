import { Link } from 'react-router'
import { useInfiniteQuery } from '@tanstack/react-query'
import { toApiError } from '@/api/errors'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { ticketReportsInfiniteQuery } from './api'
import {
  REPORT_STATUS_LABELS,
  formatDateTime,
  type HumanAction,
  type ReportView,
} from './format'

export interface ReportTimelineProps {
  ticketId: string
  /** 空状态的「去发消息」：切到消息分段并聚焦输入栏。 */
  onCompose?: () => void
}

const REPORT_TONES: Record<string, 'primary' | 'warning' | 'success' | 'danger' | 'neutral'> = {
  progress: 'primary',
  waiting: 'warning',
  ready: 'success',
  blocked: 'danger',
  failed: 'danger',
  dropped: 'neutral',
}

const ACTION_LABELS: Record<HumanAction['type'], string> = {
  confirmation: '需要你确认',
  question: '需要你回答',
  decision: '需要你决定',
}

function pipelineIds(details: Record<string, unknown> | null): string[] {
  if (!details) return []
  const ids = new Set<string>()
  if (typeof details.pipeline_id === 'string' && details.pipeline_id) ids.add(details.pipeline_id)
  if (Array.isArray(details.pipeline_ids)) {
    for (const id of details.pipeline_ids) {
      if (typeof id === 'string' && id) ids.add(id)
    }
  }
  return [...ids]
}

function ReportItem({ report }: { report: ReportView }) {
  const ids = pipelineIds(report.details)
  return (
    <li className="space-y-1.5 rounded-xl border bg-card px-3.5 py-3 shadow-card">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={REPORT_TONES[report.status] ?? 'neutral'}>
          {REPORT_STATUS_LABELS[report.status] ?? report.status}
        </Badge>
        <span className="text-footnote text-muted-foreground">{formatDateTime(report.createdAt)}</span>
      </div>
      <p className="whitespace-pre-wrap break-words text-sm">{report.summary}</p>
      {report.humanAction ? (
        <p className="break-words text-footnote text-warning">
          {ACTION_LABELS[report.humanAction.type]}：{report.humanAction.prompt}
        </p>
      ) : null}
      {ids.length > 0 ? (
        <p className="flex flex-wrap gap-x-3 gap-y-1 text-footnote">
          {ids.map((id) => (
            <Link
              key={id}
              to={`/tasks/history/${id}`}
              className="font-medium text-primary-text underline decoration-primary-text/40 underline-offset-2 hover:decoration-primary-text"
            >
              查看流水线
            </Link>
          ))}
        </p>
      ) : null}
    </li>
  )
}

/** 汇报时间线（M72 §4.6.5）：新的在上，底部「加载更多」。 */
export function ReportTimeline({ ticketId, onCompose }: ReportTimelineProps) {
  const reports = useInfiniteQuery(ticketReportsInfiniteQuery(ticketId))
  const items = reports.data?.pages.flatMap((page) => page.items) ?? []
  const total = reports.data?.pages.at(-1)?.total ?? items.length

  return (
    <Section title="汇报" description={items.length > 0 ? `共 ${total} 条` : undefined}>
      {reports.isPending ? (
        <LoadingState label="正在读取汇报…" />
      ) : reports.error && items.length === 0 ? (
        <ErrorState
          message={`读取汇报失败：${toApiError(reports.error).message}`}
          onRetry={() => void reports.refetch()}
          retrying={reports.isFetching}
        />
      ) : items.length === 0 ? (
        <EmptyState
          title="还没有汇报"
          description="Worker 每轮结束时写一条"
          action={onCompose ? (
            <Button type="button" variant="secondary" size="sm" onClick={onCompose}>去发消息</Button>
          ) : undefined}
        />
      ) : (
        <ol aria-label="汇报时间线" className="space-y-2">
          {items.map((report) => <ReportItem key={report.id} report={report} />)}
        </ol>
      )}
      {reports.hasNextPage ? (
        <Button
          variant="outline"
          className="w-full"
          loading={reports.isFetchingNextPage}
          onClick={() => void reports.fetchNextPage()}
        >
          {reports.isFetchingNextPage ? '正在加载…' : '加载更多'}
        </Button>
      ) : null}
    </Section>
  )
}
