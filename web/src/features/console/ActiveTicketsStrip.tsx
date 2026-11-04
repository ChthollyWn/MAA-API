import type { ActiveTicketsProps } from './ActiveTicketsPanel'
import { ActiveTicketLink } from './ActiveTicketsPanel'

/** 手机工具条里的工单条：有进行中的工单时横向排开（M72 §4.6.4）。 */
export function ActiveTicketsStrip({ tickets, leaseTicketId, specTitles }: ActiveTicketsProps) {
  if (tickets.length === 0) return null
  return (
    <nav aria-label="进行中的工单" className="min-w-0">
      <ul className="flex items-center gap-2 overflow-x-auto pb-0.5">
        {tickets.map((ticket) => (
          <li key={ticket.id} className="shrink-0">
            <ActiveTicketLink ticket={ticket} leaseTicketId={leaseTicketId} specTitles={specTitles} compact />
          </li>
        ))}
      </ul>
    </nav>
  )
}
