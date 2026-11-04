import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router'
import { useInfiniteQuery } from '@tanstack/react-query'
import { ChevronRight, Ticket } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { Page } from '@/components/layout/Page'
import { Button } from '@/components/ui/button'
import { SegmentedControl, type SegmentedOption } from '@/components/ui/segmented-control'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { useSpecTitles, ticketListInfiniteQuery } from './api'
import {
  ORIGIN_LABELS,
  formatDateTime,
  workerTitle,
  type TicketStatusValue,
  type TicketView,
} from './format'
import { TicketStatusBadge } from './TicketStatusBadge'

type TicketScope = 'active' | 'ended' | 'all'

const SCOPE_OPTIONS: readonly SegmentedOption<TicketScope>[] = [
  { value: 'active', label: '进行中' },
  { value: 'ended', label: '已结束' },
  { value: 'all', label: '全部' },
]

/** 「进行中」= open、waiting、blocked；「已结束」= done、failed、cancelled；「全部」不传（11 §4.1）。 */
const SCOPE_STATUSES: Record<TicketScope, TicketStatusValue[] | undefined> = {
  active: ['open', 'waiting', 'blocked'],
  ended: ['done', 'failed', 'cancelled'],
  all: undefined,
}

function scopeOf(value: string | null): TicketScope {
  return value === 'ended' || value === 'all' ? value : 'active'
}

function TicketRow({ ticket, specTitles }: {
  ticket: TicketView
  specTitles: ReadonlyMap<string, string>
}) {
  return (
    <li>
      <Link
        to={`/tickets/${ticket.id}`}
        className="flex min-h-14 w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <span className="min-w-0 flex-1 space-y-1">
          <span className="flex flex-wrap items-center gap-2">
            <TicketStatusBadge status={ticket.status} />
            <span className="min-w-0 break-words text-[0.9375rem] font-medium leading-6">{ticket.title}</span>
          </span>
          <span className="block text-footnote text-muted-foreground">
            {workerTitle(ticket.kind, specTitles)}
            {' · '}
            {ORIGIN_LABELS[ticket.origin] ?? ticket.origin}
            {' · '}
            {formatDateTime(ticket.updatedAt)}
          </span>
          {ticket.summary ? (
            <span className="line-clamp-2 block break-words text-footnote text-muted-foreground">{ticket.summary}</span>
          ) : null}
        </span>
        <ChevronRight aria-hidden="true" className="mt-1 size-4 shrink-0 text-muted-foreground/70" />
      </Link>
    </li>
  )
}

/** 工单列表（M72 §4.6.5）：吸顶分段控件按 `?status=` 筛选，整行链接，底部「加载更多」。 */
export default function TicketsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const scope = scopeOf(searchParams.get('status'))
  const specTitles = useSpecTitles()

  const filters = useMemo(() => ({ status: SCOPE_STATUSES[scope] }), [scope])
  const tickets = useInfiniteQuery(ticketListInfiniteQuery(filters))
  const items = tickets.data?.pages.flatMap((page) => page.items) ?? []
  const total = tickets.data?.pages.at(-1)?.total ?? items.length

  const changeScope = (next: TicketScope) => {
    const params = new URLSearchParams(searchParams)
    params.set('status', next)
    setSearchParams(params, { replace: true })
  }

  const toolbar = (
    <SegmentedControl
      ariaLabel="工单范围"
      value={scope}
      onValueChange={changeScope}
      options={SCOPE_OPTIONS}
      fullWidth
    />
  )

  return (
    <Page title="工单" toolbar={toolbar}>
      <div className="pb-4">
        {tickets.isPending ? (
          <div className="rounded-xl border bg-card shadow-card">
            <LoadingState label="正在读取工单…" />
          </div>
        ) : tickets.error && items.length === 0 ? (
          <div className="rounded-xl border bg-card shadow-card">
            <ErrorState
              message={`读取工单失败：${toApiError(tickets.error).message}`}
              onRetry={() => void tickets.refetch()}
              retrying={tickets.isFetching}
            />
          </div>
        ) : items.length === 0 ? (
          <div className="rounded-xl border bg-card shadow-card">
            <EmptyState
              icon={Ticket}
              title="还没有工单"
              description="在指挥台告诉总控要做什么"
              action={(
                <Button asChild variant="secondary" size="sm">
                  <Link to="/">前往指挥台</Link>
                </Button>
              )}
            />
          </div>
        ) : (
          <ul aria-label="工单列表" className="divide-y overflow-hidden rounded-xl border bg-card shadow-card">
            {items.map((ticket) => (
              <TicketRow key={ticket.id} ticket={ticket} specTitles={specTitles} />
            ))}
          </ul>
        )}
        {tickets.hasNextPage ? (
          <Button
            variant="outline"
            className="mt-3 w-full"
            loading={tickets.isFetchingNextPage}
            onClick={() => void tickets.fetchNextPage()}
          >
            {tickets.isFetchingNextPage ? '正在加载…' : '加载更多'}
          </Button>
        ) : items.length > 0 ? (
          <p className="mt-3 text-center text-footnote text-muted-foreground">已显示全部 {total} 条</p>
        ) : null}
      </div>
    </Page>
  )
}
