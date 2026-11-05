import { Link } from 'react-router'
import { toApiError } from '@/api/errors'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ErrorState, LoadingState } from '@/components/ui/states'
import {
  ALERT_SEVERITY_LABELS,
  ALERT_TARGET_LABELS,
  formatWatchdogTime,
} from '@/features/watchdog/api'
import { ConfirmationCard } from '@/features/agent/ConfirmationCard'
import { QuestionCard } from '@/features/agent/QuestionCard'
import { ErrorAlert } from '@/features/agent/StatusAlerts'
import { TICKET_STATUS_LABELS } from './api'
import { originLabel, spendAttentionText } from './attention'
import type { AttentionState } from './use-attention'

export interface AttentionListProps {
  attention: AttentionState
  specTitles: ReadonlyMap<string, string>
}

/**
 * 「待你处理」列表（M72 §4.6.4）：确认、提问就地渲染卡片，工单渲染成可打开的条目。
 * 每项上方一行来源文案；顺序由 `buildAttentionItems` 决定。
 */
export function AttentionList({ attention, specTitles }: AttentionListProps) {
  if (attention.isPending && attention.items.length === 0) {
    return <LoadingState label="正在读取…" />
  }
  if (attention.error) {
    return (
      <ErrorState
        message={`读取待你处理的事项失败：${toApiError(attention.error).message}`}
        onRetry={attention.refetch}
      />
    )
  }
  if (attention.items.length === 0) {
    return <p className="px-1 py-4 text-center text-footnote text-muted-foreground">还没有要你处理的事</p>
  }

  return (
    <div className="space-y-3">
      {attention.resolver.error ? (
        <ErrorAlert error={attention.resolver.error} onDismiss={attention.resolver.clearError} />
      ) : null}
      {attention.alertResolver.error ? (
        <ErrorAlert error={attention.alertResolver.error} onDismiss={attention.alertResolver.clearError} />
      ) : null}
      <ol aria-label="待你处理" className="space-y-3">
        {attention.items.map((item) => {
          if (item.kind === 'confirmation') {
            return (
              <li key={`confirmation-${item.id}`} data-attention-kind="confirmation" data-attention-id={item.id} className="space-y-1.5">
                <p className="text-footnote text-muted-foreground">
                  {originLabel(item.origin, item.confirmation.caller, specTitles)}
                </p>
                <ConfirmationCard
                  confirmationId={item.confirmation.confirmation_id}
                  busy={attention.resolver.busyFor(item.confirmation.confirmation_id)}
                  onResolve={attention.resolver.resolve}
                />
              </li>
            )
          }
          if (item.kind === 'question') {
            return (
              <li key={`question-${item.id}`} data-attention-kind="question" data-attention-id={item.id} className="space-y-1.5">
                <p className="text-footnote text-muted-foreground">
                  {originLabel(item.origin, undefined, specTitles)}
                </p>
                {/* 来源行由列表统一渲染，卡片自己的那行让位，避免同一句话出现两遍。 */}
                <QuestionCard
                  question={{ ...item.question, origin: null }}
                  onResolved={attention.onQuestionResolved}
                />
              </li>
            )
          }
          if (item.kind === 'memory') {
            return (
              <li key={`memory-${item.id}`} data-attention-kind="memory" data-attention-id={item.id}>
                <Link
                  to="/more/memories"
                  className="block rounded-xl border bg-card px-3.5 py-3 shadow-card transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="flex items-start justify-between gap-2">
                    <span className="min-w-0 break-words text-sm font-medium text-foreground">
                      {item.count} 条记忆待审核
                    </span>
                    <Badge variant="primary">记忆</Badge>
                  </span>
                  {item.previews.map((preview) => (
                    <span key={preview} className="mt-1 block truncate text-footnote text-muted-foreground">
                      {preview}
                    </span>
                  ))}
                </Link>
              </li>
            )
          }
          if (item.kind === 'alert') {
            const alert = item.alert
            return (
              <li
                key={`alert-${item.id}`}
                data-attention-kind="alert"
                data-attention-id={item.id}
                className="rounded-xl border border-danger/25 bg-card px-3.5 py-3 shadow-card"
              >
                <span className="flex items-start justify-between gap-2">
                  <span className="min-w-0 break-words text-sm font-medium text-foreground">{alert.message}</span>
                  <Badge variant="danger">
                    {ALERT_SEVERITY_LABELS[alert.severity] ?? alert.severity}
                  </Badge>
                </span>
                <span className="mt-1 block break-words text-footnote text-muted-foreground">
                  对象：{ALERT_TARGET_LABELS[alert.target_kind] ?? alert.target_kind}
                  {alert.target_id ? `「${alert.target_id}」` : ''} · 时间：{formatWatchdogTime(alert.created_at)}
                </span>
                <div className="mt-2">
                  <Button
                    size="sm"
                    variant="outline"
                    loading={attention.alertResolver.busyFor(alert.id)}
                    onClick={() => attention.alertResolver.resolve(alert.id)}
                  >
                    标记已处理
                  </Button>
                </div>
              </li>
            )
          }
          if (item.kind === 'spend') {
            return (
              <li key={`spend-${item.id}`} data-attention-kind="spend" data-attention-id={item.id}>
                <Link
                  to="/more/spend"
                  className="block rounded-xl border bg-card px-3.5 py-3 shadow-card transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="flex items-start justify-between gap-2">
                    <span className="min-w-0 break-words text-sm font-medium text-foreground">
                      {spendAttentionText(item.attention)}
                    </span>
                    <Badge variant={item.attention.kind === 'blocked' ? 'danger' : 'warning'}>消耗</Badge>
                  </span>
                </Link>
              </li>
            )
          }
          const ticket = item.ticket
          return (
            <li key={`ticket-${item.id}`} data-attention-kind="ticket" data-attention-id={item.id}>
              <Link
                to={`/tickets/${ticket.id}`}
                className="block rounded-xl border bg-card px-3.5 py-3 shadow-card transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="flex items-start justify-between gap-2">
                  <span className="min-w-0 break-words text-sm font-medium text-foreground">{ticket.title}</span>
                  <Badge variant={ticket.status === 'blocked' ? 'danger' : 'warning'}>
                    {TICKET_STATUS_LABELS[ticket.status] ?? ticket.status}
                  </Badge>
                </span>
                {ticket.humanAction?.prompt ? (
                  <span className="mt-1 block break-words text-footnote text-muted-foreground">{ticket.humanAction.prompt}</span>
                ) : ticket.summary ? (
                  <span className="mt-1 block break-words text-footnote text-muted-foreground">{ticket.summary}</span>
                ) : null}
              </Link>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
