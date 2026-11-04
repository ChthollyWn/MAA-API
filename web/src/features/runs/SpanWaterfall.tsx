import { useMemo } from 'react'
import { InfoHint } from '@/components/ui/info-hint'
import { InlineAlert } from '@/components/ui/inline-alert'
import type { SpanOut } from '@/features/runs/api'
import { formatDuration, spanLabel, spanStatusLabel } from '@/features/runs/format'
import { buildSpanRows, spanBar, traceBounds, type SpanRow } from '@/features/runs/timeline'
import { cn } from '@/lib/utils'

interface SpanWaterfallProps {
  spans: readonly SpanOut[]
  /** 当前打开的运行；它的 span 高亮。 */
  highlightRunId: string | null
  onSelect: (row: SpanRow) => void
  titles: ReadonlyMap<string, string>
  /** 后端在 5000 条处截断。 */
  truncated?: boolean
}

const INDENT_PX = 12
const MAX_INDENT_LEVEL = 6

/** 瀑布图：左边标签按深度缩进，右边横条相对整条 trace 定位，并给出时长。 */
export function SpanWaterfall({ spans, highlightRunId, onSelect, titles, truncated = false }: SpanWaterfallProps) {
  const rows = useMemo(() => buildSpanRows(spans), [spans])
  const bounds = useMemo(() => traceBounds(spans), [spans])

  if (spans.length === 0) {
    return (
      <div className="rounded-xl border bg-card px-4 py-8 text-center shadow-card">
        <p className="text-[0.9375rem] font-semibold">这次运行没有追踪记录</p>
        <p className="mt-1 flex items-center justify-center gap-1 text-footnote text-muted-foreground">
          运行追踪关闭，或已超过保留天数
          <InfoHint label="追踪记录说明">在设置里打开「记录运行追踪」，并确认记录没有超过保留天数。</InfoHint>
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {truncated ? <InlineAlert tone="warning">只显示前 5000 条</InlineAlert> : null}
      <ul aria-label="运行追踪" className="divide-y overflow-hidden rounded-xl border bg-card shadow-card">
        {rows.map((row) => {
          const label = spanLabel(row.span, titles)
          const bar = spanBar(row, bounds.start, bounds.end)
          const indent = Math.min(row.depth, MAX_INDENT_LEVEL - 1) * INDENT_PX
          const highlighted = highlightRunId !== null && row.span.run_id === highlightRunId
          return (
            <li key={row.span.span_id}>
              <button
                type="button"
                onClick={() => onSelect(row)}
                aria-label={`${label}，${formatDuration(row.span.duration_ms)}`}
                className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
              >
                <span className="min-w-0 flex-1" style={{ paddingLeft: `${indent}px` }}>
                  <span className={cn('block truncate text-sm', highlighted ? 'font-semibold text-primary-text' : 'text-foreground')}>
                    {row.orphan ? <span aria-hidden="true" className="mr-1 text-muted-foreground">·</span> : null}
                    {label}
                  </span>
                  <span className="block text-footnote text-muted-foreground">
                    {spanStatusLabel(row.span.status)}
                    {row.span.run_id && highlightRunId && row.span.run_id !== highlightRunId ? ' · 子运行' : ''}
                  </span>
                </span>
                <span aria-hidden="true" className="relative h-2 w-28 shrink-0 overflow-hidden rounded-full bg-surface-2 sm:w-44">
                  <span
                    className={cn('absolute inset-y-0 rounded-full', row.span.status === 'error' ? 'bg-danger' : 'bg-primary')}
                    style={{ left: `${bar.left}%`, width: `${bar.width}%` }}
                  />
                </span>
                <span className="w-20 shrink-0 text-right text-footnote text-muted-foreground tabular-nums">
                  {formatDuration(row.span.duration_ms)}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export default SpanWaterfall
