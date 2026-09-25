import { useEffect, useState } from 'react'
import { AlertTriangle, Check, Clock3, LoaderCircle, X } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

interface Confirmation {
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

function confirmationError(error: unknown): string {
  const apiError = toApiError(error)
  if (apiError.status === 409) return '这项确认已处理或已过期，请刷新列表。'
  if (apiError.status === 403) return '服务拒绝了此操作，请检查授权。'
  return apiError.message || '请求失败，请稍后重试。'
}

export function ConfirmationsPanel() {
  const queryClient = useQueryClient()
  const [now, setNow] = useState(() => Date.now())
  const [actionError, setActionError] = useState<string | null>(null)
  const pending = useQuery({
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

  useEffect(() => {
    if (!pending.data?.items.length) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [pending.data?.items.length])

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
    },
    onError: (error) => {
      setActionError(confirmationError(error))
      if (toApiError(error).status === 409) {
        void queryClient.invalidateQueries({ queryKey: keys.agent.pendingConfirmations() })
        void queryClient.invalidateQueries({ queryKey: keys.agent.audits() })
      }
    },
  })

  if (pending.isPending) {
    return <p className="flex items-center gap-2 py-3 text-sm text-muted-foreground" role="status"><LoaderCircle aria-hidden="true" className="size-4 animate-spin" />正在读取待确认操作…</p>
  }
  if (pending.error) {
    return (
      <Card className="border-destructive/40">
        <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4">
          <p className="text-sm text-destructive" role="alert">读取待确认操作失败：{confirmationError(pending.error)}</p>
          <Button type="button" variant="outline" disabled={pending.isFetching} onClick={() => void pending.refetch()}>
            {pending.isFetching ? '正在重试…' : '重试'}
          </Button>
        </CardContent>
      </Card>
    )
  }

  const items = pending.data?.items ?? []
  if (items.length === 0) return null

  return (
    <section className="space-y-3 py-4" aria-labelledby="pending-confirmations-title">
      <header className="flex items-center gap-2">
        <AlertTriangle aria-hidden="true" className="size-5 text-warning" />
        <h2 id="pending-confirmations-title" className="font-semibold">需要人工确认</h2>
        <Badge variant="warning">{items.length}</Badge>
      </header>
      {actionError ? <p className="text-sm text-destructive" role="alert">{actionError}</p> : null}
      {items.map((item) => {
        const remaining = secondsRemaining(item.expires_at, now)
        const grant = item.action === 'grant_atomic_ops'
        const grantWindow = Number(item.payload.window_seconds)
        const grantSessionId = typeof item.payload.session_id === 'string' ? item.payload.session_id : null
        const callerName = item.caller?.name ? ` · ${item.caller.name}` : ''
        const busy = resolve.isPending && resolve.variables?.confirmationId === item.confirmation_id
        return (
          <Card key={item.confirmation_id} className="overflow-hidden border-warning/50 border-l-4 border-l-warning">
            <CardHeader className="pb-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <CardTitle className="break-words text-base">{grant ? '授权会话操作游戏界面' : item.payload.tool_name ?? item.action}</CardTitle>
                  <CardDescription className="mt-1">{riskLabel(item.risk_level, item.action)} · {item.reason}</CardDescription>
                </div>
                <Badge variant={remaining === 0 ? 'destructive' : 'warning'} className="shrink-0">
                  <Clock3 aria-hidden="true" className="mr-1 size-3" />
                  {remaining === 0 ? '已过期' : formatRemaining(remaining)}
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs text-muted-foreground">调用方：{item.caller?.kind ?? item.requested_by ?? '未知'}{callerName}</p>
              {grant ? <p className="text-sm">授权目标会话：<code className="break-all">{grantSessionId ?? '无法识别目标会话'}</code>。批准后，该会话获得 {grantWindow > 0 ? `${Math.floor(grantWindow / 60)} 分钟` : '限时'}授权；点击、滑动、长按、输入和按键操作都会记入审计。</p> : null}
              <details className="rounded-lg border bg-muted/35 px-3 py-2">
                <summary className="min-h-8 cursor-pointer py-1 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">查看操作参数</summary>
                <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words border-t pt-2 font-mono text-xs leading-5">
                  {JSON.stringify(item.payload.arguments ?? {}, null, 2)}
                </pre>
              </details>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  disabled={busy || remaining === 0}
                  onClick={() => resolve.mutate({ confirmationId: item.confirmation_id, approved: true })}
                >
                  {busy ? <LoaderCircle aria-hidden="true" className="animate-spin" /> : <Check aria-hidden="true" />}
                  {busy ? '处理中…' : '批准'}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy || remaining === 0}
                  onClick={() => resolve.mutate({ confirmationId: item.confirmation_id, approved: false })}
                >
                  <X aria-hidden="true" />拒绝
                </Button>
              </div>
            </CardContent>
          </Card>
        )
      })}
    </section>
  )
}
