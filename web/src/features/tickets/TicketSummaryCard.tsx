import type { ReactNode } from 'react'
import { Badge } from '@/components/ui/badge'
import {
  ORIGIN_LABELS,
  REPORT_STATUS_LABELS,
  formatDateTime,
  workerTitle,
  type HumanAction,
  type TicketDetailView,
} from './format'
import { TicketStatusBadge } from './TicketStatusBadge'

export interface TicketSummaryCardProps {
  detail: TicketDetailView
  specTitles: ReadonlyMap<string, string>
  /** 就地的确认卡、提问卡（工单页把本工单仍挂着的卡放这里）。 */
  children?: ReactNode
}

const ACTION_LABELS: Record<HumanAction['type'], string> = {
  confirmation: '需要你确认',
  question: '需要你回答',
  decision: '需要你决定',
}

const REPORT_TONES: Record<string, 'primary' | 'warning' | 'success' | 'danger' | 'neutral'> = {
  progress: 'primary',
  waiting: 'warning',
  ready: 'success',
  blocked: 'danger',
  failed: 'danger',
  dropped: 'neutral',
}

/** 概要卡（M72 §4.6.5）：状态、Worker、来源、创建与更新时间、最近汇报摘要与待办提示。 */
export function TicketSummaryCard({ detail, specTitles, children }: TicketSummaryCardProps) {
  const { ticket } = detail
  const waiting = ticket.status === 'waiting' || ticket.status === 'blocked'
  const humanAction = waiting ? ticket.humanAction : null
  return (
    <article aria-label="工单概要" className="rounded-xl border bg-card p-4 shadow-card">
      <div className="flex flex-wrap items-center gap-2">
        <TicketStatusBadge status={ticket.status} />
        <h2 className="min-w-0 flex-1 break-words text-[0.9375rem] font-semibold leading-6">{ticket.title}</h2>
      </div>
      <dl className="mt-2 space-y-1 text-footnote text-muted-foreground">
        <div className="flex gap-1.5">
          <dt className="shrink-0">Worker</dt>
          <dd className="min-w-0 break-words text-foreground">{workerTitle(ticket.kind, specTitles)}</dd>
        </div>
        <div className="flex gap-1.5">
          <dt className="shrink-0">来源</dt>
          <dd className="min-w-0">{ORIGIN_LABELS[ticket.origin] ?? ticket.origin}</dd>
        </div>
        <div className="flex flex-wrap gap-x-1.5">
          <dt className="shrink-0">时间</dt>
          <dd className="min-w-0">
            创建 {formatDateTime(ticket.createdAt)} · 更新 {formatDateTime(ticket.updatedAt)}
          </dd>
        </div>
      </dl>
      {ticket.summary ? (
        <div className="mt-3 space-y-1">
          <p className="flex items-center gap-1.5 text-footnote text-muted-foreground">
            最近汇报
            {ticket.lastReportStatus ? (
              <Badge variant={REPORT_TONES[ticket.lastReportStatus] ?? 'neutral'}>
                {REPORT_STATUS_LABELS[ticket.lastReportStatus] ?? ticket.lastReportStatus}
              </Badge>
            ) : null}
          </p>
          <p className="whitespace-pre-wrap break-words text-sm">{ticket.summary}</p>
        </div>
      ) : null}
      {humanAction ? (
        <p className="mt-3 rounded-lg bg-warning-soft px-3 py-2 text-sm text-warning">
          {ACTION_LABELS[humanAction.type]}：{humanAction.prompt}
        </p>
      ) : null}
      {children ? <div className="mt-3 space-y-3">{children}</div> : null}
    </article>
  )
}
