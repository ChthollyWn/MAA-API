import { ArrowRight } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface VersionChangeProps {
  current: string | null
  latest?: string | null
  unknownLabel?: string
  className?: string
}

/** 「当前 → 最新」：两个版本各自成块，窄屏上在箭头处换行，超长的版本号再按字符断开。 */
export function VersionChange({ current, latest = null, unknownLabel = '未知', className }: VersionChangeProps) {
  const showLatest = latest !== null && latest !== current
  return (
    <p className={cn('flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-sm tabular-nums', className)}>
      <span className={cn('min-w-0 [overflow-wrap:anywhere]', current === null ? 'text-muted-foreground' : 'font-mono')}>{current ?? unknownLabel}</span>
      {showLatest ? (
        <>
          <ArrowRight aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="sr-only">可更新到</span>
          <span className="min-w-0 font-mono font-semibold [overflow-wrap:anywhere]">{latest}</span>
        </>
      ) : null}
    </p>
  )
}
