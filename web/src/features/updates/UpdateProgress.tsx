import { Fragment, useId, type ReactNode } from 'react'
import { CircleStop } from 'lucide-react'
import { StatusDot } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import type { UpdateRecord } from '@/features/updates/types'
import {
  asNumber, asText, formatBytes, formatDuration, phaseLabel, recordCanCancel, recordIsActive, targetLabels, withLeadingSpace,
} from '@/features/updates/types'
import { cn } from '@/lib/utils'

interface UpdateProgressProps {
  record: UpdateRecord
  busy?: boolean
  onCancel: (id: string) => void
  /** 进度下方的补充提示，例如读取失败或取消失败。 */
  children?: ReactNode
  className?: string
}

function byteProgress(downloaded: number | null, total: number | null): string | null {
  const done = formatBytes(downloaded)
  const all = formatBytes(total)
  if (done && all) return `${done} / ${all}`
  if (done) return done
  return all ? `共 ${all}` : null
}

/** 正在进行的更新：阶段、百分比、已下载 / 总量、速度与剩余时间；只有下载阶段可以取消。 */
export function UpdateProgress({ record, busy = false, onCancel, children, className }: UpdateProgressProps) {
  const headingId = useId()
  const phase = asText(record.phase)
  const rawPercent = asNumber(record.percent) ?? asNumber(record.progress)
  const percent = rawPercent === null ? null : Math.max(0, Math.min(100, rawPercent))
  const downloading = phase?.toLowerCase() === 'downloading'
  const applying = phase?.toLowerCase() === 'applying'
  const active = recordIsActive(record)
  const canCancel = recordCanCancel(record)
  const targetVersion = asText(record.target_version) ?? asText(record.to_version)
  const speed = formatBytes(asNumber(record.bytes_per_second) ?? asNumber(record.speed))
  const eta = formatDuration(record.eta_seconds ?? record.eta)
  const details = downloading
    ? [
        byteProgress(
          asNumber(record.bytes_downloaded) ?? asNumber(record.bytes_done) ?? asNumber(record.downloaded),
          asNumber(record.bytes_total) ?? asNumber(record.total),
        ),
        speed ? `${speed}/秒` : null,
        eta ? `剩余约 ${eta}` : null,
      ].filter((item): item is string => item !== null)
    : []
  const target = targetLabels[record.target] as string | undefined

  return (
    <section aria-labelledby={headingId} className={cn('rounded-xl border bg-card p-4 shadow-card', className)}>
      <p className="flex items-center gap-2 text-footnote text-muted-foreground">
        {active ? <StatusDot tone="primary" pulse /> : null}
        当前更新
      </p>
      <h2 id={headingId} className="mt-0.5 text-headline font-semibold">正在更新{target ? withLeadingSpace(target) : ''}</h2>
      {targetVersion ? (
        <p className="mt-0.5 text-footnote text-muted-foreground">
          目标版本 <span className="font-mono tabular-nums [overflow-wrap:anywhere]">{targetVersion}</span>
        </p>
      ) : null}

      <div className="mt-4 flex items-baseline justify-between gap-3">
        <p className="text-[0.9375rem] font-medium">{phaseLabel(phase ?? record.status)}</p>
        {downloading && percent !== null ? <span className="text-sm font-semibold tabular-nums">{percent.toFixed(0)}%</span> : null}
      </div>
      {active ? (
        <Progress value={downloading ? percent : null} label="更新进度" className="mt-2 h-2" />
      ) : null}
      {details.length ? (
        <p className="mt-2 flex flex-wrap gap-x-1.5 text-footnote text-muted-foreground tabular-nums">
          {details.map((item, index) => (
            <Fragment key={item}>
              {index > 0 ? <span aria-hidden="true">·</span> : null}
              <span>{item}</span>
            </Fragment>
          ))}
        </p>
      ) : null}

      {active ? (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
          <p className="min-w-0 flex-1 text-footnote text-muted-foreground">
            {applying ? '正在应用更新，这一步无法取消。' : canCancel ? '下载阶段可以取消，已下载的部分会被丢弃。' : '只有下载阶段可以取消。'}
          </p>
          <Button variant="destructive-soft" size="sm" loading={busy} disabled={!canCancel} onClick={() => onCancel(record.id)}>
            {busy ? null : <CircleStop aria-hidden="true" />}
            {busy ? '正在取消…' : '取消更新'}
          </Button>
        </div>
      ) : null}
      {children ? <div className="mt-3 space-y-2">{children}</div> : null}
    </section>
  )
}
