import { useInfiniteQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { Radar } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import type { Trigger } from './api'
import { evalBadge, formatDateTime, routeBadge, sourceLabel } from './format'
import { triggersInfiniteQuery } from './queries'

const card = 'rounded-xl border bg-card shadow-card'
const rowClassName = 'flex min-h-14 w-full items-start gap-3 px-4 py-3 text-left'

function TriggerRow({ trigger }: { trigger: Trigger }) {
  const route = routeBadge(trigger)
  const evaluation = evalBadge(trigger.eval_status)
  const body = (
    <>
      <span className="min-w-0 flex-1 space-y-1">
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-footnote text-muted-foreground">{formatDateTime(trigger.created_at)}</span>
          <Badge variant={route.tone}>{route.label}</Badge>
          <Badge variant={evaluation.tone}>{evaluation.label}</Badge>
        </span>
        <span className="block break-words text-sm font-medium leading-5">{trigger.summary}</span>
        <span className="flex flex-wrap items-center gap-x-1.5 text-footnote text-muted-foreground">
          <span>{sourceLabel(trigger.source)}</span>
          <span aria-hidden="true">·</span>
          <code className="truncate font-mono">{trigger.kind}</code>
        </span>
      </span>
    </>
  )
  if (trigger.ticket_id) {
    return (
      <li>
        <Link
          to={`/tickets/${trigger.ticket_id}`}
          className={`${rowClassName} transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring`}
        >
          {body}
        </Link>
      </li>
    )
  }
  return <li className={rowClassName}>{body}</li>
}

/** 「事件记录」分页签：时间、摘要、来源、去向与研判状态；建过工单的行可点进工单页。 */
export function TriggerLog() {
  const triggers = useInfiniteQuery(triggersInfiniteQuery())
  const items = triggers.data?.pages.flatMap((page) => page.items) ?? []

  let content
  if (triggers.isPending) {
    content = <LoadingState label="正在读取事件记录…" />
  } else if (triggers.isError) {
    content = (
      <div className={card}>
        <ErrorState
          title="事件记录读取失败"
          message={toApiError(triggers.error).message}
          onRetry={() => void triggers.refetch()}
          retrying={triggers.isFetching}
        />
      </div>
    )
  } else if (items.length === 0) {
    content = (
      <div className={card}>
        <EmptyState
          icon={Radar}
          title="还没有事件"
          description="流水线结束、设备断开后会按时间列出"
          action={(
            <Button asChild variant="secondary" size="sm">
              <Link to="/tasks">去提交任务</Link>
            </Button>
          )}
        />
      </div>
    )
  } else {
    content = (
      <>
        <ul className={`${card} divide-y divide-border overflow-hidden`}>
          {items.map((trigger) => (
            <TriggerRow key={trigger.id} trigger={trigger} />
          ))}
        </ul>
        {triggers.hasNextPage ? (
          <div className="flex justify-center pt-1">
            <Button
              type="button"
              variant="outline"
              size="sm"
              loading={triggers.isFetchingNextPage}
              onClick={() => void triggers.fetchNextPage()}
            >
              {triggers.isFetchingNextPage ? '正在加载…' : '加载更多'}
            </Button>
          </div>
        ) : null}
      </>
    )
  }

  return <div className="space-y-3">{content}</div>
}
