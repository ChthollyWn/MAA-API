import { Link } from 'react-router'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ResponsiveSheet } from '@/components/ui/sheet'
import type { SpanRow } from '@/features/runs/timeline'
import {
  formatDateTime, formatDuration, spanKindLabel, spanLabel, spanStatusLabel,
} from '@/features/runs/format'

interface SpanDetailSheetProps {
  row: SpanRow | null
  onOpenChange: (open: boolean) => void
  currentRunId: string | null
  titles: ReadonlyMap<string, string>
}

function attributeValue(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).join('、')
  if (value !== null && typeof value === 'object') {
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

/** span 详情抽屉：名字、种类、状态、开始、时长与属性表；属于别的运行时给入口。 */
export function SpanDetailSheet({ row, onOpenChange, currentRunId, titles }: SpanDetailSheetProps) {
  const span = row?.span ?? null
  const label = span ? spanLabel(span, titles) : '追踪详情'
  const attributes = span ? Object.entries(span.attributes ?? {}) : []
  const otherRun = span?.run_id && span.run_id !== currentRunId ? span.run_id : null

  return (
    <ResponsiveSheet
      open={row !== null}
      onOpenChange={onOpenChange}
      title={label}
      description={span ? `${spanKindLabel(span.kind)} · ${span.name}` : undefined}
      desktop="panel"
    >
      {span ? (
        <div className="space-y-4 pb-2">
          <dl className="divide-y rounded-xl border bg-card">
            <div className="flex items-start justify-between gap-3 px-3 py-2.5">
              <dt className="text-sm text-muted-foreground">名字</dt>
              <dd className="min-w-0 break-all text-right font-mono text-xs">{span.name}</dd>
            </div>
            <div className="flex items-start justify-between gap-3 px-3 py-2.5">
              <dt className="text-sm text-muted-foreground">种类</dt>
              <dd className="text-right text-sm">{spanKindLabel(span.kind)}</dd>
            </div>
            <div className="flex items-start justify-between gap-3 px-3 py-2.5">
              <dt className="text-sm text-muted-foreground">状态</dt>
              <dd className="text-right">
                <Badge variant={span.status === 'error' ? 'danger' : 'success'}>{spanStatusLabel(span.status)}</Badge>
              </dd>
            </div>
            <div className="flex items-start justify-between gap-3 px-3 py-2.5">
              <dt className="text-sm text-muted-foreground">开始</dt>
              <dd className="text-right text-sm tabular-nums">{formatDateTime(span.started_at)}</dd>
            </div>
            <div className="flex items-start justify-between gap-3 px-3 py-2.5">
              <dt className="text-sm text-muted-foreground">时长</dt>
              <dd className="text-right text-sm tabular-nums">{formatDuration(span.duration_ms)}</dd>
            </div>
          </dl>

          {otherRun ? (
            <Button asChild variant="outline" className="w-full">
              <Link to={`/more/runs/${otherRun}`} onClick={() => onOpenChange(false)}>打开这次运行</Link>
            </Button>
          ) : null}

          <section aria-label="属性" className="space-y-2">
            <h3 className="px-1 text-footnote font-medium text-muted-foreground">属性</h3>
            {attributes.length === 0 ? (
              <p className="rounded-xl border bg-card px-3 py-6 text-center text-footnote text-muted-foreground">没有属性</p>
            ) : (
              <dl className="divide-y overflow-hidden rounded-xl border bg-card">
                {attributes.map(([key, value]) => (
                  <div key={key} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-3 px-3 py-2">
                    <dt className="break-all font-mono text-xs text-muted-foreground">{key}</dt>
                    <dd className="break-all text-right font-mono text-xs">{attributeValue(value)}</dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
        </div>
      ) : null}
    </ResponsiveSheet>
  )
}

export default SpanDetailSheet
