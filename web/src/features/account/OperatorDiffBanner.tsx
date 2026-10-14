import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { toast } from '@/components/ui/toaster'
import { accountKeys, dismissOperatorDiffs, fetchOperatorDiffs } from './api'
import { diffText, errorText, formatDateTime } from './format'

/**
 * 差异提示条（规格 §7.3、§7.5）：识别覆盖了上次识别之后被手动改过的字段时，
 * 逐条或一次性「知道了」。逐条传 `ids`，全部不传 body。
 */
export function OperatorDiffBanner() {
  const queryClient = useQueryClient()
  const diffs = useQuery({
    queryKey: accountKeys.diffs(),
    queryFn: ({ signal }) => fetchOperatorDiffs(signal),
    retry: false,
  })

  const dismiss = useMutation({
    mutationFn: (ids?: number[]) => dismissOperatorDiffs(ids),
    onSuccess: async (dismissed, ids) => {
      toast.success(ids?.length ? '已知道这处差异' : `已知道全部 ${dismissed} 处差异`)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: accountKeys.diffs() }),
        queryClient.invalidateQueries({ queryKey: accountKeys.summary() }),
      ])
    },
  })

  const items = diffs.data ?? []
  if (diffs.error && items.length === 0) {
    return (
      <InlineAlert
        tone="danger"
        title="读取识别差异失败"
        action={<Button variant="outline" size="sm" loading={diffs.isFetching} onClick={() => void diffs.refetch()}>重试</Button>}
      >
        {errorText(diffs.error)}
      </InlineAlert>
    )
  }
  if (items.length === 0) return null

  return (
    <section aria-label="识别差异" className="space-y-2.5 rounded-xl border border-warning/40 bg-warning-soft/60 p-3.5">
      <div className="flex flex-wrap items-start justify-between gap-2.5">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <AlertTriangle aria-hidden="true" className="mt-0.5 size-[1.125rem] shrink-0 text-warning" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm font-semibold">
                识别结果覆盖了 {items.length} 处手动修改
              </p>
              <Badge variant="warning" className="tabular-nums">{items.length}</Badge>
            </div>
            <p className="mt-0.5 text-footnote text-muted-foreground">确认无误后逐条或一次性标记为已知；标记只影响这条提示。</p>
          </div>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="shrink-0 bg-card"
          loading={dismiss.isPending && !dismiss.variables?.length}
          onClick={() => dismiss.mutate(undefined)}
        >
          {dismiss.isPending && !dismiss.variables?.length ? null : <Check aria-hidden="true" />}
          {dismiss.isPending && !dismiss.variables?.length ? '处理中…' : '全部知道了'}
        </Button>
      </div>
      <ul className="max-h-60 divide-y divide-warning/20 overflow-y-auto overscroll-contain rounded-lg border border-warning/30 bg-card">
        {items.map((diff) => {
          const timeText = formatDateTime(diff.created_at)
          return (
            <li key={diff.id} className="flex items-center justify-between gap-3 px-3 py-2.5">
              <div className="min-w-0 flex-1 space-y-0.5">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                  <span className="truncate text-sm font-medium">{diff.name}</span>
                  {timeText ? (
                    <span className="text-footnote tabular-nums text-muted-foreground">{timeText}</span>
                  ) : null}
                </div>
                <p className="text-footnote tabular-nums text-muted-foreground">{diffText(diff)}</p>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="shrink-0"
                loading={dismiss.isPending && dismiss.variables?.[0] === diff.id}
                onClick={() => dismiss.mutate([diff.id])}
              >
                知道了
              </Button>
            </li>
          )
        })}
      </ul>
      {dismiss.error ? <InlineAlert tone="danger">标记失败：{errorText(dismiss.error)}</InlineAlert> : null}
    </section>
  )
}
