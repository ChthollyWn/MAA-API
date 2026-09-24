import { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { UpdateRecord } from '@/features/updates/types'
import { asText, errorMessage, formatDate } from '@/features/updates/types'

interface UpdateHistoryProps {
  records: UpdateRecord[]
  total?: number
  isLoading: boolean
  error: unknown
  retryingId?: string | null
  detailId?: string | null
  detail?: UpdateRecord | null
  detailError?: unknown
  detailLoading?: boolean
  onRetry: (id: string) => void
  onDetailRequested: (id: string) => void
  onRefresh: () => void
}

const targetLabels = { core: 'MAA 内核', resource: '活动资源', game: '游戏本体' }

function recordVersion(record: UpdateRecord): string {
  const from = asText(record.from_version) ?? asText(record.current_version)
  const to = asText(record.to_version) ?? asText(record.target_version)
  if (from && to) return `${from} → ${to}`
  return to ?? from ?? '版本信息未提供'
}

function isFailed(record: UpdateRecord): boolean {
  return record.status.toLowerCase() === 'failed'
}

function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    pending: '等待中',
    running: '执行中',
    success: '已完成',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
    skipped: '已跳过',
  }
  return labels[value.toLowerCase()] ?? value
}

function statusVariant(value: string): 'destructive' | 'positive' | 'warning' | 'secondary' {
  const status = value.toLowerCase()
  if (status === 'failed') return 'destructive'
  if (status === 'success' || status === 'completed') return 'positive'
  if (status === 'pending' || status === 'running') return 'warning'
  return 'secondary'
}

export function UpdateHistory({
  records, total, isLoading, error, retryingId, detailId, detail, detailError, detailLoading,
  onRetry, onDetailRequested, onRefresh,
}: UpdateHistoryProps) {
  const [expanded, setExpanded] = useState<string | null>(null)

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div>
          <CardTitle>最近更新记录</CardTitle>
          <CardDescription className="mt-1">保留最近的更新执行结果；检查版本不会生成历史记录。</CardDescription>
        </div>
        <Button type="button" variant="outline" size="sm" className="min-h-11" onClick={onRefresh} disabled={isLoading}>
          刷新记录
        </Button>
      </CardHeader>
      <CardContent>
        {error ? (
          <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
            <p>{errorMessage(error)}</p>
            <Button type="button" variant="link" className="mt-1 h-auto p-0" onClick={onRefresh}>重试加载</Button>
          </div>
        ) : isLoading ? (
          <p className="text-sm text-muted-foreground" role="status">正在读取更新记录…</p>
        ) : records.length === 0 ? (
          <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">还没有更新记录。</p>
        ) : (
          <div className="divide-y divide-border">
            {records.map((record) => {
              const isOpen = expanded === record.id
              const failed = isFailed(record)
              const shownRecord = detailId === record.id && detail ? detail : record
              const errorText = asText(shownRecord.error_message)
              return (
                <article key={record.id} className="py-3 first:pt-0 last:pb-0">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">{targetLabels[record.target] ?? record.target}</span>
                        <Badge variant={statusVariant(record.status)}>
                          {statusLabel(record.status)}
                        </Badge>
                      </div>
                      <p className="mt-1 break-words text-sm text-muted-foreground">{recordVersion(record)}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{formatDate(record.created_at ?? record.started_at)}</p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="min-h-11"
                        aria-expanded={isOpen}
                        onClick={() => {
                          setExpanded(isOpen ? null : record.id)
                          if (!isOpen) onDetailRequested(record.id)
                        }}
                      >
                        {isOpen ? '收起详情' : '查看详情'}
                      </Button>
                      {failed && (
                        <Button type="button" size="sm" className="min-h-11" disabled={retryingId === record.id} onClick={() => onRetry(record.id)}>
                          {retryingId === record.id ? '正在重试…' : '重试更新'}
                        </Button>
                      )}
                    </div>
                  </div>
                  {isOpen && (
                    <div className="mt-3 space-y-2 rounded-md bg-muted/40 p-3 text-sm">
                      <p><span className="text-muted-foreground">记录编号：</span><code className="break-all">{record.id}</code></p>
                      {detailId === record.id && detailLoading && <p role="status">正在读取更新详情…</p>}
                      {detailId === record.id && detailError !== null && detailError !== undefined && <p role="alert">详情读取失败：{errorMessage(detailError)}</p>}
                      {shownRecord.phase && <p><span className="text-muted-foreground">阶段：</span>{shownRecord.phase}</p>}
                      {shownRecord.error_code && <p><span className="text-muted-foreground">错误代码：</span><code>{shownRecord.error_code}</code></p>}
                      {errorText && <p role="alert" className="break-words">{errorText}</p>}
                      {shownRecord.finished_at && <p><span className="text-muted-foreground">结束时间：</span>{formatDate(shownRecord.finished_at)}</p>}
                      {shownRecord.log !== undefined && shownRecord.log !== null && (
                        <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded border bg-background p-2 text-xs">{typeof shownRecord.log === 'string' ? shownRecord.log : JSON.stringify(shownRecord.log, null, 2)}</pre>
                      )}
                    </div>
                  )}
                </article>
              )
            })}
          </div>
        )}
        {total !== undefined && total > records.length && (
          <p className="mt-3 text-xs text-muted-foreground">显示最近 {records.length} 条，共 {total} 条。</p>
        )}
      </CardContent>
    </Card>
  )
}
