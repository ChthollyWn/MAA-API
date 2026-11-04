import { Link } from 'react-router'
import { toApiError } from '@/api/errors'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { TICKET_STATUS_LABELS, type TicketView } from './api'

export interface ActiveTicketsProps {
  tickets: readonly TicketView[]
  /** 当前持有设备的工单；它那一行带「占用设备」徽章。 */
  leaseTicketId: string | null
  specTitles: ReadonlyMap<string, string>
  isPending: boolean
  error: unknown
  onRetry: () => void
  /** 空态按钮「写下要做的事」：聚焦会话输入栏（§4.6.7）。 */
  onCompose?: () => void
}

export function ticketAgentTitle(ticket: TicketView, specTitles: ReadonlyMap<string, string>): string {
  if (!ticket.kind) return 'Worker'
  return specTitles.get(ticket.kind) ?? ticket.kind
}

/** 进行中的工单列表项；面板与手机工具条共用同一行内容。 */
export function ActiveTicketLink({
  ticket,
  leaseTicketId,
  specTitles,
  compact = false,
}: {
  ticket: TicketView
  leaseTicketId: string | null
  specTitles: ReadonlyMap<string, string>
  compact?: boolean
}) {
  const lease = ticket.id === leaseTicketId
  return (
    <Link
      to={`/tickets/${ticket.id}`}
      className={compact
        ? 'inline-flex min-h-9 max-w-full shrink-0 items-center gap-1.5 rounded-full border border-border bg-surface px-3 text-footnote font-medium text-foreground transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
        : 'block rounded-xl border bg-card px-3.5 py-3 shadow-card transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'}
    >
      <span className="flex min-w-0 items-center gap-2">
        <span className="min-w-0 truncate text-sm font-medium text-foreground">{ticket.title}</span>
        <Badge variant={ticket.status === 'open' ? 'primary' : ticket.status === 'waiting' ? 'warning' : 'neutral'}>
          {TICKET_STATUS_LABELS[ticket.status] ?? ticket.status}
        </Badge>
        {lease ? <Badge variant="outline">占用设备</Badge> : null}
      </span>
      {compact ? null : (
        <span className="mt-1 flex items-center justify-between gap-2 text-footnote text-muted-foreground">
          <span className="truncate">{ticketAgentTitle(ticket, specTitles)}</span>
          <span className="truncate">{ticket.lastReportStatus ?? ticket.summary ?? ''}</span>
        </span>
      )}
    </Link>
  )
}

/** 桌面右侧的「进行中的工单」（M72 §4.6.4）。 */
export function ActiveTicketsPanel({ tickets, leaseTicketId, specTitles, isPending, error, onRetry, onCompose }: ActiveTicketsProps) {
  return (
    <Section title="进行中的工单" description={`${tickets.length} 个`}>
      {isPending ? (
        <LoadingState label="正在读取工单…" />
      ) : error ? (
        <ErrorState message={`读取工单失败：${toApiError(error).message}`} onRetry={onRetry} />
      ) : tickets.length === 0 ? (
        onCompose ? (
          <EmptyState
            title="还没有进行中的工单"
            description="在总控会话里说出要做的事"
            action={<Button type="button" variant="secondary" size="sm" onClick={onCompose}>写下要做的事</Button>}
          />
        ) : (
          <p className="px-1 py-4 text-center text-footnote text-muted-foreground">还没有进行中的工单</p>
        )
      ) : (
        <ul className="space-y-2">
          {tickets.map((ticket) => (
            <li key={ticket.id}>
              <ActiveTicketLink ticket={ticket} leaseTicketId={leaseTicketId} specTitles={specTitles} />
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}
