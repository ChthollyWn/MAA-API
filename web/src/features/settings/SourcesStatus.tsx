import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Database } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge, StatusDot } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import { errorText, formatTime } from './format'

/**
 * `GET /api/sources/status` 与 `DELETE /api/sources/cache`（规格 §5.11、附录 C）。
 *
 * 这两个端点还没进 `web/src/types/api.d.ts`（T9 的生成物）。接口形状按规格冻结，先在
 * 本地声明；请求仍走 `api` 客户端，鉴权（`X-Token`）与请求号由它的中间件照旧加。
 * 生成类型落地后删掉这里的转写，直接调用 `api.GET('/api/sources/status')`。
 */
interface SourceCacheStats {
  entries: number
  bytes: number
  hits: number
  misses: number
  /** 没有读写时后端给 null（`SourceCacheStats.hit_rate`），显示成「—」。 */
  hit_rate: number | null
  evictions: number
}

interface SourceStatus {
  kind: string
  label: string
  enabled: boolean
  rate_per_minute: number
  last_success_at: string | null
  last_error_at: string | null
  last_error_code: string | null
  last_error_message: string | null
  consecutive_failures: number
  cooldown_until: string | null
  cooldown_seconds: number
  requests: number
  bytes_read: number
  retries: number
  cache: SourceCacheStats
}

interface SourcesStatusPayload {
  sources: SourceStatus[]
}

interface ClearCacheResult {
  source: string | null
  cleared: number
}

interface SourcesEndpoints {
  GET: (
    path: '/api/sources/status',
    init?: { signal?: AbortSignal },
  ) => Promise<{ data?: SourcesStatusPayload; error?: unknown; response: Response }>
  DELETE: (
    path: '/api/sources/cache',
    init?: { params?: { query?: { source: string } } },
  ) => Promise<{ data?: ClearCacheResult; error?: unknown; response: Response }>
}

const sourcesApi = api as unknown as SourcesEndpoints

/** 与 `keys.all` 同根，方便按 `['api', 'settings']` 之外的前缀整片失效。 */
export const SOURCES_STATUS_KEY = [...keys.all, 'sources', 'status'] as const

/** 状态面板的刷新间隔：每 30 s 一次（规格 §5.11）。 */
export const SOURCES_REFRESH_MS = 30_000

async function fetchSourcesStatus(signal?: AbortSignal): Promise<SourcesStatusPayload> {
  const { data, error, response } = await sourcesApi.GET('/api/sources/status', { signal })
  if (error) throw toApiError(error, response)
  return { sources: data?.sources ?? [] }
}

/** 缓存字节数：B / KB / MB / GB，整数只有 B，其余留一位小数。 */
export function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB'] as const
  let amount = value
  let unit = 0
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024
    unit += 1
  }
  const text = unit === 0 ? String(Math.round(amount)) : amount.toFixed(1).replace(/\.0$/, '')
  return `${text} ${units[unit]}`
}

/** 命中率：0.3333 → 33.3%，1 → 100%，0 → 0%；null（还没有读写）与无效值显示「—」。 */
export function formatHitRate(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  if (value <= 0) return '0%'
  return `${(Math.min(value, 1) * 100).toFixed(1).replace(/\.0$/, '')}%`
}

function errorLine(source: SourceStatus): string {
  const code = source.last_error_code ?? ''
  const message = source.last_error_message ?? ''
  if (!code && !message) return '无'
  return [code || '未知错误', message].filter(Boolean).join(' · ')
}

function SourceRow({ source, busy, clearing, onClear }: {
  source: SourceStatus
  busy: boolean
  clearing: boolean
  onClear: () => void
}) {
  const failing = source.consecutive_failures > 0
  const tone = source.enabled ? (failing ? 'warning' : 'success') : 'neutral'
  return (
    <li data-testid={`source-${source.kind}`} className="space-y-2 p-3.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <StatusDot tone={tone} />
        <span className="text-[0.9375rem] font-medium">{source.label}</span>
        <span className="font-mono text-xs text-muted-foreground">{source.kind}</span>
        <Badge variant={source.enabled ? 'success' : 'neutral'}>{source.enabled ? '已启用' : '已关闭'}</Badge>
        <span className="text-footnote text-muted-foreground">限流 {source.rate_per_minute} 次/分钟</span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="ml-auto"
          disabled={busy || source.cache.entries === 0}
          loading={clearing}
          onClick={onClear}
        >
          清空缓存
        </Button>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-footnote">
        <dt className="text-muted-foreground">最近成功</dt>
        <dd className="tabular-nums">{formatTime(source.last_success_at) ?? '暂无'}</dd>
        <dt className="text-muted-foreground">最近错误</dt>
        <dd className="min-w-0 break-words">{errorLine(source)}</dd>
        <dt className="text-muted-foreground">连续失败</dt>
        <dd className="tabular-nums">{source.consecutive_failures} 次</dd>
        <dt className="text-muted-foreground">冷却</dt>
        <dd className="tabular-nums">{source.cooldown_seconds > 0 ? `剩余 ${source.cooldown_seconds} 秒` : '无'}</dd>
        <dt className="text-muted-foreground">缓存</dt>
        <dd className="tabular-nums">
          {source.cache.entries} 条 · {formatBytes(source.cache.bytes)} · 命中率 {formatHitRate(source.cache.hit_rate)}
        </dd>
      </dl>
    </li>
  )
}

export interface SourcesStatusProps {
  /** 轮询间隔；测试用小值。默认 30 s（规格 §5.11）。 */
  refreshMs?: number
}

/**
 * 「外部数据源」分区顶部的状态面板：每个来源的开关、最近成功/失败、冷却与缓存统计，
 * 外加清空缓存。与 `NotificationChannels` 同一套做法：react-query 轮询 + 就地错误条，
 * 卸载时定时器与在途请求都由查询自身收掉（`queryFn` 消费 `signal`）。
 */
export function SourcesStatus({ refreshMs = SOURCES_REFRESH_MS }: SourcesStatusProps) {
  const queryClient = useQueryClient()
  const [notice, setNotice] = useState<string | null>(null)
  // undefined＝没有清空任务；kind＝清了哪个来源；null＝清全部。
  const [pending, setPending] = useState<{ kind: string | null } | null>(null)
  const status = useQuery({
    queryKey: SOURCES_STATUS_KEY,
    queryFn: ({ signal }) => fetchSourcesStatus(signal),
    refetchInterval: refreshMs,
  })

  const sources = status.data?.sources ?? []
  const totalEntries = sources.reduce((sum, source) => sum + source.cache.entries, 0)

  const clear = async (kind: string | null) => {
    setPending({ kind })
    setNotice(null)
    try {
      const { data, error, response } = await sourcesApi.DELETE(
        '/api/sources/cache',
        kind ? { params: { query: { source: kind } } } : {},
      )
      if (error) throw toApiError(error, response)
      const cleared = data?.cleared ?? 0
      const name = kind ? sources.find((source) => source.kind === kind)?.label ?? kind : ''
      toast.success(kind ? `已清空「${name}」的缓存：${cleared} 条` : `已清空全部缓存：${cleared} 条`)
      // 清完只刷新一次状态；查询失效让其它面板看到同一份新数据。
      await queryClient.invalidateQueries({ queryKey: SOURCES_STATUS_KEY })
    } catch (caught) {
      setNotice(`清空缓存失败：${errorText(caught)}`)
    } finally {
      setPending(null)
    }
  }

  return (
    <Section
      title="来源状态"
      description={`每 ${Math.round(refreshMs / 1000)} 秒自动刷新。清空缓存只影响本服务的内存缓存，不会改动外部来源。`}
      actions={sources.length ? (
        <Button
          type="button"
          variant="soft"
          size="sm"
          disabled={pending !== null || totalEntries === 0}
          loading={pending?.kind === null}
          onClick={() => void clear(null)}
        >
          清空全部缓存
        </Button>
      ) : null}
    >
      {notice ? <InlineAlert tone="danger">{notice}</InlineAlert> : null}
      {status.isPending ? (
        <LoadingState label="正在读取外部来源状态…" />
      ) : status.isError ? (
        <div className="rounded-xl border bg-card shadow-card">
          <ErrorState
            title="外部来源状态读取失败"
            message={errorText(status.error)}
            onRetry={() => void status.refetch()}
            retrying={status.isFetching}
          />
        </div>
      ) : sources.length ? (
        <ul aria-label="外部来源状态" className="divide-y overflow-hidden rounded-xl border bg-card shadow-card">
          {sources.map((source) => (
            <SourceRow
              key={source.kind}
              source={source}
              busy={pending !== null}
              clearing={pending?.kind === source.kind}
              onClear={() => void clear(source.kind)}
            />
          ))}
        </ul>
      ) : (
        <div className="rounded-xl border bg-card shadow-card">
          <EmptyState icon={Database} title="没有已登记的外部来源" description="这个服务还没有启用任何外部数据来源" />
        </div>
      )}
    </Section>
  )
}
