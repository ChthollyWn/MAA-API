import { Link } from 'react-router'
import { useInfiniteQuery } from '@tanstack/react-query'
import { History, RefreshCw } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ListGroup, ListItem } from '@/components/ui/list'
import { Section } from '@/components/ui/section'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState, ErrorState, Spinner } from '@/components/ui/states'
import { errorMessage } from '../form'
import {
  formatTime,
  normalizeStatus,
  pipelineElapsed,
  pipelineIdOf,
  statusLabel,
  statusTone,
  type PipelineRow,
} from '../pipeline-format'
import { usePipelineTitle } from '../pipeline-queries'

export const HISTORY_PAGE_SIZE = 20

type HistoryPage = { items: PipelineRow[]; total: number; page: number; size: number }

function rowMeta(row: PipelineRow): string {
  const started = row.started_at
  const elapsed = pipelineElapsed(row)
  const count = typeof row.task_count === 'number' ? `${row.task_count} 个任务` : null
  return [
    started ? formatTime(started) : `${formatTime(row.created_at)} 入队`,
    elapsed ? (elapsed.running ? `已运行 ${elapsed.text}` : elapsed.text) : null,
    count,
  ].filter(Boolean).join(' · ')
}

/** 任务 › 历史：最新的在前，底部「加载更多」按 page / size / total 翻页，点行进入详情。 */
export function HistoryView() {
  const history = useInfiniteQuery({
    queryKey: keys.pipelines.list({ size: HISTORY_PAGE_SIZE }),
    queryFn: async ({ pageParam }) => {
      const { data, error, response } = await api.GET('/api/pipelines', { params: { query: { page: pageParam, size: HISTORY_PAGE_SIZE } } })
      if (error) throw toApiError(error, response)
      return data as HistoryPage
    },
    initialPageParam: 1,
    getNextPageParam: (last) => (last.page * last.size < last.total ? last.page + 1 : undefined),
    retry: false,
  })
  const seen = new Set<string>()
  const rows = (history.data?.pages ?? []).flatMap((page) => page.items ?? []).filter((row) => {
    const id = pipelineIdOf(row)
    if (!id || seen.has(id)) return false
    seen.add(id)
    return true
  })
  const total = history.data?.pages.at(-1)?.total ?? rows.length
  const titleOf = usePipelineTitle(rows)

  if (history.isPending) {
    return (
      <div role="status" aria-label="正在读取执行历史" className="divide-y overflow-hidden rounded-xl border bg-card shadow-card">
        {[0, 1, 2].map((index) => (
          <div key={index} className="space-y-2 px-4 py-4"><Skeleton className="h-4 w-40" /><Skeleton className="h-3 w-56" /></div>
        ))}
      </div>
    )
  }
  if (history.error && rows.length === 0) {
    return (
      <div className="rounded-xl border bg-card shadow-card">
        <ErrorState message={`无法读取执行历史：${errorMessage(history.error)}`} onRetry={() => void history.refetch()} retrying={history.isFetching} />
      </div>
    )
  }
  if (rows.length === 0) {
    return (
      <div className="rounded-xl border bg-card shadow-card">
        <EmptyState
          icon={History}
          title="还没有执行记录"
          description="执行过的流水线会出现在这里。"
          action={<Button asChild variant="outline"><Link to="/tasks">新建任务</Link></Button>}
        />
      </div>
    )
  }

  return (
    <Section
      id="history-list"
      title="最近执行"
      description={`共 ${total} 条`}
      className="pb-4"
      actions={(
        <Button variant="ghost" size="icon-sm" aria-label="刷新历史" onClick={() => void history.refetch()}>
          {history.isRefetching && !history.isFetchingNextPage ? <Spinner /> : <RefreshCw aria-hidden="true" />}
        </Button>
      )}
    >
      {history.error ? <InlineAlert tone="warning">历史更新失败：{errorMessage(history.error)}</InlineAlert> : null}
      <ListGroup>
        {rows.map((row) => {
          const id = pipelineIdOf(row)
          const status = normalizeStatus(row.status)
          return (
            <ListItem
              key={id}
              to={`/tasks/history/${encodeURIComponent(id)}`}
              title={titleOf(row)}
              description={<span className="tabular-nums">{rowMeta(row)}</span>}
              trailing={<Badge variant={statusTone(status)}>{statusLabel(status)}</Badge>}
            />
          )
        })}
      </ListGroup>
      {history.hasNextPage ? (
        <Button variant="outline" className="w-full" loading={history.isFetchingNextPage} onClick={() => void history.fetchNextPage()}>
          {history.isFetchingNextPage ? '正在加载…' : '加载更多'}
        </Button>
      ) : (
        <p className="py-1 text-center text-footnote text-muted-foreground">已显示全部 {total} 条</p>
      )}
    </Section>
  )
}
