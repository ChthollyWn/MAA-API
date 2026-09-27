import { Link } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CircleStop, ListChecks } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { Disclosure } from '@/components/ui/disclosure'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Progress } from '@/components/ui/progress'
import { LoadingState } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import { pipelineStatusLabels, pipelineTone } from '@/features/dashboard/format'
import type { PipelineView } from '@/features/dashboard/use-dashboard'
import { cn } from '@/lib/utils'

function formatStepDuration(seconds: number | null): string | null {
  if (seconds === null) return null
  if (seconds < 60) return `${Math.floor(seconds)} 秒`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.floor(seconds % 60)
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`
}

const stepTone: Record<string, string> = {
  completed: 'text-success',
  running: 'text-primary-text',
  failed: 'text-danger',
}

export interface CurrentPipelineCardProps {
  pipeline: PipelineView | null
  isPending: boolean
  error: Error | null
  onRetry: () => void
}

/** 「当前任务」：进度、正在执行的步骤、全部步骤与停止。 */
export function CurrentPipelineCard({ pipeline, isPending, error, onRetry }: CurrentPipelineCardProps) {
  const queryClient = useQueryClient()
  const [confirm, confirmElement] = useConfirm()
  const stop = useMutation({
    mutationFn: async (pipelineId: string) => {
      const { error: stopError, response } = await api.DELETE('/api/pipelines/{pipeline_id}', { params: { path: { pipeline_id: pipelineId } } })
      if (stopError) throw toApiError(stopError, response)
    },
    onSuccess: async () => {
      toast.success('已请求停止当前任务')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.pipelines.current() }),
        queryClient.invalidateQueries({ queryKey: keys.queue.all() }),
      ])
    },
    onError: (stopError) => toast.error(`停止失败：${toApiError(stopError).message}`),
  })

  const card = 'rounded-xl border bg-card shadow-card'

  if (error) {
    return (
      <section aria-label="当前任务" className={cn(card, 'p-4')}>
        <InlineAlert tone="danger" action={<Button onClick={onRetry} size="sm" variant="outline">重试</Button>}>
          读取当前任务失败：{toApiError(error).message}
        </InlineAlert>
      </section>
    )
  }
  if (isPending && !pipeline) {
    return <section aria-label="当前任务" className={card}><LoadingState label="正在读取当前任务…" /></section>
  }
  if (!pipeline) {
    return (
      <section aria-label="当前任务" className={cn(card, 'flex items-center gap-4 p-4')}>
        <span className="grid size-11 shrink-0 place-items-center rounded-full bg-surface-2 text-muted-foreground">
          <ListChecks aria-hidden="true" className="size-5" />
        </span>
        <div className="min-w-0">
          <p className="text-[0.9375rem] font-semibold">当前没有运行中的任务</p>
          <p className="mt-0.5 text-footnote text-muted-foreground">新建任务或运行一个定时任务后，进度会显示在这里。</p>
        </div>
      </section>
    )
  }

  const done = pipeline.completed + pipeline.failed
  const total = pipeline.total
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
  const running = pipeline.status === 'running'
  const currentStep = pipeline.tasks.find((task) => task.status === 'running')
  const statusLabel = pipelineStatusLabels[pipeline.status] ?? pipeline.status
  const tone = pipelineTone(pipeline.status)

  const stopPipeline = async () => {
    if (!pipeline.id) return
    const ok = await confirm({
      title: '停止当前任务？',
      description: '正在执行的步骤会被中断，已经完成的步骤不受影响。',
      confirmText: '停止任务',
      tone: 'danger',
    })
    if (ok) stop.mutate(pipeline.id)
  }

  return (
    <section aria-labelledby="current-pipeline-title" className={cn(card, 'p-4')}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-footnote text-muted-foreground">当前任务</p>
          <h2 id="current-pipeline-title" className="mt-0.5 break-words text-headline font-semibold">{pipeline.title ?? '执行中的流水线'}</h2>
        </div>
        <Badge variant={tone} aria-label={`流水线状态：${statusLabel}`}>{statusLabel}</Badge>
      </div>

      <Progress value={done} max={total || 1} label="流水线任务进度" tone={pipeline.failed > 0 ? 'warning' : 'primary'} className="mt-4 h-2" />
      <div className="mt-2 flex justify-between gap-2 text-footnote text-muted-foreground tabular-nums">
        <span>已完成 {done} / {total} 项</span>
        <span>{percent}%</span>
      </div>
      {currentStep ? <p className="mt-3 text-sm">正在执行：<span className="font-medium">{currentStep.name}</span></p> : null}

      {pipeline.tasks.length ? (
        <Disclosure variant="plain" title={`全部步骤（${pipeline.tasks.length}）`} className="-mx-1 mt-2">
          <ol aria-label="流水线任务步骤" className="space-y-1">
            {pipeline.tasks.map((task, index) => (
              <li key={task.id} className="flex items-center justify-between gap-3 rounded-lg px-1 py-1.5">
                <span className="flex min-w-0 items-center gap-2.5">
                  <span aria-hidden="true" className="w-4 shrink-0 text-right text-xs text-muted-foreground tabular-nums">{index + 1}</span>
                  <span className="break-words text-sm">{task.name}</span>
                </span>
                <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground tabular-nums">
                  {formatStepDuration(task.durationSeconds) ? <span>{formatStepDuration(task.durationSeconds)}</span> : null}
                  <span className={cn('font-medium', stepTone[task.status])}>{pipelineStatusLabels[task.status] ?? task.status}</span>
                </span>
              </li>
            ))}
          </ol>
        </Disclosure>
      ) : (
        <p className="mt-3 text-footnote text-muted-foreground">任务步骤详情正在同步。</p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {running && pipeline.id ? (
          <Button variant="destructive-soft" size="sm" loading={stop.isPending} onClick={() => void stopPipeline()}>
            {stop.isPending ? null : <CircleStop aria-hidden="true" />}
            停止任务
          </Button>
        ) : null}
        {pipeline.id ? (
          <Button asChild variant="ghost" size="sm">
            <Link to={`/tasks/history/${encodeURIComponent(pipeline.id)}`}>查看详情</Link>
          </Button>
        ) : null}
      </div>
      {confirmElement}
    </section>
  )
}
