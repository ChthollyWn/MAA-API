import { useId } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, ShieldAlert, X } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { expiryLabel, pretty } from './format'
import type { AgentConfirmation } from './types'

export interface ConfirmationCardProps {
  confirmationId: string
  /** 正在提交的是哪个决定；两个按钮都会禁用，只在被点的那个上显示加载。 */
  busy: 'approve' | 'reject' | null
  onResolve: (id: string, approved: boolean) => void
}

/** 对话里的待确认卡：读确认详情、查看参数，批准并继续或拒绝。 */
export function ConfirmationCard({ confirmationId, busy, onResolve }: ConfirmationCardProps) {
  const titleId = useId()
  const confirmation = useQuery({
    queryKey: [...keys.agent.confirmations(), confirmationId],
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/confirmations/{confirmation_id}', {
        params: { path: { confirmation_id: confirmationId } },
      })
      if (error) throw toApiError(error, response)
      return data as unknown as AgentConfirmation
    },
    retry: false,
  })
  const detail = confirmation.data
  const grant = detail?.action === 'grant_atomic_ops'
  const approved = detail?.status === 'approved'
  const rejected = detail?.status === 'rejected' || detail?.status === 'expired'
  const grantMinutes = Math.floor(Number(detail?.payload.window_seconds) / 60)
  const unavailable = busy !== null || confirmation.isLoading || !detail

  return (
    <article aria-labelledby={titleId} aria-live="polite" className="overflow-hidden rounded-xl border bg-card shadow-card">
      <div className="space-y-3 p-4">
        <div className="flex items-start gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-full bg-warning-soft text-warning">
            <ShieldAlert aria-hidden="true" className="size-[1.125rem]" />
          </span>
          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <h3 id={titleId} className="text-[0.9375rem] font-semibold leading-6">需要确认此操作</h3>
              <Badge variant={approved ? 'success' : rejected ? 'danger' : 'warning'} className="tabular-nums">
                {approved ? '已批准' : rejected ? '已拒绝或过期' : expiryLabel(detail?.expires_at)}
              </Badge>
            </div>
            <p className="break-words text-sm text-muted-foreground">
              {detail ? (grant ? '会话级屏幕操作授权' : detail.reason) : '正在读取确认详情…'}
            </p>
          </div>
        </div>
        {confirmation.error ? <InlineAlert tone="danger">读取确认详情失败：{toApiError(confirmation.error).message}</InlineAlert> : null}
        {grant && !approved && !rejected ? (
          <p className="text-sm">
            批准后本会话获得{grantMinutes > 0 ? ` ${grantMinutes} 分钟` : '限时'}屏幕操作授权，期间的点击、滑动、长按、输入和按键都会记入审计。
          </p>
        ) : null}
        {detail?.payload.arguments ? (
          <details className="group rounded-lg bg-surface-2/70">
            <summary className="flex min-h-10 cursor-pointer list-none items-center px-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">查看操作参数</summary>
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words border-t border-border/70 px-3 py-2 font-mono text-xs leading-5">{pretty(detail.payload.arguments)}</pre>
          </details>
        ) : null}
        {approved ? (
          <p className="text-sm text-success">已批准，Agent 正在继续处理。</p>
        ) : rejected ? (
          <p className="text-sm text-muted-foreground">确认已结束，本轮不会重试该操作。</p>
        ) : (
          <p className="text-footnote text-muted-foreground">此操作会经过服务端策略和审计；拒绝或过期后 Agent 不会重试。</p>
        )}
      </div>
      {approved || rejected ? null : (
        <div className="grid grid-cols-2 gap-2 border-t bg-surface-2/40 p-3">
          <Button type="button" variant="outline" disabled={unavailable} loading={busy === 'reject'} onClick={() => onResolve(confirmationId, false)}>
            {busy === 'reject' ? null : <X aria-hidden="true" />}
            拒绝
          </Button>
          <Button type="button" disabled={unavailable} loading={busy === 'approve'} onClick={() => onResolve(confirmationId, true)}>
            {busy === 'approve' ? null : <Check aria-hidden="true" />}
            {busy === 'approve' ? '正在处理…' : '批准并继续'}
          </Button>
        </div>
      )}
    </article>
  )
}
