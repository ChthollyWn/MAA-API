import { Badge, type StatusTone } from '@/components/ui/badge'
import { TICKET_STATUS_LABELS, type TicketStatusValue } from './format'

export interface TicketStatusBadgeProps {
  status: string
}

const STATUS_TONES: Record<TicketStatusValue, StatusTone> = {
  open: 'primary',
  waiting: 'warning',
  blocked: 'danger',
  done: 'success',
  failed: 'danger',
  cancelled: 'neutral',
}

/** 工单状态徽章；表里没有的取值原样显示（§4.6.5）。 */
export function TicketStatusBadge({ status }: TicketStatusBadgeProps) {
  const known = Object.hasOwn(TICKET_STATUS_LABELS, status)
  return (
    <Badge variant={known ? STATUS_TONES[status as TicketStatusValue] : 'neutral'}>
      {known ? TICKET_STATUS_LABELS[status as TicketStatusValue] : status}
    </Badge>
  )
}
