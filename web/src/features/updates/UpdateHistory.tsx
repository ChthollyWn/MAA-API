import { ChevronRight, History, RefreshCw } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import type { UpdateRecord } from '@/features/updates/types'
import { asText, errorMessage, formatDate, recordIsFailed, recordStatus, recordVersion, targetLabels } from '@/features/updates/types'
import { cn } from '@/lib/utils'

interface UpdateHistoryProps {
  records: UpdateRecord[]
  total?: number
  isLoading: boolean
  isFetching: boolean
  error: unknown
  onRefresh: () => void
  onSelect: (id: string) => void
}

/** 最近的更新记录：状态、目标、版本与时间；点开看详情，失败的可以在详情里重试。 */
export function UpdateHistory({ records, total, isLoading, isFetching, error, onRefresh, onSelect }: UpdateHistoryProps) {
  return (
    <Section
      id="update-history"
      title="最近更新记录"
      hint="检查版本不会产生记录。"
      actions={(
        <Button variant="ghost" size="icon" aria-label="刷新更新记录" disabled={isFetching} onClick={onRefresh}>
          <RefreshCw aria-hidden="true" className={cn(isFetching && 'animate-spin')} />
        </Button>
      )}
    >
      {error && !records.length ? (
        <div className="rounded-xl border bg-card shadow-card">
          <ErrorState message={`读取更新记录失败：${errorMessage(error)}`} onRetry={onRefresh} retrying={isFetching} />
        </div>
      ) : isLoading ? (
        <div className="rounded-xl border bg-card shadow-card"><LoadingState label="正在读取更新记录…" /></div>
      ) : records.length === 0 ? (
        <div className="rounded-xl border bg-card shadow-card">
          <EmptyState icon={History} title="还没有更新记录" description="开始一次更新后，结果会记录在这里" />
        </div>
      ) : (
        <ul aria-label="最近更新记录" className="divide-y overflow-hidden rounded-xl border bg-card shadow-card">
          {records.map((record) => {
            const status = recordStatus(record.status)
            const version = recordVersion(record)
            const failure = recordIsFailed(record) ? asText(record.error_message) ?? asText(record.error_code) : null
            return (
              <li key={record.id}>
                <button
                  type="button"
                  onClick={() => onSelect(record.id)}
                  className="flex min-h-14 w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                >
                  <span className="min-w-0 flex-1 space-y-0.5">
                    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="text-[0.9375rem] font-medium">{targetLabels[record.target] ?? record.target}</span>
                      <Badge variant={status.tone}>{status.label}</Badge>
                    </span>
                    <span className="flex flex-wrap gap-x-1.5 text-footnote text-muted-foreground tabular-nums">
                      {version ? <span className="[overflow-wrap:anywhere]">{version}</span> : null}
                      {version ? <span aria-hidden="true">·</span> : null}
                      <span>{formatDate(record.created_at ?? record.started_at)}</span>
                    </span>
                    {failure ? <span className="block truncate text-footnote text-danger">{failure}</span> : null}
                  </span>
                  <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground/70" />
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {error && records.length ? (
        <p role="alert" className="px-1 text-footnote text-danger">刷新记录失败：{errorMessage(error)}</p>
      ) : null}
      {total !== undefined && total > records.length ? (
        <p className="px-1 text-footnote text-muted-foreground tabular-nums">显示最近 {records.length} 条 / 共 {total} 条</p>
      ) : null}
    </Section>
  )
}
