import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { UpdateRecord } from '@/features/updates/types'
import {
  asNumber, asText, formatBytes, formatDuration, phaseLabel, recordCanCancel, recordIsActive,
} from '@/features/updates/types'

interface UpdateProgressProps {
  record: UpdateRecord
  busy?: boolean
  onCancel: (id: string) => void
}

export function UpdateProgress({ record, busy = false, onCancel }: UpdateProgressProps) {
  const phase = asText(record.phase)
  const percent = asNumber(record.percent) ?? asNumber(record.progress)
  const downloaded = asNumber(record.bytes_downloaded) ?? asNumber(record.bytes_done) ?? asNumber(record.downloaded)
  const total = asNumber(record.bytes_total) ?? asNumber(record.total)
  const speed = asNumber(record.bytes_per_second) ?? asNumber(record.speed)
  const eta = formatDuration(record.eta_seconds ?? record.eta)
  const isDownloading = phase === 'downloading'
  const canCancel = recordCanCancel(record)
  const active = recordIsActive(record)

  return (
    <section aria-label="更新进度" className="mt-4 rounded-lg border bg-muted/40 p-3 sm:p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge variant={record.status.toLowerCase() === 'failed' ? 'destructive' : 'secondary'}>
            {phaseLabel(phase ?? record.status)}
          </Badge>
          {(record.target_version ?? record.to_version) && <span className="text-sm text-muted-foreground">目标版本 {record.target_version ?? record.to_version}</span>}
        </div>
        {!active && <span className="text-sm font-medium">{phaseLabel(phase ?? record.status)}</span>}
      </div>

      {isDownloading && (
        <div className="mt-3 space-y-2">
          <div className="flex items-center justify-between gap-3 text-sm">
            <span>{percent === null ? '下载中' : `${Math.max(0, Math.min(100, percent)).toFixed(0)}%`}</span>
            <span className="text-right text-muted-foreground">
              {downloaded !== null && formatBytes(downloaded)}
              {total !== null ? ` / ${formatBytes(total)}` : ''}
              {speed !== null && ` · ${formatBytes(speed)}/秒`}
              {eta && ` · 约 ${eta}`}
            </span>
          </div>
          <progress
            aria-label="下载进度"
            className="h-2 w-full accent-primary"
            max={100}
            value={percent === null ? undefined : Math.max(0, Math.min(100, percent))}
          />
        </div>
      )}

      {phase === 'applying' && (
        <p className="mt-2 text-sm text-muted-foreground">正在应用更新，无法取消。</p>
      )}

      {active && (
        <div className="mt-3 flex justify-end">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!canCancel || busy}
            onClick={() => onCancel(record.id)}
          >
            {busy ? '正在取消…' : '取消更新'}
          </Button>
        </div>
      )}
    </section>
  )
}
