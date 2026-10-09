import { useEffect, useRef } from 'react'
import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Section } from '@/components/ui/section'
import { LoadingState } from '@/components/ui/states'
import { formatFullTime, formatLogTime, isoTime, logText, normalizeLevel } from '@/features/logs/format'
import { cn } from '@/lib/utils'
import type { LogRecord } from '@/realtime/events'
import { errorMessage } from '../form'

export const PIPELINE_LOG_LIMIT = 100

type LogPage = { items: LogRecord[]; total: number; page: number; size: number }

const levelTone: Partial<Record<string, string>> = {
  WARNING: 'text-warning',
  ERROR: 'text-danger',
  CRITICAL: 'text-danger',
}

/** 流水线的最近 100 条日志，按时间顺序排成紧凑列表；完整日志去日志页看。 */
export function PipelineLogs({ pipelineId, active }: { pipelineId: string; active: boolean }) {
  const logs = useQuery({
    queryKey: [...keys.pipelines.detail(pipelineId), 'logs'],
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/pipelines/{pipeline_id}/logs', {
        params: { path: { pipeline_id: pipelineId }, query: { order: 'desc', size: PIPELINE_LOG_LIMIT } },
      })
      if (error) throw toApiError(error, response)
      return data as LogPage
    },
    refetchInterval: active ? 5_000 : false,
    retry: false,
  })
  const items = [...(logs.data?.items ?? [])].reverse()
  const total = logs.data?.total ?? 0
  const listRef = useRef<HTMLOListElement>(null)
  const pinnedRef = useRef(true)
  const newestId = items.at(-1)?.id

  // 最新一条在最下面：初次显示与刷新时停在底部，用户往上翻之后不再抢滚动位置。
  useEffect(() => {
    const node = listRef.current
    if (node && pinnedRef.current) node.scrollTop = node.scrollHeight
  }, [newestId])

  return (
    <Section
      id="pipeline-logs"
      title="日志"
      description={total > items.length ? `最近 ${items.length} 条，共 ${total} 条` : undefined}
      actions={<Button asChild variant="ghost" size="sm" className="text-primary-text"><Link to="/logs/task">在日志页查看</Link></Button>}
    >
      {logs.isPending ? (
        <div className="rounded-xl border bg-card shadow-card"><LoadingState label="正在读取日志…" /></div>
      ) : logs.error ? (
        <InlineAlert tone="danger" action={<Button variant="outline" size="sm" onClick={() => void logs.refetch()}>重试</Button>}>
          日志读取失败：{errorMessage(logs.error)}
        </InlineAlert>
      ) : items.length === 0 ? (
        <p className="rounded-xl border bg-card px-4 py-3.5 text-footnote text-muted-foreground shadow-card">还没有日志</p>
      ) : (
        <ol
          ref={listRef}
          aria-label="流水线日志"
          onScroll={(event) => {
            const node = event.currentTarget
            pinnedRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 24
          }}
          className="max-h-[28rem] divide-y overflow-y-auto overscroll-contain rounded-xl border bg-card shadow-card"
        >
          {items.map((record) => {
            const level = normalizeLevel(record.level)
            return (
              <li key={record.id} className="flex gap-3 px-4 py-2 text-footnote leading-5">
                <time dateTime={isoTime(record.ts)} title={formatFullTime(record.ts)} className="shrink-0 font-mono tabular-nums text-muted-foreground">{formatLogTime(record.ts)}</time>
                <span className={cn('min-w-0 flex-1 whitespace-pre-wrap break-words', levelTone[level])}>
                  {levelTone[level] ? <span className="mr-1.5 font-mono text-xs font-semibold">{level}</span> : null}
                  {logText(record)}
                </span>
              </li>
            )
          })}
        </ol>
      )}
    </Section>
  )
}
