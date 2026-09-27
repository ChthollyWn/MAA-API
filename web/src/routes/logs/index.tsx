import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ScrollText, SearchX } from 'lucide-react'
import { useLocation, useNavigate } from 'react-router'
import { Page } from '@/components/layout/Page'
import { StatusDot, type StatusTone } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { EmptyState, ErrorState } from '@/components/ui/states'
import { LogFilterSheet } from '@/features/logs/LogFilterSheet'
import { LogList } from '@/features/logs/LogList'
import { LogToolbar } from '@/features/logs/LogToolbar'
import {
  NO_FILTERS,
  asEpoch,
  filterConditions,
  removeCondition,
  sourceFromPath,
  sourcePath,
  visibleRecords,
  type LogFilters,
  type LogSourceFilter,
} from '@/features/logs/filters'
import { useDebouncedValue } from '@/features/logs/use-debounced-value'
import { useLogHistory } from '@/features/logs/use-log-history'
import { LAST_SEEN_ID_STORAGE_KEY, realtimeClient, type RealtimeConnectionState } from '@/realtime/client'
import { useRealtimeStatus } from '@/realtime/RealtimeProvider'
import { useLogBuffer } from '@/stores/log-buffer'

const KEYWORD_QUERY_DELAY_MS = 300
/** 外壳 main 的底部留白是 --nav-offset + 1.5rem；页面刚好占满其余高度，只让列表滚动。 */
const FULL_HEIGHT_CLASS = 'h-[calc(100dvh_-_var(--nav-offset)_-_1.5rem)] min-h-[28rem]'

const connectionText: Record<RealtimeConnectionState, { label: string; tone: StatusTone }> = {
  CONNECTED: { label: '实时', tone: 'success' },
  CONNECTING: { label: '连接中', tone: 'warning' },
  RECONNECTING: { label: '重连中', tone: 'warning' },
  OFFLINE: { label: '离线', tone: 'danger' },
}

function readLastSeenId(): number {
  try {
    const value = Number(window.localStorage.getItem(LAST_SEEN_ID_STORAGE_KEY) ?? 0)
    return Number.isSafeInteger(value) && value >= 0 ? value : 0
  } catch {
    return 0
  }
}

export default function LogsPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const source = sourceFromPath(location.pathname)
  const realtime = useRealtimeStatus()
  const bySource = useLogBuffer((state) => state.bySource)
  const [filters, setFilters] = useState<LogFilters>(NO_FILTERS)
  const [keyword, setKeyword] = useState('')
  const [filtersOpen, setFiltersOpen] = useState(false)
  const queryKeyword = useDebouncedValue(keyword, KEYWORD_QUERY_DELAY_MS)
  const lastSeenIdRef = useRef<number | null>(null)
  if (lastSeenIdRef.current === null) lastSeenIdRef.current = readLastSeenId()

  useEffect(() => {
    realtimeClient.setLogSubscription(true)
    return () => realtimeClient.setLogSubscription(false)
  }, [])

  const { levels, range } = filters
  const since = asEpoch(range.since)
  const until = asEpoch(range.until)
  const historyFilters = useMemo(
    () => ({ levels, keyword: queryKeyword, since, until }),
    [levels, queryKeyword, since, until],
  )
  const history = useLogHistory(historyFilters, lastSeenIdRef.current)
  const liveRecords = useMemo(() => Object.values(bySource).flat(), [bySource])
  const records = useMemo(
    () => visibleRecords(history.records, liveRecords, { source, levels, keyword, since, until }),
    [history.records, keyword, levels, liveRecords, since, source, until],
  )
  const conditions = useMemo(() => filterConditions(filters), [filters])
  const narrowed = conditions.length > 0 || keyword.trim() !== ''
  // 筛选或来源一变就重建列表：回到最新一条，不把筛选结果的变化误算成「新日志」。
  const viewKey = JSON.stringify([source, levels, keyword.trim(), range.since, range.until])

  const changeSource = (next: LogSourceFilter) => navigate(sourcePath(next))
  const clearFilters = useCallback(() => setFilters(NO_FILTERS), [])
  const connection = connectionText[realtime.status]

  return (
    <Page
      title="日志"
      width="wide"
      className={FULL_HEIGHT_CLASS}
      contentClassName="flex min-h-0 flex-col gap-3 pt-3"
      actions={(
        <span className="mr-2 flex items-center gap-1.5 whitespace-nowrap text-footnote text-muted-foreground tabular-nums">
          <StatusDot tone={connection.tone} />
          <span>{connection.label}</span>
          <span aria-hidden="true">·</span>
          <span>{records.length.toLocaleString()} 条</span>
        </span>
      )}
      toolbar={(
        <LogToolbar
          source={source}
          onSourceChange={changeSource}
          keyword={keyword}
          onKeywordChange={setKeyword}
          conditions={conditions}
          onOpenFilters={() => setFiltersOpen(true)}
          onRemoveCondition={(id) => setFilters((current) => removeCondition(current, id))}
          onClearConditions={clearFilters}
        />
      )}
    >
      {history.error && records.length > 0 ? (
        <InlineAlert tone="danger" action={<Button variant="outline" size="sm" onClick={history.retry}>重试</Button>}>
          历史日志加载失败：{history.error}
        </InlineAlert>
      ) : null}

      {realtime.truncated && !history.backfillResolved ? (
        <InlineAlert
          tone="warning"
          action={(
            <Button
              variant="outline"
              size="sm"
              loading={history.backfillLoading}
              onClick={() => void history.loadTruncatedHistory(liveRecords)}
            >
              {history.backfillLoading ? '正在补齐' : '加载缺失历史日志'}
            </Button>
          )}
        >
          断线期间的日志超出实时缓冲，可能存在未加载的历史日志。
        </InlineAlert>
      ) : realtime.truncated && history.backfillResolved ? (
        <InlineAlert tone="success">已从历史记录补齐 {history.recoveredCount.toLocaleString()} 条缺失日志</InlineAlert>
      ) : null}
      {history.backfillError ? <InlineAlert tone="danger">历史补齐失败：{history.backfillError}</InlineAlert> : null}

      {realtime.status === 'OFFLINE' && !history.error ? (
        <InlineAlert tone="info">
          目前显示已加载的历史记录；重新连接后实时日志会继续追加。
          {realtime.error ? <span className="mt-0.5 block text-footnote text-muted-foreground">连接错误：{realtime.error}</span> : null}
        </InlineAlert>
      ) : null}

      <LogList
        key={viewKey}
        className="flex-1"
        records={records}
        loading={history.loading}
        hasMore={history.hasMore}
        loadingMore={history.loadingMore}
        onLoadOlder={history.loadOlder}
        placeholder={history.error ? (
          <ErrorState message={`历史日志加载失败：${history.error}`} onRetry={history.retry} />
        ) : (
          <EmptyState
            icon={narrowed ? SearchX : ScrollText}
            title="暂无匹配的历史或实时日志"
            description={narrowed ? '换个关键词或放宽筛选条件试试。' : '新的日志会实时出现在这里。'}
            action={conditions.length ? <Button variant="outline" size="sm" onClick={clearFilters}>清除筛选</Button> : null}
          />
        )}
      />

      <LogFilterSheet open={filtersOpen} onOpenChange={setFiltersOpen} filters={filters} onFiltersChange={setFilters} />
    </Page>
  )
}
