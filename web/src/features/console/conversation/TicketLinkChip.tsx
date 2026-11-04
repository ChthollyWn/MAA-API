import { Link } from 'react-router'
import { Ticket } from 'lucide-react'
import { isRecord, stringValue } from '@/features/agent/format'
import type { ToolView } from '@/features/agent/transcript'
import { cn } from '@/lib/utils'

export interface TicketLinkChipProps {
  ticketId: string
  title: string
  /** 是这一轮新建的工单：文案用「已建工单」（实时副本的 ticket 段）。 */
  created?: boolean
}

/** 工具条下的工单胶囊（11 §3.3）：读工单、改工单、建工单之后都能一键打开。 */
export function TicketLinkChip({ ticketId, title, created }: TicketLinkChipProps) {
  const label = title || ticketId
  return (
    <Link
      to={`/tickets/${ticketId}`}
      className={cn(
        'inline-flex min-h-9 max-w-full items-center gap-1.5 rounded-full border border-border bg-surface px-3 text-footnote font-medium text-foreground',
        'transition-colors duration-150 hover:bg-surface-2',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
      )}
    >
      <Ticket aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
      <span className="truncate">{created ? `已建工单「${label}」` : `工单「${label}」`}</span>
    </Link>
  )
}

const TICKET_TOOLS: ReadonlySet<string> = new Set([
  'create_ticket',
  'send_to_ticket',
  'get_ticket',
  'cancel_ticket',
])

export interface ToolTicketLink {
  ticketId: string
  title: string
}

/**
 * 从工具结果里读工单胶囊（11 §3.3）：只认四个工单工具。
 * `create_ticket` 的 `{ticket_id, title}` 直接给出；`get_ticket` 的标题在 `ticket.title` 里；
 * `send_to_ticket`、`cancel_ticket` 没有标题时退回工单 id。
 */
export function toolTicketLink(tool: ToolView): ToolTicketLink | null {
  if (!TICKET_TOOLS.has(tool.name)) return null
  const body = isRecord(tool.result) && isRecord(tool.result.result) ? tool.result.result : null
  const ticket = body && isRecord(body.ticket) ? body.ticket : null
  const ticketId = body ? stringValue(body.ticket_id) || stringValue(ticket?.id) : ''
  if (!ticketId) return null
  const title = body
    ? stringValue(body.title) || stringValue(ticket?.title) || ticketId
    : ticketId
  return { ticketId, title }
}
