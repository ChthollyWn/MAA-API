import { memo, useEffect, useState } from 'react'
import { Check, Copy, Expand } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  copyableLine,
  formatFullTime,
  formatLogTime,
  isoTime,
  logImage,
  logLogger,
  logText,
  normalizeLevel,
  sourceLabel,
  type LogImage,
} from '@/features/logs/format'
import { copyText } from '@/lib/clipboard'
import { cn } from '@/lib/utils'
import type { LogRecord } from '@/realtime/events'

interface LevelTone {
  text: string
  bar: string
}

const INFO_TONE: LevelTone = { text: 'text-primary-text', bar: 'bg-primary/60' }

const levelTone: Partial<Record<string, LevelTone>> = {
  DEBUG: { text: 'text-muted-foreground', bar: 'bg-border-strong' },
  INFO: INFO_TONE,
  WARNING: { text: 'text-warning', bar: 'bg-warning' },
  ERROR: { text: 'text-danger', bar: 'bg-danger' },
  CRITICAL: { text: 'text-danger', bar: 'bg-danger' },
}

type CopyState = 'idle' | 'copied' | 'failed'

const copyLabel: Record<CopyState, string> = { idle: '复制', copied: '已复制', failed: '复制失败' }

export interface LogEntryProps {
  record: LogRecord
  expanded: boolean
  onToggle: (id: number) => void
  onViewImage: (image: LogImage, record: LogRecord) => void
}

/** 一行日志：点正文展开全文与记录器，展开后可复制；截图缩略图点开看原图。 */
export const LogEntry = memo(function LogEntry({ record, expanded, onToggle, onViewImage }: LogEntryProps) {
  const [copyState, setCopyState] = useState<CopyState>('idle')
  const level = normalizeLevel(record.level)
  const tone = levelTone[level] ?? INFO_TONE
  const content = typeof record.content === 'string' ? record.content : ''
  const placeholderText = !content || content.startsWith('data:image/')
  const logger = logLogger(record)
  const image = logImage(record)

  useEffect(() => {
    if (copyState === 'idle') return
    const timer = window.setTimeout(() => setCopyState('idle'), 2_000)
    return () => window.clearTimeout(timer)
  }, [copyState])

  const copy = async () => setCopyState((await copyText(copyableLine(record))) ? 'copied' : 'failed')

  return (
    <article className="relative py-2.5 pl-5 pr-3 sm:pr-4">
      <span aria-hidden="true" className={cn('absolute bottom-2.5 left-2 top-2.5 w-[3px] rounded-full', tone.bar)} />
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => onToggle(record.id)}
        className="block w-full min-w-0 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="flex min-w-0 items-center gap-2 text-xs leading-5">
          <time dateTime={isoTime(record.ts)} className="shrink-0 font-mono tabular-nums text-muted-foreground">{formatLogTime(record.ts)}</time>
          <span className={cn('shrink-0 font-mono font-semibold', tone.text)}>{level}</span>
          <span className="min-w-0 truncate text-muted-foreground">{sourceLabel(record.source)}</span>
        </span>
        <span
          className={cn(
            'mt-0.5 whitespace-pre-wrap break-words font-mono text-footnote leading-5',
            placeholderText && 'text-muted-foreground',
            expanded ? 'block' : 'line-clamp-2',
          )}
        >
          {logText(record)}
        </span>
      </button>
      {image ? (
        <button
          type="button"
          aria-label={`查看日志 ${record.id} 的截图`}
          onClick={() => onViewImage(image, record)}
          className="group relative mt-2 block overflow-hidden rounded-lg border bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <img src={image.thumb} alt="日志截图缩略图" loading="lazy" decoding="async" className="aspect-video w-40 object-cover sm:w-48" />
          <span aria-hidden="true" className="absolute bottom-1.5 right-1.5 grid size-6 place-items-center rounded-full bg-black/50 text-white opacity-85 transition-opacity group-hover:opacity-100">
            <Expand className="size-3.5" />
          </span>
        </button>
      ) : null}
      {expanded ? (
        <div className="mt-2 flex flex-wrap items-end justify-between gap-x-3 gap-y-2">
          <dl className="min-w-0 space-y-0.5 text-xs leading-5 text-muted-foreground">
            <div className="flex gap-2">
              <dt className="shrink-0">时间</dt>
              <dd className="font-mono tabular-nums">{formatFullTime(record.ts)}</dd>
            </div>
            {logger ? (
              <div className="flex gap-2">
                <dt className="shrink-0">记录器</dt>
                <dd className="min-w-0 break-all font-mono">{logger}</dd>
              </div>
            ) : null}
          </dl>
          <Button variant="outline" size="sm" onClick={() => void copy()}>
            {copyState === 'copied' ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
            {copyLabel[copyState]}
          </Button>
        </div>
      ) : null}
    </article>
  )
})
