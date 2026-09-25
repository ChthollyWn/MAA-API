import type { QueryClient, QueryKey } from '@tanstack/react-query'
import { keys } from '@/api/keys'
import { useLogBuffer } from '@/stores/log-buffer'
import type { ServerEvent } from '@/realtime/events'

const INVALIDATION_WINDOW_MS = 250
const invalidations = new Map<string, ReturnType<typeof setTimeout>>()

function invalidateBatched(queryClient: QueryClient, queryKey: QueryKey): void {
  const key = JSON.stringify(queryKey)
  const pending = invalidations.get(key)
  if (pending !== undefined) clearTimeout(pending)
  invalidations.set(
    key,
    setTimeout(() => {
      invalidations.delete(key)
      void queryClient.invalidateQueries({ queryKey, refetchType: 'active' })
    }, INVALIDATION_WINDOW_MS),
  )
}

function isTerminal(status: string): boolean {
  return ['completed', 'failed', 'cancelled'].includes(status)
}

function patchTaskStatus(queryClient: QueryClient, task: Record<string, unknown>): void {
  queryClient.setQueryData<unknown>(keys.pipelines.current(), (old: unknown) => {
    if (!old || typeof old !== 'object') return old
    const root = old as Record<string, unknown>
    const pipeline = root.pipeline && typeof root.pipeline === 'object'
      ? root.pipeline as Record<string, unknown>
      : root
    const tasks = pipeline.tasks
    if (!Array.isArray(tasks)) return old

    const updatedTasks = tasks.map((entry) => {
      if (!entry || typeof entry !== 'object') return entry
      const item = entry as Record<string, unknown>
      if (item.task_id !== task.task_id && item.id !== task.task_id) return entry
      return { ...item, ...task }
    })
    const updatedPipeline = { ...pipeline, tasks: updatedTasks }
    return pipeline === root ? updatedPipeline : { ...root, pipeline: updatedPipeline }
  })
}

export function handleServerEvent(event: ServerEvent, queryClient: QueryClient): void {
  switch (event.type) {
    case 'log':
      useLogBuffer.getState().append(event.data)
      break
    case 'log_batch':
      useLogBuffer.getState().appendMany(event.data.records)
      break
    case 'core_status':
      queryClient.setQueryData<Record<string, unknown>>(keys.system.health(), (old) =>
        old ? { ...old, core: event.data } : old,
      )
      break
    case 'device_status':
      queryClient.setQueryData<Record<string, unknown>>(keys.system.health(), (old) =>
        old ? { ...old, device: event.data } : old,
      )
      break
    case 'pipeline_status':
      queryClient.setQueryData<Record<string, unknown>>(
        keys.pipelines.current(),
        (old) => ({ ...(old ?? {}), pipeline: isTerminal(event.data.status) ? null : event.data }),
      )
      if (isTerminal(event.data.status)) {
        invalidateBatched(queryClient, keys.pipelines.lists())
        invalidateBatched(queryClient, keys.queue.all())
      }
      break
    case 'task_status':
      patchTaskStatus(queryClient, event.data)
      break
    case 'queue_changed':
      invalidateBatched(queryClient, keys.queue.all())
      break
    case 'confirm_request':
    case 'confirm_resolved':
      invalidateBatched(queryClient, keys.agent.pendingConfirmations())
      invalidateBatched(queryClient, keys.agent.audits())
      break
    case 'update_progress':
    case 'update_available':
      invalidateBatched(queryClient, keys.updates.status())
      break
    case 'agent_event':
      if (event.data.event === 'atomic_grant_changed') {
        queryClient.invalidateQueries({ queryKey: keys.agent.all(), refetchType: 'active' })
      }
      break
    case 'subscribed':
      // Reconnect acknowledgement follows a possible WS gap; reload persisted
      // approval/audit state so missed terminal events cannot leave stale cards.
      invalidateBatched(queryClient, keys.agent.pendingConfirmations())
      invalidateBatched(queryClient, keys.agent.audits())
      break
    case 'server_shutdown':
    case 'pong':
    case 'error':
      break
  }
}
