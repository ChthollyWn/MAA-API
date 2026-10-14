import type { ReactNode } from 'react'
import { Badge, StatusDot } from '@/components/ui/badge'
import { InfoHint } from '@/components/ui/info-hint'
import { InlineAlert } from '@/components/ui/inline-alert'
import type { AccountSummaryEntry } from './api'
import { recognizedCountText, recognitionText, staleBadgeText } from './format'

export interface RecognitionStatusProps {
  /** 「干员识别」/「仓库识别」。 */
  label: string
  entry: AccountSummaryEntry
  /** 计数单位：「名」/「种物品」。 */
  unit: string
  /** 计数后的补充说明，例如「只覆盖养成材料页签」。 */
  description?: string
  /** 右侧附加操作或入口。 */
  actions?: ReactNode
}

/**
 * 识别状态条：识别时间、是否过期与规模（规格 §7.5、§7.8 的口径）。
 * 识别由任务触发（§7.10、Q8），不提供刷新识别按钮。
 */
export function RecognitionStatus({ label, entry, unit, description, actions }: RecognitionStatusProps) {
  const badge = staleBadgeText(entry)
  const tone = !entry.recognized ? 'neutral' : entry.stale ? 'warning' : 'success'
  const count = recognizedCountText(entry, unit)

  return (
    <section aria-label={label} className="space-y-2 rounded-xl border bg-card px-3.5 py-2.5 shadow-card">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <StatusDot tone={tone} />
          <span className="text-sm font-semibold text-foreground">{label}</span>
          {badge ? <Badge variant={tone}>{badge}</Badge> : null}
          {description ? <InfoHint label={`${label}说明`}>{description}</InfoHint> : null}
          <span aria-hidden="true" className="hidden text-border-strong sm:inline">·</span>
          <span className="hidden text-footnote tabular-nums text-muted-foreground sm:inline">
            {recognitionText(entry)}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {count ? (
            <span className="rounded-md bg-surface-2 px-2 py-0.5 text-footnote font-medium tabular-nums text-foreground">
              {count}
            </span>
          ) : null}
          {actions}
        </div>
      </div>

      <p className="text-footnote tabular-nums text-muted-foreground sm:hidden">{recognitionText(entry)}</p>

      {entry.stale ? (
        <InlineAlert tone="warning" role="status">
          数据已超过 {entry.stale_after_days} 天，需要时请在任务里运行「{label}」重新识别。
        </InlineAlert>
      ) : null}
    </section>
  )
}
