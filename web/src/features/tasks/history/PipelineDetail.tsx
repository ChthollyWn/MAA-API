import { useState } from 'react'
import { useNavigate } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleStop, RotateCcw } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InlineAlert, type AlertTone } from '@/components/ui/inline-alert'
import { Progress } from '@/components/ui/progress'
import { Section } from '@/components/ui/section'
import { ErrorState, LoadingState } from '@/components/ui/states'
import { StickyActionBar } from '@/components/ui/sticky-action-bar'
import { toast } from '@/components/ui/toaster'
import { errorMessage, isObject, textValue } from '../form'
import {
  explicitTitle,
  formatTime,
  isActiveStatus,
  normalizeStatus,
  pipelineElapsed,
  priorityLabel,
  sourceLabel,
  statusLabel,
  statusTone,
  taskDuration,
  taskNamesTitle,
  UNTITLED,
  type PipelineRow,
} from '../pipeline-format'
import { cancelPipeline, pipelineDetailQuery } from '../pipeline-queries'
import { taskTypeIcon } from '../task-types'
import { PipelineLogs } from './PipelineLogs'
import { PipelineScreenshots } from './PipelineScreenshots'

const card = 'rounded-xl border bg-card shadow-card'

function errorOf(value: unknown): { message: string; code: string | null } | null {
  if (!isObject(value)) return null
  const message = textValue(value.message)
  const code = textValue(value.code) || null
  return message || code ? { message: message || '没有给出原因', code } : null
}

function TaskResults({ tasks }: { tasks: PipelineRow[] }) {
  return (
    <Section id="pipeline-tasks" title="任务结果" description={`${tasks.length} 个任务`}>
      {tasks.length === 0 ? (
        <p className={`${card} px-4 py-3.5 text-footnote text-muted-foreground`}>服务端没有返回任务明细。</p>
      ) : (
        <ol className={`${card} divide-y overflow-hidden`}>
          {tasks.map((task, index) => {
            const typeName = textValue(task.type_name)
            const Icon = taskTypeIcon(typeName)
            const status = normalizeStatus(task.status)
            const duration = taskDuration(task.duration_seconds)
            const retries = typeof task.retry_count === 'number' && task.retry_count > 0 ? task.retry_count : 0
            const failure = errorOf(task.error)
            return (
              <li key={textValue(task.id, String(index))} className="flex items-start gap-3 px-4 py-3">
                <span aria-hidden="true" className="w-4 shrink-0 pt-2 text-right text-footnote tabular-nums text-muted-foreground">{index + 1}</span>
                <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-surface-2 text-muted-foreground">
                  <Icon aria-hidden="true" className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <p className="min-w-0 truncate text-[0.9375rem] font-medium">{textValue(task.task_name) || typeName || `任务 ${index + 1}`}</p>
                    <Badge variant={statusTone(status)}>{statusLabel(status)}</Badge>
                  </div>
                  <p className="mt-0.5 text-footnote tabular-nums text-muted-foreground">{duration ?? '未开始'}</p>
                  {retries > 0 ? <p className="text-footnote text-muted-foreground">重试 {retries} 次</p> : null}
                  {failure ? <p className="mt-1 break-words text-footnote text-danger">{failure.message}</p> : null}
                </div>
              </li>
            )
          })}
        </ol>
      )}
    </Section>
  )
}

/** 流水线详情：概要、每个任务的结果、最近日志与截图；运行中或等待中可取消，失败或已取消可重试。 */
export function PipelineDetail({ pipelineId }: { pipelineId: string }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [confirm, confirmElement] = useConfirm()
  const [notice, setNotice] = useState<{ tone: AlertTone; message: string } | null>(null)
  const detail = useQuery({
    ...pipelineDetailQuery(pipelineId),
    refetchInterval: (query) => (isActiveStatus(normalizeStatus(query.state.data?.status)) ? 5_000 : false),
  })
  const pipeline = detail.data

  const refresh = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: keys.pipelines.detail(pipelineId), exact: true }),
    queryClient.invalidateQueries({ queryKey: [...keys.pipelines.detail(pipelineId), 'logs'] }),
    queryClient.invalidateQueries({ queryKey: keys.pipelines.lists() }),
    queryClient.invalidateQueries({ queryKey: keys.queue.all() }),
  ])

  const cancel = useMutation({
    mutationFn: () => cancelPipeline(pipelineId),
    onSuccess: async () => {
      setNotice({ tone: 'success', message: '已发送取消请求。' })
      await refresh()
    },
    onError: (error) => setNotice({ tone: 'danger', message: `取消失败：${errorMessage(error)}` }),
  })

  const retry = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.POST('/api/pipelines/{pipeline_id}/retry', { params: { path: { pipeline_id: pipelineId } }, body: {} })
      if (error) throw toApiError(error, response)
      return data as { pipeline_id?: string }
    },
    onSuccess: async (data) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.pipelines.lists() }),
        queryClient.invalidateQueries({ queryKey: keys.queue.all() }),
      ])
      const nextId = textValue(data?.pipeline_id)
      if (nextId) {
        toast.success('已重新加入队列')
        navigate(`/tasks/history/${encodeURIComponent(nextId)}`, { replace: true })
      } else {
        setNotice({ tone: 'success', message: '已提交重试流水线。' })
      }
    },
    onError: (error) => setNotice({ tone: 'danger', message: `重试失败：${errorMessage(error)}` }),
  })

  if (detail.isPending) return <div className={card}><LoadingState label="正在读取流水线…" /></div>
  if (!pipeline) {
    return (
      <div className={card}>
        <ErrorState message={`读取流水线失败：${errorMessage(detail.error)}`} onRetry={() => void detail.refetch()} retrying={detail.isFetching} />
      </div>
    )
  }

  const status = normalizeStatus(pipeline.status)
  const active = isActiveStatus(status)
  const retryable = status === 'failed' || status === 'cancelled'
  const tasks = Array.isArray(pipeline.tasks) ? pipeline.tasks.filter(isObject) : []
  const title = explicitTitle(pipeline) ?? taskNamesTitle(tasks) ?? UNTITLED
  const elapsed = pipelineElapsed(pipeline)
  const failure = errorOf(pipeline.error)
  const progress = isObject(pipeline.progress) ? pipeline.progress : null
  const progressTotal = typeof progress?.total === 'number' ? progress.total : tasks.length
  const progressDone = (typeof progress?.completed === 'number' ? progress.completed : 0) + (typeof progress?.failed === 'number' ? progress.failed : 0)
  const facts: Array<[string, string]> = [
    ['优先级', priorityLabel(pipeline.priority)],
    ['来源', sourceLabel(pipeline.source)],
    ['创建时间', formatTime(pipeline.created_at, { seconds: true })],
    ['开始时间', formatTime(pipeline.started_at, { seconds: true })],
    ['结束时间', formatTime(pipeline.finished_at, { seconds: true })],
    ...(elapsed ? [['耗时', elapsed.running ? `${elapsed.text}（进行中）` : elapsed.text] as [string, string]] : []),
  ]

  const requestCancel = async () => {
    const ok = await confirm({
      title: '取消这条流水线？',
      description: status === 'running' ? '正在执行的任务会被中断，已经完成的任务不受影响。' : '它会离开待执行队列，不再执行。',
      confirmText: '取消流水线',
      cancelText: '保留',
      tone: 'danger',
    })
    if (ok) {
      setNotice(null)
      cancel.mutate()
    }
  }

  const requestRetry = async () => {
    const ok = await confirm({
      title: '重新执行这条流水线？',
      description: '会用同样的任务和参数新建一条流水线，加入队列。',
      confirmText: '重新加入队列',
    })
    if (ok) {
      setNotice(null)
      retry.mutate()
    }
  }

  return (
    <>
      <div className="space-y-6 pb-4">
        <section aria-labelledby="pipeline-summary-title" className={`${card} p-4`}>
          <div className="flex items-start justify-between gap-3">
            <h2 id="pipeline-summary-title" className="min-w-0 break-words text-headline font-semibold">{title}</h2>
            <Badge variant={statusTone(status)} className="mt-0.5">{statusLabel(status)}</Badge>
          </div>
          {status === 'running' && progressTotal > 0 ? (
            <div className="mt-3 space-y-1.5">
              <Progress value={progressDone} max={progressTotal} label="流水线任务进度" />
              <p className="text-footnote tabular-nums text-muted-foreground">已完成 {progressDone} / {progressTotal} 项</p>
            </div>
          ) : null}
          <dl className="mt-3 divide-y text-sm">
            {facts.map(([label, value]) => (
              <div key={label} className="flex items-baseline justify-between gap-4 py-2">
                <dt className="shrink-0 text-muted-foreground">{label}</dt>
                <dd className="min-w-0 text-right tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
          {failure ? (
            <InlineAlert tone="danger" title="失败原因" className="mt-2">
              <span className="block break-words">{failure.message}</span>
              {failure.code ? <span className="mt-1 block font-mono text-xs">{failure.code}</span> : null}
            </InlineAlert>
          ) : null}
        </section>

        {detail.error ? <InlineAlert tone="warning">详情更新失败：{errorMessage(detail.error)}</InlineAlert> : null}
        {notice ? <InlineAlert tone={notice.tone}>{notice.message}</InlineAlert> : null}

        <TaskResults tasks={tasks} />
        <PipelineLogs pipelineId={pipelineId} active={active} />
        <PipelineScreenshots pipelineId={pipelineId} active={active} />
      </div>

      {active ? (
        <StickyActionBar>
          <Button variant="destructive-soft" loading={cancel.isPending} onClick={() => void requestCancel()}>
            {cancel.isPending ? null : <CircleStop aria-hidden="true" />}
            取消流水线
          </Button>
        </StickyActionBar>
      ) : retryable ? (
        <StickyActionBar>
          <Button loading={retry.isPending} onClick={() => void requestRetry()}>
            {retry.isPending ? null : <RotateCcw aria-hidden="true" />}
            重试
          </Button>
        </StickyActionBar>
      ) : null}
      {confirmElement}
    </>
  )
}
