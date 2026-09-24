import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, LoaderCircle, Wifi, WifiOff } from 'lucide-react'
import { useLocation, useNavigate } from 'react-router'
import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { LogFilters, type LogSourceFilter } from '@/features/logs/LogFilters'
import { LogList } from '@/features/logs/LogList'
import { useLogHistory, type LogLevelFilter } from '@/features/logs/use-log-history'
import { LAST_SEEN_ID_STORAGE_KEY, realtimeClient } from '@/realtime/client'
import type { LogRecord } from '@/realtime/events'
import { useRealtimeStatus } from '@/realtime/RealtimeProvider'
import { useLogBuffer } from '@/stores/log-buffer'

function readLastSeenId(): number {
  try {
    const value = Number(window.localStorage.getItem(LAST_SEEN_ID_STORAGE_KEY) ?? 0)
    return Number.isSafeInteger(value) && value >= 0 ? value : 0
  } catch {
    return 0
  }
}

function sourceFromPath(pathname: string): LogSourceFilter {
  const source = pathname.replace(/\/+$/, '').split('/').at(-1)
  return source === 'task' || source === 'service' || source === 'core' ? source : 'all'
}

function asEpoch(value: string): number | undefined {
  if (!value) return undefined
  const timestamp = new Date(value).getTime()
  return Number.isFinite(timestamp) ? timestamp / 1000 : undefined
}

function matchesLevels(record: LogRecord, levels: LogLevelFilter[]): boolean {
  if (levels.length === 0) return true
  const level = String(record.level ?? 'INFO').toUpperCase()
  return levels.includes((level === 'WARN' ? 'WARNING' : level) as LogLevelFilter)
}

export default function LogsPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const source = sourceFromPath(location.pathname)
  const realtime = useRealtimeStatus()
  const bySource = useLogBuffer((state) => state.bySource)
  const [levels, setLevels] = useState<LogLevelFilter[]>([])
  const [keyword, setKeyword] = useState('')
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const lastSeenIdRef = useRef<number | null>(null)
  if (lastSeenIdRef.current === null) lastSeenIdRef.current = readLastSeenId()

  useEffect(() => {
    realtimeClient.setLogSubscription(true)
    return () => realtimeClient.setLogSubscription(false)
  }, [])

  const filters = useMemo(() => ({
    levels,
    keyword,
    since: asEpoch(since),
    until: asEpoch(until),
  }), [keyword, levels, since, until])
  const history = useLogHistory(filters, lastSeenIdRef.current)
  const liveRecords = useMemo(() => Object.values(bySource).flat(), [bySource])
  const records = useMemo(() => {
    const byId = new Map<number, LogRecord>()
    for (const record of history.records) byId.set(record.id, record)
    for (const record of liveRecords) byId.set(record.id, record)
    const query = keyword.trim().toLocaleLowerCase()
    return [...byId.values()]
      .filter((record) => source === 'all' || record.source === source)
      .filter((record) => matchesLevels(record, levels))
      .filter((record) => !query || String(record.content ?? '').toLocaleLowerCase().includes(query))
      .filter((record) => filters.since === undefined || (typeof record.ts === 'number' && record.ts >= filters.since))
      .filter((record) => filters.until === undefined || (typeof record.ts === 'number' && record.ts <= filters.until))
      .sort((left, right) => left.id - right.id)
  }, [filters.since, filters.until, history.records, keyword, levels, liveRecords, source])

  const changeSource = (nextSource: LogSourceFilter) => {
    navigate(nextSource === 'all' ? '/logs' : `/logs/${nextSource}`)
  }

  const connectionLabel = realtime.status === 'CONNECTED'
    ? '实时连接正常'
    : realtime.status === 'OFFLINE'
      ? '实时离线'
      : realtime.status === 'RECONNECTING'
        ? `正在重连（第 ${realtime.reconnectAttempt} 次）`
        : '正在连接实时服务'
  const connectionVariant = realtime.status === 'CONNECTED'
    ? 'positive'
    : realtime.status === 'OFFLINE'
      ? 'destructive'
      : 'warning'

  return (
    <>
      <PageHeader title="日志" description="历史日志与实时消息" />
      <Card className="mt-4 overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3 sm:px-6">
          <div className="flex items-center gap-2">
            <Badge variant={connectionVariant}>
              {realtime.status === 'CONNECTED'
                ? <Wifi aria-hidden="true" className="mr-1 size-3" />
                : <WifiOff aria-hidden="true" className="mr-1 size-3" />}
              {connectionLabel}
            </Badge>
            <span className="text-xs text-muted-foreground">{records.length.toLocaleString()} 条匹配日志</span>
          </div>
          {realtime.error ? <span className="text-xs text-negative">{realtime.error}</span> : null}
        </div>

        <LogFilters
          source={source}
          onSourceChange={changeSource}
          levels={levels}
          onLevelsChange={setLevels}
          keyword={keyword}
          onKeywordChange={setKeyword}
          since={since}
          onSinceChange={setSince}
          until={until}
          onUntilChange={setUntil}
        />

        {history.error ? (
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-destructive/30 bg-destructive/5 px-4 py-3" role="alert">
            <p className="flex items-center gap-2 text-sm text-destructive"><AlertTriangle className="size-4 shrink-0" aria-hidden="true" />历史日志加载失败：{history.error}</p>
            <Button variant="outline" size="sm" onClick={history.retry}>重试</Button>
          </div>
        ) : null}

        {realtime.truncated && !history.backfillResolved ? (
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-warning/40 bg-warning/10 px-4 py-3" role="alert">
            <p className="text-sm">断线期间的日志超出实时缓冲，可能存在未加载的历史日志。</p>
            <Button
              variant="outline"
              size="sm"
              disabled={history.backfillLoading}
              onClick={() => void history.loadTruncatedHistory(liveRecords)}
            >
              {history.backfillLoading ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : null}
              {history.backfillLoading ? '正在补齐' : '加载缺失历史日志'}
            </Button>
          </div>
        ) : realtime.truncated && history.backfillResolved ? (
          <div className="border-b border-positive/30 bg-positive/5 px-4 py-2 text-sm text-positive" role="status">
            已从历史记录补齐 {history.recoveredCount.toLocaleString()} 条缺失日志
          </div>
        ) : null}
        {history.backfillError ? (
          <p className="border-b border-destructive/30 bg-destructive/5 px-4 py-2 text-sm text-destructive" role="alert">
            历史补齐失败：{history.backfillError}
          </p>
        ) : null}

        <CardContent className="p-0">
          <LogList
            records={records}
            loading={history.loading}
            hasMore={history.hasMore}
            loadingMore={history.loadingMore}
            onLoadOlder={history.loadOlder}
          />
        </CardContent>
      </Card>
      {realtime.status === 'OFFLINE' && !history.error ? (
        <p className="mt-3 text-xs text-muted-foreground" role="status">目前显示已加载的历史记录；重新连接后实时日志会继续追加。</p>
      ) : null}
    </>
  )
}
