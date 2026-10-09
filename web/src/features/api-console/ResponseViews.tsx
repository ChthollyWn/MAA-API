import { Send } from 'lucide-react'
import { Badge, StatusDot, type BadgeProps, type StatusTone } from '@/components/ui/badge'
import { Disclosure } from '@/components/ui/disclosure'
import { InlineAlert } from '@/components/ui/inline-alert'
import { EmptyState } from '@/components/ui/states'
import { cn } from '@/lib/utils'
import { formatJson, recordOf, responseLabel, type OpenApiDocument, type ResponseView } from './console-model'
import { LazyJsonEditor } from './LazyJsonEditor'
import type { ConsoleRealtimeState } from './use-console-realtime'

const toneVariant: Record<ResponseView['tone'], NonNullable<BadgeProps['variant']>> = {
  success: 'success',
  neutral: 'neutral',
  warning: 'warning',
  danger: 'danger',
}

function milliseconds(value: number): string {
  return `${Number(value).toFixed(1)} ms`
}

/** 状态码 + 往返耗时；有 X-Response-Time-Ms 时并列服务端耗时，用来区分「后端慢」和「网络慢」。 */
export function ResponseStatus({ response, className }: { response: ResponseView; className?: string }) {
  return (
    <span className={cn('flex min-w-0 items-center gap-2', className)}>
      <Badge variant={toneVariant[response.tone]} className="rounded-md font-mono font-semibold">{responseLabel(response)}</Badge>
      <span className="min-w-0 truncate text-footnote text-muted-foreground tabular-nums">
        往返 {milliseconds(response.elapsedMs)}
        {typeof response.serverElapsedMs === 'number' ? ` · 服务端 ${milliseconds(response.serverElapsedMs)}` : ''}
      </span>
    </span>
  )
}

function ErrorCard({ error }: { error: NonNullable<ResponseView['errorCard']> }) {
  const hasDetails = Object.keys(error.details).length > 0
  return (
    <div className="space-y-1.5 rounded-xl bg-danger-soft px-3.5 py-3">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <code className="font-mono text-sm font-semibold text-danger [overflow-wrap:anywhere]">{error.code}</code>
        <a href="/docs" className="text-footnote font-medium text-primary-text underline-offset-2 hover:underline">查看错误码与 API 文档</a>
      </div>
      <p className="text-sm font-medium [overflow-wrap:anywhere]">{error.message}</p>
      {hasDetails ? (
        <details className="group">
          <summary className="inline-flex min-h-9 cursor-pointer items-center text-footnote font-medium text-muted-foreground [&::-webkit-details-marker]:hidden">
            <span className="group-open:hidden">展开 details</span>
            <span className="hidden group-open:inline">收起 details</span>
          </summary>
          <pre className="mt-1 overflow-x-auto rounded-lg bg-surface p-2.5 font-mono text-xs leading-5">{JSON.stringify(error.details, null, 2)}</pre>
        </details>
      ) : null}
    </div>
  )
}

export function ResponseContents({ response, openApi }: { response: ResponseView | null; openApi?: OpenApiDocument }) {
  if (!response) {
    return <EmptyState icon={Send} title="还没有响应" description="发送请求后这里会显示结果" className="py-8" />
  }
  const headers = recordOf(response.headers)
  const contentType = String(headers['content-type'] ?? '').toLowerCase()
  const text = response.bodyText.trim()
  const isJson = contentType.includes('json') || (typeof response.body === 'object' && response.body !== null) || text.startsWith('{') || text.startsWith('[')
  return (
    <div className="space-y-4">
      {response.errorCard ? <ErrorCard error={response.errorCard} /> : null}
      {response.imageUrl ? (
        <figure className="overflow-hidden rounded-xl border bg-surface-2 p-2">
          <img src={response.imageUrl} alt="API 图片响应" className="mx-auto max-h-[42dvh] max-w-full rounded-md object-contain" />
          <figcaption className="mt-2 text-center font-mono text-footnote text-muted-foreground">{String(headers['content-type'] ?? '图片响应')}</figcaption>
        </figure>
      ) : response.bodyText ? (
        <section aria-label="响应体" className="space-y-2">
          <div className="flex items-center justify-between gap-2 px-1">
            <h3 className="text-sm font-semibold">响应体</h3>
            <span className="text-footnote text-muted-foreground">{isJson ? 'JSON' : '文本'}</span>
          </div>
          {isJson
            ? <LazyJsonEditor value={formatJson(response.bodyText) ?? response.bodyText} readOnly openApi={openApi} ariaLabel="只读 JSON 响应" />
            : <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface-2 p-3 font-mono text-xs leading-5">{response.bodyText}</pre>}
        </section>
      ) : (
        <p className="rounded-lg bg-surface-2 px-3 py-2.5 text-footnote text-muted-foreground">响应没有内容</p>
      )}
      <Disclosure variant="plain" title={<>响应头 <span className="font-normal text-muted-foreground tabular-nums">{Object.keys(headers).length}</span></>}>
        <dl className="divide-y rounded-lg bg-surface-2/70">
          {Object.entries(headers).map(([key, value]) => (
            <div key={key} className="grid grid-cols-[minmax(6rem,0.8fr)_minmax(0,1.4fr)] gap-3 px-3 py-2 font-mono text-xs">
              <dt className="text-muted-foreground [overflow-wrap:anywhere]">{key}</dt>
              <dd className="[overflow-wrap:anywhere]">{key.toLowerCase() === 'set-cookie' ? '••••••' : String(value)}</dd>
            </div>
          ))}
        </dl>
      </Disclosure>
    </div>
  )
}

const connectionText: Record<ConsoleRealtimeState, { label: string; tone: StatusTone }> = {
  CONNECTED: { label: '已连接', tone: 'success' },
  CONNECTING: { label: '连接中', tone: 'warning' },
  OFFLINE: { label: '离线', tone: 'neutral' },
}

const timelineLabel: Record<string, string> = { core_status: '内核状态', device_status: '设备状态', pipeline_status: '流水线状态' }

function levelClass(level: string): string {
  if (level === 'ERROR' || level === 'CRITICAL') return 'text-danger'
  if (level === 'WARNING') return 'text-warning'
  if (level === 'DEBUG') return 'text-muted-foreground'
  return 'text-primary-text'
}

export interface LogContentsProps {
  logs: Record<string, unknown>[]
  timeline: Array<{ type: string; data: Record<string, unknown>; at: number }>
  requestId: string | null
  connection: ConsoleRealtimeState
  historyTruncated: boolean
}

/** 这次请求（request_id）与关联流水线的服务端日志，外加内核、设备、流水线的状态时间线。 */
export function LogContents({ logs, timeline, requestId, connection, historyTruncated }: LogContentsProps) {
  const state = connectionText[connection] ?? connectionText.OFFLINE
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-1 text-footnote">
        <span className="inline-flex items-center gap-2">
          <StatusDot tone={state.tone} pulse={connection === 'CONNECTING'} />专用 WebSocket：{state.label}
        </span>
        <span className="font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">request_id {requestId ?? '等待发送请求'}</span>
      </div>
      {historyTruncated ? <InlineAlert tone="warning">日志历史不完整，较早的部分可能缺失</InlineAlert> : null}
      <section aria-label="状态时间线" className="space-y-2">
        <h3 className="px-1 text-sm font-semibold">状态时间线</h3>
        {timeline.length === 0 ? (
          <p className="px-1 text-footnote text-muted-foreground">还没有状态变化</p>
        ) : (
          <ol className="ml-2 space-y-2.5 border-l pl-4">
            {timeline.slice(-6).map((item, index) => (
              <li key={`${item.at}-${index}`} className="relative text-footnote">
                <span aria-hidden="true" className="absolute -left-[1.3rem] top-1.5 size-2 rounded-full bg-primary ring-4 ring-card" />
                <span className="font-medium">{timelineLabel[item.type] ?? item.type}</span>
                <code className="ml-2 font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">{JSON.stringify(item.data)}</code>
              </li>
            ))}
          </ol>
        )}
      </section>
      <section aria-label="服务端日志" className="space-y-2">
        <h3 className="px-1 text-sm font-semibold">服务端日志 <span className="font-normal text-muted-foreground tabular-nums">{logs.length}</span></h3>
        {logs.length === 0 ? (
          <p className="px-1 text-footnote text-muted-foreground">还没有收到这次请求或关联流水线的日志</p>
        ) : (
          <ol className="divide-y overflow-hidden rounded-lg border">
            {logs.map((record, index) => {
              const level = String(record.level ?? 'INFO')
              return (
                <li key={String(record.id ?? index)} className="space-y-1 px-3 py-2.5">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                    <span className={cn('font-mono font-semibold', levelClass(level))}>{level}</span>
                    <span className="text-muted-foreground">{String(record.source ?? 'server')}</span>
                    {record.request_id ? <Badge variant="neutral">本次请求</Badge> : null}
                    {record.pipeline_id ? <Badge variant="primary" className="font-mono">流水线 {String(record.pipeline_id)}</Badge> : null}
                  </div>
                  <p className="text-sm [overflow-wrap:anywhere]">{String(record.content ?? record.message ?? JSON.stringify(record))}</p>
                </li>
              )
            })}
          </ol>
        )}
      </section>
    </div>
  )
}
