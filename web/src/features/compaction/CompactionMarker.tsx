import { Badge } from '@/components/ui/badge'
import { Disclosure } from '@/components/ui/disclosure'
import { InfoHint } from '@/components/ui/info-hint'
import type { CheckpointView } from './api'
import { formatTokens } from './format'

/**
 * 压缩标记（M74 §4.6）：一条分隔行「已压缩较早的消息（约 {before} → {after} token）」，
 * 摘要收在「查看摘要」里按原文换行展开；机械摘要额外标「按规则生成」。
 */
export function CompactionMarker({ checkpoint }: { checkpoint: CheckpointView }) {
  const label = `已压缩较早的消息（约 ${formatTokens(checkpoint.tokens_before)} → ${formatTokens(checkpoint.tokens_after)} token）`
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2 text-footnote text-muted-foreground">
        <span aria-hidden="true" className="h-px flex-1 bg-border" />
        <span className="text-center">{label}</span>
        {checkpoint.degraded ? (
          <>
            <Badge variant="warning">按规则生成</Badge>
            <InfoHint label="按规则生成说明">
              摘要模型不可用时按规则生成，内容比较简略。
            </InfoHint>
          </>
        ) : null}
        <span aria-hidden="true" className="h-px flex-1 bg-border" />
      </div>
      <Disclosure variant="plain" title="查看摘要">
        <p className="whitespace-pre-wrap break-words text-footnote text-muted-foreground">
          {checkpoint.summary}
        </p>
      </Disclosure>
    </div>
  )
}
