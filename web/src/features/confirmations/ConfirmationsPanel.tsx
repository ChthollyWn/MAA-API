import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router'
import { Check, Clock3, X } from 'lucide-react'
import { queryOptions, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ErrorState, LoadingState } from '@/components/ui/states'
import { cn } from '@/lib/utils'

export interface Confirmation {
  confirmation_id: string
  action: string
  risk_level: string
  reason: string
  status: string
  requested_by?: string
  expires_at: string
  caller?: { kind?: string; name?: string | null; session_id?: string | null }
  payload: { tool_name?: string; arguments?: Record<string, unknown>; [key: string]: unknown }
}

interface ConfirmationPage {
  items: Confirmation[]
  total: number
  page: number
  size: number
}

/** 待确认列表的共享查询：外壳的入口计数与面板共用一份缓存，WS 事件经 query-bridge 使其失效。 */
export const pendingConfirmationsQuery = queryOptions({
  queryKey: keys.agent.pendingConfirmations(),
  queryFn: async () => {
    const { data, error, response } = await api.GET('/api/confirmations', {
      params: { query: { status: 'pending', page: 1, size: 20 } },
    })
    if (error) throw toApiError(error, response)
    return data as unknown as ConfirmationPage
  },
  staleTime: 5_000,
  retry: false,
})

function parseExpiry(value: string): number {
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function secondsRemaining(value: string, now: number): number {
  return Math.max(0, Math.ceil((parseExpiry(value) - now) / 1000))
}

function formatRemaining(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return minutes > 0 ? `${minutes} 分 ${rest} 秒` : `${rest} 秒`
}

function riskLabel(value: string, action: string): string {
  if (action === 'grant_atomic_ops') return '会话级屏幕操作授权'
  if (value === 'consume') return '资源消耗'
  if (value === 'destructive') return '破坏性操作'
  return '屏幕操作授权'
}

function callerLabel(item: Confirmation): string {
  const kind = item.caller?.kind ?? item.requested_by ?? '未知'
  const kindText = kind === 'internal' ? '内置 Agent' : kind === 'rest' ? 'REST 调用' : kind
  return item.caller?.name && item.caller.name !== kindText ? `${kindText} · ${item.caller.name}` : kindText
}

function confirmationError(error: unknown): string {
  const apiError = toApiError(error)
  if (apiError.status === 409) return '这项确认已处理或已过期，请刷新列表。'
  if (apiError.status === 403) return '服务拒绝了此操作，请检查授权。'
  return apiError.message || '请求失败，请稍后重试。'
}

export interface ConfirmationsPanelProps {
  /** 独立使用时显示「需要人工确认」标题；嵌在面板里时由面板标题代替。 */
  showHeading?: boolean
  /** 没有待确认、也没有深链时显示的内容；默认什么都不渲染。 */
  emptyFallback?: ReactNode
}

export function ConfirmationsPanel({ showHeading = true, emptyFallback = null }: ConfirmationsPanelProps) {
  const queryClient = useQueryClient()
  const [now, setNow] = useState(() => Date.now())
  const [actionError, setActionError] = useState<string | null>(null)
  // 推送通知的深链入口是 /?confirm=<id>：打开应用后要直接定位到那张卡片。
  const [searchParams, setSearchParams] = useSearchParams()
  const targetConfirmationId = searchParams.get('confirm')
  const targetRef = useRef<HTMLDivElement | null>(null)
  const pending = useQuery(pendingConfirmationsQuery)

  useEffect(() => {
    if (!pending.data?.items.length) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [pending.data?.items.length])

  useEffect(() => {
    if (!targetConfirmationId) return
    const node = targetRef.current
    if (!node || typeof node.scrollIntoView !== 'function') return
    node.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [targetConfirmationId, pending.data?.items.length])

  /** 处置完深链指向的确认后清掉查询参数，避免刷新时再次高亮一个已处理的卡片。 */
  const clearTargetParam = () => {
    if (!targetConfirmationId) return
    const next = new URLSearchParams(searchParams)
    next.delete('confirm')
    setSearchParams(next, { replace: true })
  }

  const resolve = useMutation({
    mutationFn: async (input: { confirmationId: string; approved: boolean }) => {
      const { data, error, response } = await api.POST('/api/confirmations/{confirmation_id}', {
        params: { path: { confirmation_id: input.confirmationId } },
        body: { approved: input.approved },
      })
      if (error) throw toApiError(error, response)
      return data
    },
    onMutate: () => setActionError(null),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.agent.pendingConfirmations() })
      void queryClient.invalidateQueries({ queryKey: keys.agent.audits() })
      clearTargetParam()
    },
    onError: (error) => {
      setActionError(confirmationError(error))
      if (toApiError(error).status === 409) {
        void queryClient.invalidateQueries({ queryKey: keys.agent.pendingConfirmations() })
        void queryClient.invalidateQueries({ queryKey: keys.agent.audits() })
      }
    },
  })

  if (pending.isPending) return <LoadingState label="正在读取待确认操作…" />
  if (pending.error) {
    return (
      <ErrorState
        title="读取待确认操作失败"
        message={`读取待确认操作失败：${confirmationError(pending.error)}`}
        onRetry={() => void pending.refetch()}
        retrying={pending.isFetching}
      />
    )
  }

  const items = pending.data?.items ?? []
  // 深链指向的确认已经不在待处理列表里（已处置或已过期）：提示一次并把参数清掉，
  // 否则用户会停在一个看起来什么都没发生的页面上。列表里还有别的待确认时也要提示，
  // 免得用户把看到的第一条误认成通知里的那一条。
  const missingTarget = Boolean(targetConfirmationId) && !items.some((item) => item.confirmation_id === targetConfirmationId)
  const missingNotice = missingTarget ? (
    <InlineAlert
      tone="info"
      role="status"
      action={<Button type="button" variant="outline" size="sm" onClick={clearTargetParam}>知道了</Button>}
    >
      这条待确认操作已经处理或已过期，无需再操作。
    </InlineAlert>
  ) : null
  if (items.length === 0) return missingNotice ?? <>{emptyFallback}</>

  return (
    <section className="space-y-3" aria-labelledby={showHeading ? 'pending-confirmations-title' : undefined}>
      {missingNotice}
      {showHeading ? (
        <header className="flex items-center gap-2 px-1">
          <h2 id="pending-confirmations-title" className="text-headline font-semibold">需要人工确认</h2>
          <Badge variant="warning">{items.length}</Badge>
        </header>
      ) : null}
      {actionError ? <InlineAlert tone="danger">{actionError}</InlineAlert> : null}
      {items.map((item) => {
        const remaining = secondsRemaining(item.expires_at, now)
        const expired = remaining === 0
        const grant = item.action === 'grant_atomic_ops'
        const grantWindow = Number(item.payload.window_seconds)
        const grantSessionId = typeof item.payload.session_id === 'string' ? item.payload.session_id : null
        const busy = resolve.isPending && resolve.variables?.confirmationId === item.confirmation_id
        const isTarget = item.confirmation_id === targetConfirmationId
        return (
          <div
            key={item.confirmation_id}
            ref={isTarget ? targetRef : undefined}
            className={cn('rounded-xl', isTarget && 'ring-2 ring-primary ring-offset-2 ring-offset-surface-raised')}
          >
            <article className="overflow-hidden rounded-xl border bg-card shadow-card">
              <div className="space-y-3 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 space-y-1">
                    <h3 className="break-words text-[0.9375rem] font-semibold leading-6">{grant ? '授权会话操作游戏界面' : item.payload.tool_name ?? item.action}</h3>
                    <p className="text-sm text-muted-foreground">
                      <span className="font-medium text-warning">{riskLabel(item.risk_level, item.action)}</span> · {item.reason}
                    </p>
                  </div>
                  <Badge variant={expired ? 'danger' : 'warning'} className="tabular-nums">
                    <Clock3 aria-hidden="true" />
                    {expired ? '已过期' : formatRemaining(remaining)}
                  </Badge>
                </div>
                <p className="text-footnote text-muted-foreground">调用方：{callerLabel(item)}</p>
                {grant ? (
                  <p className="text-sm">
                    授权目标会话：<code className="break-all rounded bg-surface-2 px-1 py-0.5 font-mono text-[0.8125rem]">{grantSessionId ?? '无法识别目标会话'}</code>。批准后，该会话获得 {grantWindow > 0 ? `${Math.floor(grantWindow / 60)} 分钟` : '限时'}授权；点击、滑动、长按、输入和按键操作都会记入审计。
                  </p>
                ) : null}
                <details className="group rounded-lg bg-surface-2/70">
                  <summary className="flex min-h-10 list-none items-center px-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">查看操作参数</summary>
                  <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words border-t border-border/70 px-3 py-2 font-mono text-xs leading-5">
                    {JSON.stringify(item.payload.arguments ?? {}, null, 2)}
                  </pre>
                </details>
              </div>
              <div className="grid grid-cols-2 gap-2 border-t bg-surface-2/40 p-3">
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy || expired}
                  onClick={() => resolve.mutate({ confirmationId: item.confirmation_id, approved: false })}
                >
                  <X aria-hidden="true" />拒绝
                </Button>
                <Button
                  type="button"
                  loading={busy}
                  disabled={expired}
                  onClick={() => resolve.mutate({ confirmationId: item.confirmation_id, approved: true })}
                >
                  {busy ? null : <Check aria-hidden="true" />}
                  {busy ? '处理中…' : '批准'}
                </Button>
              </div>
            </article>
          </div>
        )
      })}
    </section>
  )
}
