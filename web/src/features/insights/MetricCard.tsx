import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { MetricCardView, MetricDefinitionView } from './api'
import {
  EVIDENCE_LABELS,
  METRIC_EMPTY_HINTS,
  formatCi,
  formatDelta,
  formatMetricValue,
  formatSubMetricValue,
} from './format'

const EVIDENCE_VARIANT = {
  measured: 'success',
  self_reported: 'warning',
  proxy: 'primary',
  unavailable: 'neutral',
} as const

export interface MetricCardProps {
  card: MetricCardView
  /** 目录里的定义；用来提示样本阈值，缺失时不显示。 */
  definition?: MetricDefinitionView
}

/** 一张指标卡：主值、n 与 Wilson 区间、变化、证据徽章与 secondary（M99 §4.6）。 */
export function MetricCard({ card, definition }: MetricCardProps) {
  const hasValue = card.value !== null
  const ci = card.ci_low !== null && card.ci_high !== null
    ? formatCi(card.ci_low, card.ci_high, card.unit)
    : null
  const belowSample = hasValue && definition !== undefined && card.sample_size < definition.min_sample

  return (
    <article
      aria-label={`${card.label}指标`}
      className="flex min-w-0 flex-col gap-1 rounded-xl border bg-card p-3 shadow-card"
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="min-w-0 truncate text-footnote font-medium text-muted-foreground">{card.label}</h3>
        <Badge variant={EVIDENCE_VARIANT[card.evidence]}>{EVIDENCE_LABELS[card.evidence]}</Badge>
      </div>

      <p className={cn('text-title font-semibold tabular-nums', !hasValue && 'text-muted-foreground')}>
        {formatMetricValue(card.value, card.unit)}
      </p>

      <p className="text-footnote tabular-nums text-muted-foreground">
        n={card.sample_size}
        {ci ? ` · ${ci}` : ''}
      </p>

      {card.delta !== null ? (
        <p className="text-footnote text-muted-foreground">
          较上一窗口 <span className="font-medium tabular-nums text-foreground">{formatDelta(card.delta, card.unit)}</span>
        </p>
      ) : null}

      {belowSample ? (
        <p className="text-footnote text-warning">样本不足，至少 {definition.min_sample} 个</p>
      ) : null}

      {card.secondary.length > 0 ? (
        <dl className="mt-1 space-y-0.5 border-t border-border/70 pt-1.5 text-footnote">
          {card.secondary.map((item, index) => (
            <div key={`${item.label}-${index}`} className="flex items-baseline justify-between gap-2">
              <dt className="min-w-0 truncate text-muted-foreground">{item.label}</dt>
              <dd className="shrink-0 tabular-nums">{formatSubMetricValue(item.value, item.unit)}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {card.sample_size === 0 ? (
        <p className="mt-1 break-words text-footnote text-muted-foreground">{METRIC_EMPTY_HINTS[card.id]}</p>
      ) : null}
    </article>
  )
}
