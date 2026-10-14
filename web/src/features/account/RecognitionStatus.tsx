import type { ReactNode } from 'react'
import { Badge, StatusDot } from '@/components/ui/badge'
import { InfoHint } from '@/components/ui/info-hint'
import { InlineAlert } from '@/components/ui/inline-alert'
import type { AccountSummaryEntry } from './api'
import { recognizedCountText, recognitionText, staleBadgeText } from './format'

export interface RecognitionStatusProps {
  /** 「森空岛练度同步」/「仓库识别」。 */
  label: string
  entry: AccountSummaryEntry
  /** 计数单位：「名」/「种物品」。 */
  unit: string
  /** 计数后的补充说明，例如「干员数据只来自森空岛同步」。 */
  description?: string
  /** 右侧附加操作或入口。 */
  actions?: ReactNode
  /**
   * 过期提示的模板，`{days}` 会换成阈值天数。
   *
   * 两个调用方给的「怎么办」不一样（干员去森空岛同步、仓库去跑识别任务），所以这句话
   * 由调用方给，组件只负责拼天数（M47）。
   */
  staleHint?: string
  /** 时间行的前缀词：「识别于」/「同步于」。 */
  recognizedPrefix?: string
}

/**
 * 数据来源状态条：上次写入时间、是否过期与规模（规格 §7.5、§7.8 的口径）。
 * 写入由任务或森空岛同步触发，所以这里不提供刷新按钮——列表自己的「刷新」只重取数据。
 */
export function RecognitionStatus({
  label,
  entry,
  unit,
  description,
  actions,
  staleHint,
  recognizedPrefix,
}: RecognitionStatusProps) {
  const badge = staleBadgeText(entry)
  const tone = !entry.recognized ? 'neutral' : entry.stale ? 'warning' : 'success'
  const count = recognizedCountText(entry, unit)
  const timeText = recognitionText(entry, recognizedPrefix)

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
            {timeText}
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

      <p className="text-footnote tabular-nums text-muted-foreground sm:hidden">{timeText}</p>

      {entry.stale ? (
        <InlineAlert tone="warning" role="status">
          {staleHint
            ? staleHint.replace('{days}', String(entry.stale_after_days))
            : `数据已超过 ${entry.stale_after_days} 天，需要时请重新写入一次数据。`}
        </InlineAlert>
      ) : null}
    </section>
  )
}
