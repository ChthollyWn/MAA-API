import { LoaderCircle, ListChecks, RefreshCw } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { PipelineView } from '@/features/dashboard/use-dashboard'

const statusLabels: Record<string, string> = {
  pending: '等待执行',
  running: '正在执行',
  completed: '已完成',
  failed: '执行失败',
  cancelled: '已取消',
  skipped: '已跳过',
}

function formatDuration(seconds: number | null): string | null {
  if (seconds === null) return null
  if (seconds < 60) return `${Math.floor(seconds)} 秒`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.floor(seconds % 60)
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`
}

export interface PipelineProgressProps {
  pipeline: PipelineView | null
  isPending: boolean
  error: Error | null
  onRetry: () => void
}

export function PipelineProgress({ pipeline, isPending, error, onRetry }: PipelineProgressProps) {
  const percent = pipeline && pipeline.total > 0
    ? Math.min(100, Math.round(((pipeline.completed + pipeline.failed) / pipeline.total) * 100))
    : 0

  return (
    <Card aria-labelledby="pipeline-progress-title">
      <CardHeader className="flex-row items-center justify-between gap-3">
        <div>
          <CardTitle id="pipeline-progress-title">当前流水线</CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">任务步骤与执行进度</p>
        </div>
        <Button aria-label="刷新流水线状态" disabled={isPending} onClick={onRetry} size="sm" variant="outline">
          {isPending ? <LoaderCircle aria-hidden="true" className="animate-spin" /> : <RefreshCw aria-hidden="true" />}
          刷新
        </Button>
      </CardHeader>
      <CardContent>
        {error ? (
          <div className="space-y-2" role="alert">
            <p className="text-sm text-destructive">读取流水线失败：{toApiError(error).message}</p>
            <Button onClick={onRetry} size="sm" variant="outline">重试</Button>
          </div>
        ) : isPending && !pipeline ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
            <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />正在读取流水线状态
          </p>
        ) : !pipeline ? (
          <div className="rounded-lg border border-dashed px-4 py-5 text-center">
            <ListChecks aria-hidden="true" className="mx-auto mb-2 size-6 text-muted-foreground" />
            <p className="font-medium">当前没有运行中的流水线</p>
            <p className="mt-1 text-sm text-muted-foreground">开始任务后，进度和各步骤状态会显示在这里。</p>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="break-words font-medium">{pipeline.title ?? '执行中的流水线'}</p>
                {pipeline.id ? <p className="mt-0.5 break-all text-xs text-muted-foreground">{pipeline.id}</p> : null}
              </div>
              <span className="rounded-full border px-2.5 py-1 text-xs font-medium" aria-label={`流水线状态：${statusLabels[pipeline.status] ?? pipeline.status}`}>
                {statusLabels[pipeline.status] ?? pipeline.status}
              </span>
            </div>

            <div>
              <div className="mb-1.5 flex justify-between gap-2 text-sm">
                <span>已完成 {pipeline.completed + pipeline.failed} / {pipeline.total} 项</span>
                <span>{percent}%</span>
              </div>
              <div
                aria-label="流水线任务进度"
                aria-valuemax={pipeline.total || 1}
                aria-valuemin={0}
                aria-valuenow={pipeline.completed + pipeline.failed}
                className="h-2 overflow-hidden rounded-full bg-muted"
                role="progressbar"
              >
                <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${percent}%` }} />
              </div>
            </div>

            {pipeline.tasks.length ? (
              <ol aria-label="流水线任务步骤" className="divide-y rounded-lg border">
                {pipeline.tasks.map((task, index) => (
                  <li className="flex items-center justify-between gap-3 px-3 py-2.5" key={task.id}>
                    <div className="flex min-w-0 items-center gap-2">
                      <span aria-hidden="true" className="w-5 shrink-0 text-right text-xs text-muted-foreground">{index + 1}</span>
                      <span className="truncate text-sm">{task.name}</span>
                    </div>
                    <div className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                      {formatDuration(task.durationSeconds) ? <span>{formatDuration(task.durationSeconds)}</span> : null}
                      <span>{statusLabels[task.status] ?? task.status}</span>
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-sm text-muted-foreground">任务步骤详情正在同步。</p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
