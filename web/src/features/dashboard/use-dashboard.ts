import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'

type RecordValue = Record<string, unknown>

export interface PipelineTaskView {
  id: string
  name: string
  status: string
  durationSeconds: number | null
}

export interface PipelineView {
  id: string | null
  title: string | null
  status: string
  total: number
  completed: number
  failed: number
  tasks: PipelineTaskView[]
}

export interface QueueView {
  pending: number
  running: number
  paused: boolean
}

function isRecord(value: unknown): value is RecordValue {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function pipelineRecord(value: unknown): RecordValue | null {
  if (!isRecord(value)) return null
  if ('pipeline' in value) {
    return isRecord(value.pipeline) ? value.pipeline : null
  }
  // The realtime bridge stores pipeline_status payloads directly at this key.
  return value
}

export function isPipelineRunning(value: unknown): boolean {
  return pipelineRecord(value)?.status === 'running'
}

export function readPipeline(value: unknown): PipelineView | null {
  const pipeline = pipelineRecord(value)
  if (!pipeline || typeof pipeline.status !== 'string') return null

  const tasks = Array.isArray(pipeline.tasks)
    ? pipeline.tasks.flatMap((entry, index): PipelineTaskView[] => {
        if (!isRecord(entry)) return []
        const id = typeof entry.id === 'string'
          ? entry.id
          : typeof entry.task_id === 'string'
            ? entry.task_id
            : String(index)
        const name = typeof entry.task_name === 'string' && entry.task_name
          ? entry.task_name
          : typeof entry.type_name === 'string'
            ? entry.type_name
            : `任务 ${index + 1}`
        return [{
          id,
          name,
          status: typeof entry.status === 'string' ? entry.status : 'pending',
          durationSeconds: finiteNumber(entry.duration_seconds),
        }]
      })
    : []

  const progress = isRecord(pipeline.progress) ? pipeline.progress : null
  const total = finiteNumber(progress?.total) ?? finiteNumber(pipeline.task_count) ?? tasks.length
  const completedFromTasks = tasks.filter((task) => task.status === 'completed').length
  const failedFromTasks = tasks.filter((task) => ['failed', 'skipped'].includes(task.status)).length

  return {
    id: typeof pipeline.id === 'string'
      ? pipeline.id
      : typeof pipeline.pipeline_id === 'string'
        ? pipeline.pipeline_id
        : null,
    title: typeof pipeline.title === 'string' && pipeline.title.trim() ? pipeline.title : null,
    status: pipeline.status,
    total,
    completed: tasks.length
      ? completedFromTasks
      : finiteNumber(progress?.completed) ?? 0,
    failed: tasks.length
      ? failedFromTasks
      : finiteNumber(progress?.failed) ?? 0,
    tasks,
  }
}

export function readQueue(value: unknown, fallback?: QueueView | null): QueueView | null {
  if (!isRecord(value)) return fallback ?? null
  const counts = isRecord(value.counts) ? value.counts : null
  const pendingItems = Array.isArray(value.pending) ? value.pending : null
  const pending = finiteNumber(counts?.pending) ?? pendingItems?.length ?? fallback?.pending ?? 0
  const running = finiteNumber(counts?.running)
    ?? (value.running != null ? 1 : 0)
  return {
    pending,
    running,
    paused: typeof value.paused === 'boolean' ? value.paused : fallback?.paused ?? false,
  }
}

export function useDashboard() {
  const healthQuery = useQuery({
    queryKey: keys.system.health(),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/system/health')
      if (error) throw toApiError(error, response)
      return data
    },
  })

  const pipelineQuery = useQuery({
    queryKey: keys.pipelines.current(),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/pipelines/current')
      if (error) throw toApiError(error, response)
      return data
    },
    refetchInterval: (query) => isPipelineRunning(query.state.data) ? 5_000 : false,
  })

  const queueQuery = useQuery({
    queryKey: keys.queue.all(),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/queue')
      if (error) throw toApiError(error, response)
      return data
    },
  })

  const healthQueue = healthQuery.data?.queue
  const queueFallback: QueueView | null = healthQueue
    ? { pending: healthQueue.pending, running: healthQueue.running, paused: healthQueue.paused }
    : null

  return {
    healthQuery,
    pipelineQuery,
    queueQuery,
    pipeline: readPipeline(pipelineQuery.data),
    pipelineRunning: isPipelineRunning(pipelineQuery.data),
    queue: readQueue(queueQuery.data, queueFallback),
    deviceConnected: healthQuery.data?.device?.state === 'connected',
  }
}
