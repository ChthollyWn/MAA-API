import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUpToLine, CircleStop, Inbox, Pause, Play, RefreshCw, X } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { ActionMenu } from '@/components/ui/action-sheet'
import { Badge, StatusDot } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InlineAlert, type AlertTone } from '@/components/ui/inline-alert'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState, Spinner } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import { errorMessage, isObject } from '../form'
import {
  explicitTitle,
  formatTime,
  normalizeStatus,
  pipelineIdOf,
  priorityLabel,
  sourceLabel,
  statusLabel,
  UNTITLED,
  type PipelineRow,
} from '../pipeline-format'
import { cancelPipeline, useUntitledPipelineNames } from '../pipeline-queries'

interface QueueSnapshot {
  running?: PipelineRow | null
  pending?: PipelineRow[]
  paused?: boolean
  counts?: { pending?: number; running?: number }
}

const card = 'rounded-xl border bg-card shadow-card'

function currentPipeline(value: unknown): PipelineRow | null {
  if (!isObject(value)) return null
  if ('pipeline' in value) return isObject(value.pipeline) ? value.pipeline : null
  return value
}

function detailPath(id: string): string {
  return `/tasks/history/${encodeURIComponent(id)}`
}

/** 任务 › 队列：队列状态与暂停，当前运行项，待执行列表与行内操作。每 5 秒刷新。 */
export function QueueView() {
  const queryClient = useQueryClient()
  const [confirm, confirmElement] = useConfirm()
  const [notice, setNotice] = useState<{ tone: AlertTone; message: string } | null>(null)

  const queue = useQuery({
    queryKey: keys.queue.all(),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/queue')
      if (error) throw toApiError(error, response)
      return data as QueueSnapshot
    },
    refetchInterval: 5_000,
    retry: false,
  })
  const current = useQuery({
    queryKey: keys.pipelines.current(),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/pipelines/current')
      if (error) throw toApiError(error, response)
      return data
    },
    retry: false,
  })

  const snapshot = queue.data
  const running = snapshot?.running ?? null
  const pending = Array.isArray(snapshot?.pending) ? snapshot.pending : []
  const paused = snapshot?.paused === true
  const runningId = running ? pipelineIdOf(running) : ''
  const runningCount = snapshot?.counts?.running ?? (running ? 1 : 0)
  const pendingCount = snapshot?.counts?.pending ?? pending.length
  const currentRow = currentPipeline(current.data)
  const currentId = currentRow ? pipelineIdOf(currentRow) : ''
  const names = useUntitledPipelineNames(running ? [running, ...pending] : pending)
  const titleOf = (row: PipelineRow) => explicitTitle(row) ?? names.get(pipelineIdOf(row)) ?? UNTITLED

  // 运行项换了而当前流水线的缓存还没跟上时，补读一次开始时间。
  const refetchCurrent = current.refetch
  useEffect(() => {
    if (runningId && currentId !== runningId) void refetchCurrent()
  }, [runningId, currentId, refetchCurrent])

  const refreshAll = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: keys.queue.all() }),
    queryClient.invalidateQueries({ queryKey: keys.pipelines.lists() }),
    queryClient.invalidateQueries({ queryKey: keys.pipelines.current() }),
  ])

  const toggle = useMutation({
    mutationFn: async (action: 'pause' | 'resume') => {
      const result = action === 'pause' ? await api.POST('/api/queue/pause') : await api.POST('/api/queue/resume')
      if (result.error) throw toApiError(result.error, result.response)
      return action
    },
    onSuccess: async (action) => {
      setNotice(null)
      queryClient.setQueryData<QueueSnapshot>(keys.queue.all(), (old) => (old ? { ...old, paused: action === 'pause' } : old))
      toast.success(action === 'pause' ? '队列已暂停；当前流水线会继续运行。' : '队列已恢复。')
      await queryClient.invalidateQueries({ queryKey: keys.queue.all() })
    },
    onError: (error) => setNotice({ tone: 'danger', message: `操作失败：${errorMessage(error)}` }),
  })

  const cancel = useMutation({
    mutationFn: async ({ id }: { id: string; message: string }) => cancelPipeline(id),
    onSuccess: async (_data, { message }) => {
      setNotice(null)
      toast.success(message)
      await refreshAll()
    },
    onError: (error) => setNotice({ tone: 'danger', message: `操作失败：${errorMessage(error)}` }),
  })

  const clear = useMutation({
    mutationFn: async () => {
      const { error, response } = await api.DELETE('/api/queue')
      if (error) throw toApiError(error, response)
    },
    onSuccess: async () => {
      setNotice(null)
      toast.success('已取消所有待执行条目。')
      await refreshAll()
    },
    onError: (error) => setNotice({ tone: 'danger', message: `操作失败：${errorMessage(error)}` }),
  })

  const promote = useMutation({
    mutationFn: async ({ targetId, currentRunningId }: { targetId: string; currentRunningId: string }) => {
      const promoted = await api.PATCH('/api/queue/{pipeline_id}', { params: { path: { pipeline_id: targetId } }, body: { priority: 0 } })
      if (promoted.error) throw toApiError(promoted.error, promoted.response)
      await cancelPipeline(currentRunningId)
    },
    onMutate: () => setNotice({ tone: 'info', message: '正在提升目标并请求取消当前流水线…' }),
    onSuccess: async () => {
      setNotice({ tone: 'success', message: '目标已调至优先级 0，当前流水线已请求取消；队列按显示顺序继续调度。' })
      await refreshAll()
    },
    onError: async (error) => {
      setNotice({ tone: 'danger', message: `操作未完整完成：${errorMessage(error)}。若优先级调整已成功，目标仍会留在队列中。` })
      await queryClient.invalidateQueries({ queryKey: keys.queue.all() })
    },
  })

  const busy = cancel.isPending || clear.isPending || promote.isPending

  const stopRunning = async () => {
    if (!running || !runningId) return
    const ok = await confirm({
      title: '停止当前流水线？',
      description: '正在执行的任务会被中断，已经完成的任务不受影响。',
      confirmText: '停止流水线',
      tone: 'danger',
    })
    if (ok) cancel.mutate({ id: runningId, message: '已请求停止当前流水线' })
  }

  const cancelPending = async (row: PipelineRow) => {
    const title = titleOf(row)
    const ok = await confirm({
      title: `取消「${title}」？`,
      description: '它会离开待执行队列，不再执行。',
      confirmText: '取消这条',
      cancelText: '保留',
      tone: 'danger',
    })
    if (ok) cancel.mutate({ id: pipelineIdOf(row), message: `已取消「${title}」` })
  }

  const jumpAhead = async (row: PipelineRow) => {
    const targetId = pipelineIdOf(row)
    if (!running || !runningId || !targetId) {
      setNotice({ tone: 'danger', message: '无法识别当前或目标流水线 ID，未执行操作。' })
      return
    }
    const ok = await confirm({
      title: '停止当前并执行此条？',
      description: `当前流水线「${titleOf(running)}」会被停止，「${titleOf(row)}」调到最高优先级。队列仍按优先级和入队时间调度，更早的最高优先级条目可能先运行，因此不能保证目标紧接当前项执行。`,
      confirmText: '停止并执行此条',
      tone: 'danger',
    })
    if (ok) promote.mutate({ targetId, currentRunningId: runningId })
  }

  const clearPending = async () => {
    const count = pending.length
    if (count === 0) return
    const ok = await confirm({
      title: '清空待执行？',
      description: `将取消 ${count} 条待执行流水线，正在运行的不受影响。`,
      confirmText: `清空 ${count} 条待执行`,
      tone: 'danger',
    })
    if (ok) clear.mutate()
  }

  if (queue.isPending) return <div className={card}><LoadingState label="正在读取队列…" /></div>
  if (queue.error && !snapshot) {
    return (
      <div className={card}>
        <ErrorState message={`无法读取队列：${errorMessage(queue.error)}`} onRetry={() => void queue.refetch()} retrying={queue.isFetching} />
      </div>
    )
  }

  const startedAt = runningId && currentId === runningId ? currentRow?.started_at : undefined

  return (
    <div className="space-y-6 pb-4">
      <div className="space-y-3">
        <section aria-label="队列状态" className={`${card} flex items-center gap-3 px-4 py-3`}>
          <StatusDot tone={paused ? 'warning' : 'success'} />
          <div className="min-w-0 flex-1">
            <p className="text-[0.9375rem] font-semibold">{paused ? '队列已暂停' : '队列运行中'}</p>
            <p className="mt-0.5 text-footnote tabular-nums text-muted-foreground">运行中 {runningCount} · 待执行 {pendingCount}</p>
          </div>
          <Button variant="outline" size="sm" loading={toggle.isPending} onClick={() => toggle.mutate(paused ? 'resume' : 'pause')}>
            {toggle.isPending ? null : paused ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}
            {paused ? '恢复队列' : '暂停队列'}
          </Button>
        </section>
        {queue.error ? (
          <InlineAlert tone="warning" action={<Button variant="outline" size="sm" onClick={() => void queue.refetch()}>重试</Button>}>
            队列更新失败：{errorMessage(queue.error)}
          </InlineAlert>
        ) : null}
        {notice ? <InlineAlert tone={notice.tone}>{notice.message}</InlineAlert> : null}
      </div>

      <Section id="queue-running" title="正在运行">
        {running ? (
          <div className={`${card} p-4`}>
            <div className="flex items-start gap-3">
              <StatusDot tone="primary" pulse className="mt-2" />
              <div className="min-w-0 flex-1">
                <p className="break-words text-[0.9375rem] font-semibold">{titleOf(running)}</p>
                <p className="mt-0.5 text-footnote text-muted-foreground">
                  {sourceLabel(running.source)} · {startedAt ? `开始于 ${formatTime(startedAt)}` : `入队于 ${formatTime(running.created_at)}`}
                </p>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button variant="destructive-soft" size="sm" disabled={busy} onClick={() => void stopRunning()}>
                <CircleStop aria-hidden="true" />
                停止
              </Button>
              {runningId ? <Button asChild variant="ghost" size="sm"><Link to={detailPath(runningId)}>查看详情</Link></Button> : null}
            </div>
          </div>
        ) : (
          <div className={card}>
            <EmptyState
              className="py-7"
              title="当前没有运行中的流水线"
              description={paused ? '队列已暂停，恢复后会继续执行待执行的流水线。' : '待执行的流水线会按顺序开始。'}
            />
          </div>
        )}
      </Section>

      <Section
        id="queue-pending"
        title="待执行"
        description={pending.length > 0 ? '按优先级与入队时间调度' : undefined}
        actions={(
          <>
            <Button variant="ghost" size="icon-sm" aria-label="刷新队列" onClick={() => void queue.refetch()}>
              {queue.isFetching ? <Spinner /> : <RefreshCw aria-hidden="true" />}
            </Button>
            <Button variant="ghost" size="sm" className="text-danger" disabled={pending.length === 0 || busy} onClick={() => void clearPending()}>
              清空待执行
            </Button>
          </>
        )}
      >
        {pending.length === 0 ? (
          <div className={card}><EmptyState className="py-7" icon={Inbox} title="没有待执行的流水线" description="新建任务或定时任务到点后会在这里排队。" /></div>
        ) : (
          <ol className={`${card} divide-y overflow-hidden`}>
            {pending.map((row, index) => {
              const id = pipelineIdOf(row)
              const title = titleOf(row)
              const status = normalizeStatus(row.status ?? 'pending')
              return (
                <li key={id || index} className="flex items-center pr-1.5">
                  <Link
                    to={detailPath(id)}
                    className="flex min-h-16 min-w-0 flex-1 items-center gap-3 py-3 pl-3 pr-2 transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                  >
                    <span aria-hidden="true" className="w-5 shrink-0 text-right text-footnote tabular-nums text-muted-foreground">{index + 1}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[0.9375rem] font-medium">{title}</span>
                      <span className="mt-0.5 block text-footnote text-muted-foreground">{sourceLabel(row.source)} · 优先级 {priorityLabel(row.priority)}</span>
                    </span>
                    <Badge variant="neutral">{statusLabel(status)}</Badge>
                  </Link>
                  <ActionMenu
                    label={`「${title}」的更多操作`}
                    title={title}
                    disabled={busy || !id}
                    actions={[
                      {
                        label: '停止当前并执行此条',
                        description: '调到最高优先级，并停止正在运行的流水线',
                        icon: ArrowUpToLine,
                        disabled: !running,
                        onSelect: () => void jumpAhead(row),
                      },
                      { label: '取消', description: '从队列移除，不再执行', icon: X, tone: 'danger', onSelect: () => void cancelPending(row) },
                    ]}
                  />
                </li>
              )
            })}
          </ol>
        )}
      </Section>
      {confirmElement}
    </div>
  )
}
