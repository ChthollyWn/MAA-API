import { Badge } from '@/components/ui/badge'
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
}

/**
 * 识别状态条：识别时间、是否过期与规模（规格 §7.5、§7.8 的口径）。
 * 这里刻意没有「刷新识别」按钮——识别由任务触发（§7.10、Q8）。
 */
export function RecognitionStatus({ label, entry, unit, description }: RecognitionStatusProps) {
  const badge = staleBadgeText(entry)
  const tone = !entry.recognized ? 'neutral' : entry.stale ? 'warning' : 'success'
  const count = recognizedCountText(entry, unit)

  return (
    <section aria-label={label} className="space-y-1 px-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-footnote text-muted-foreground">{label}</span>
        {badge ? <Badge variant={tone}>{badge}</Badge> : null}
      </div>
      <p className="text-[0.9375rem] font-medium tabular-nums">{recognitionText(entry)}</p>
      {count ? <p className="text-footnote tabular-nums text-muted-foreground">{count}</p> : null}
      {entry.stale ? (
        <InlineAlert tone="warning" role="status">
          数据已超过 {entry.stale_after_days} 天，需要时请在任务里运行「{label}」重新识别。
        </InlineAlert>
      ) : null}
      {description ? <p className="text-footnote text-muted-foreground">{description}</p> : null}
    </section>
  )
}
