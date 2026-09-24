import { useState } from 'react'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { LogRecord } from '@/realtime/events'

const levelStyles: Record<string, string> = {
  CRITICAL: 'text-negative',
  ERROR: 'text-negative',
  WARNING: 'text-warning',
  INFO: 'text-foreground',
  DEBUG: 'text-muted-foreground',
}

const levelRank: Record<string, number> = {
  DEBUG: 10,
  INFO: 20,
  WARNING: 30,
  WARN: 30,
  ERROR: 40,
  CRITICAL: 50,
}

export function logLevelRank(level: unknown): number {
  return levelRank[String(level ?? 'INFO').toUpperCase()] ?? 20
}

function attachmentUrls(record: LogRecord): { thumb: string; full: string } | null {
  const attachment = record.attachment
  if (!attachment || typeof attachment !== 'object') return null
  const data = attachment as Record<string, unknown>
  const hash = typeof data.sha256 === 'string' && /^[a-f\d]{64}$/i.test(data.sha256)
    ? data.sha256
    : null
  const normalize = (candidate: unknown, suffix: 'thumb' | 'full') => {
    if (typeof candidate !== 'string') return null
    const match = candidate.match(/^\/api\/images\/([a-f\d]{64})\/(thumb|full)$/i)
    return match ? `/api/images/${match[1]}/${suffix}` : null
  }
  const thumb = normalize(data.thumb_url, 'thumb') ?? (hash ? `/api/images/${hash}/thumb` : null)
  const full = normalize(data.full_url, 'full') ?? (hash ? `/api/images/${hash}/full` : null)
  return thumb && full ? { thumb, full } : null
}

function formatTimestamp(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '时间未知'
  return new Date(value * 1000).toLocaleString()
}

export function LogEntry({ record }: { record: LogRecord }) {
  const [showFullImage, setShowFullImage] = useState(false)
  const level = String(record.level ?? 'INFO').toUpperCase()
  const source = String(record.source ?? 'unknown')
  const content = typeof record.content === 'string' ? record.content : ''
  const image = attachmentUrls(record)

  return (
    <article className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 border-b px-3 py-3 text-sm sm:px-4">
      <time className="col-span-2 font-mono text-[11px] text-muted-foreground" dateTime={typeof record.ts === 'number' ? new Date(record.ts * 1000).toISOString() : undefined}>
        {formatTimestamp(record.ts)}
      </time>
      <div className="flex items-start gap-2 pt-0.5">
        <span className={`min-w-16 font-mono text-xs font-semibold ${levelStyles[level] ?? 'text-foreground'}`}>{level}</span>
        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase text-muted-foreground">{source}</span>
      </div>
      <div className="min-w-0 break-words">
        {content.startsWith('data:image/') ? (
          <p className="text-xs text-muted-foreground">内嵌图片内容已省略</p>
        ) : (
          <p className="whitespace-pre-wrap break-words font-mono text-xs leading-5">{content || '（空日志）'}</p>
        )}
        {image ? (
          <button
            className="mt-2 block max-w-full rounded-md border bg-muted p-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            type="button"
            aria-label={`查看日志 ${record.id} 的截图`}
            onClick={() => setShowFullImage(true)}
          >
            <img
              className="max-h-44 max-w-full rounded object-contain"
              src={image.thumb}
              loading="lazy"
              decoding="async"
              alt="日志截图缩略图"
            />
          </button>
        ) : null}
        {typeof record.logger === 'string' && record.logger ? (
          <p className="mt-1 break-all text-[10px] text-muted-foreground">{record.logger}</p>
        ) : null}
      </div>
      {showFullImage && image ? (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/80 p-4" role="dialog" aria-modal="true" aria-label="日志截图原图">
          <Button className="absolute right-3 top-3 bg-background text-foreground" variant="outline" size="icon" aria-label="关闭截图" onClick={() => setShowFullImage(false)}>
            <X aria-hidden="true" />
          </Button>
          <img className="max-h-[90dvh] max-w-full object-contain" src={image.full} alt="日志截图原图" />
        </div>
      ) : null}
    </article>
  )
}
