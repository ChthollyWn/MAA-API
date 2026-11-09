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
      // 实时 log 事件的 ts 在信封上，data 里没有（见 maa_api/api/ws.py 的
      // broadcast_log）。日志列表只读 data，所以要把信封时间并进记录，否则新到的行
      // 渲染成「时间未知」；记录自带 ts 时以记录为准，与 log_batch 的口径一致。
      useLogBuffer.getState().append(
        event.data.ts === undefined && event.ts !== undefined
          ? { ...event.data, ts: event.ts }
          : event.data,
      )
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
    // Agent 提问：只为多标签页同步待答卡，不进全局「待确认」面板与标题栏胶囊。
    case 'question_request':
    case 'question_resolved':
      invalidateBatched(queryClient, keys.agent.pendingQuestions())
      break
    case 'update_progress':
    case 'update_available':
      invalidateBatched(queryClient, keys.updates.status())
      break
    case 'agent_event':
      if (event.data.event === 'compaction_created') {
        // M74 §4.6：压缩只影响检查点标记。不失效消息窗口——直播中整片失效会把本轮
        // 已落库的消息带回历史，与实时副本重复渲染（M59，见 09 §6.4）。
        const sessionId = event.data.session_id
        if (typeof sessionId === 'string' && sessionId) {
          invalidateBatched(queryClient, keys.agent.checkpoints(sessionId))
        }
      } else if (event.data.event === 'plan_updated') {
        // M75 §4.6：计划只随该会话的 plan 查询失效；工单详情与消息窗口不动。
        const sessionId = event.data.session_id
        if (typeof sessionId === 'string' && sessionId) {
          invalidateBatched(queryClient, keys.agent.plan(sessionId))
        }
      } else if (event.data.event === 'artifact_created') {
        // M75 §4.6：载荷只有 session_id、没有 ticket_id（04 §4.2），按工单分段的
        // 列表没法精确定位，只能失效所有已挂载的产物列表。
        invalidateBatched(queryClient, keys.agent.artifactsAll())
      } else if (event.data.event === 'memory_proposed') {
        // M79 §4.6：待你处理的「N 条记忆待审核」与记忆页共用同一个查询前缀，
        // 一次失效两处；不碰消息窗口与工单。
        invalidateBatched(queryClient, keys.agent.memories())
      } else if (event.data.event === 'session_rewound') {
        // M80 §4.6：回溯已经提交，其他标签页重拉该会话的消息窗口。回溯只在没有活动
        // 运行、没有待确认时成功（§4.5.7），所以不会与直播中的实时副本重复渲染（M59）。
        const sessionId = event.data.session_id
        if (typeof sessionId === 'string' && sessionId) {
          invalidateBatched(queryClient, keys.agent.messages(sessionId))
          const orchestrator = queryClient.getQueryData<{ session_id?: string }>(
            keys.agent.orchestrator.state(),
          )
          if (orchestrator?.session_id === sessionId) {
            invalidateBatched(queryClient, keys.agent.orchestrator.messages())
            invalidateBatched(queryClient, keys.agent.orchestrator.state())
          }
          // 工单消息窗口的 key 是工单 id，不是会话 id：从已缓存的工单详情反查。
          for (const [queryKey, data] of queryClient.getQueriesData<{ workerSessionId?: string | null }>({
            queryKey: keys.agent.tickets.all(),
          })) {
            if (data?.workerSessionId !== sessionId) continue
            if (queryKey[3] !== 'detail' || typeof queryKey[4] !== 'string') continue
            invalidateBatched(queryClient, keys.agent.tickets.messagesAll(queryKey[4]))
            invalidateBatched(queryClient, keys.agent.tickets.detail(queryKey[4]))
          }
        }
      } else if (event.data.event === 'lease_changed') {
        // 设备租约（M71 §4.3）：租约条与持有者工单的详情各取各的。payload.lease 在
        // 释放/过期时可能是 null，所以只有拿得到 ticket_id 才顺带失效工单详情。
        invalidateBatched(queryClient, keys.agent.deviceLease())
        const payload = event.data.payload
        const lease = payload && typeof payload === 'object'
          ? (payload as Record<string, unknown>).lease
          : null
        const ticketId = lease && typeof lease === 'object'
          ? (lease as Record<string, unknown>).ticket_id
          : null
        if (typeof ticketId === 'string' && ticketId) {
          invalidateBatched(queryClient, keys.agent.tickets.detail(ticketId))
        }
      } else if (event.data.event === 'watchdog_alert') {
        // M83 §4.6：新告警同时改值守页与待你处理的严重告警两处；两者共用
        // `watchdogAlerts` 前缀，一次失效即可。状态里的模式与上次巡检时间不跟着动。
        invalidateBatched(queryClient, keys.agent.watchdogAlertsAll())
      } else if (event.data.event === 'observation_created') {
        // M81 §4.6：新观察记录同时改「待审阅」列表与规则行的 30 天统计。载荷不带
        // reviewed，两份观察记录缓存都失效；规则整片失效一次即可。
        invalidateBatched(queryClient, keys.agent.observationsAll())
        invalidateBatched(queryClient, keys.agent.triggerRules())
      }
      break
    // 工单状态：列表、详情、汇报三段各取各的；不整片失效 `keys.agent.all()`。
    case 'ticket_status':
      invalidateBatched(queryClient, keys.agent.tickets.lists())
      // M82 §4.6：工单状态变化可能改目标的规划状态与进度，列表整片失效；
      // 详情页有自己的 30 秒轮询，不在这里额外重取。
      invalidateBatched(queryClient, keys.agent.goals())
      // 新建工单时列表里还没有详情缓存；其余两个事件都可能改摘要与汇报。
      if (event.data.event !== 'ticket_created') {
        invalidateBatched(queryClient, keys.agent.tickets.detail(event.data.ticket_id))
      }
      if (event.data.event === 'report_created') {
        invalidateBatched(queryClient, keys.agent.tickets.reports(event.data.ticket_id))
      }
      break
    // Agent 运行事件（M72 §4.5.7）：总控会话与工单两套页面各自只看自己的那段。
    case 'agent_run': {
      const data = event.data
      const orchestrator = queryClient.getQueryData<{ session_id?: string }>(
        keys.agent.orchestrator.state(),
      )
      // `kind` 可能是 null（路由写的用户消息）；这时靠缓存里的总控 session_id 认。
      const isOrchestrator = data.kind === 'orchestrator_turn'
        || (Boolean(data.session_id) && data.session_id === orchestrator?.session_id)
      if (isOrchestrator) {
        // message_created 只改消息窗口，不动状态；run_started 不刷新消息（避免刚发出的
        // 用户消息被历史重取回来，与乐观气泡重复）。
        if (data.event !== 'message_created') {
          invalidateBatched(queryClient, keys.agent.orchestrator.state())
        }
        if (data.event !== 'run_started') {
          invalidateBatched(queryClient, keys.agent.orchestrator.messages())
        }
      }
      if (data.ticket_id) {
        if (data.event !== 'message_created') {
          invalidateBatched(queryClient, keys.agent.tickets.detail(data.ticket_id))
          invalidateBatched(queryClient, keys.agent.tickets.runs(data.ticket_id))
        }
        if (data.event !== 'run_started') {
          invalidateBatched(queryClient, keys.agent.tickets.messagesAll(data.ticket_id))
        }
      }
      break
    }
    case 'subscribed':
      // Reconnect acknowledgement follows a possible WS gap; reload persisted
      // approval/audit state so missed terminal events cannot leave stale cards.
      invalidateBatched(queryClient, keys.agent.pendingConfirmations())
      invalidateBatched(queryClient, keys.agent.pendingQuestions())
      invalidateBatched(queryClient, keys.agent.audits())
      // M72：v3 会话状态与工单列表、设备租约也靠 WS，断线期间的终态要补读。
      invalidateBatched(queryClient, keys.agent.orchestrator.all())
      invalidateBatched(queryClient, keys.agent.tickets.lists())
      invalidateBatched(queryClient, keys.agent.deviceLease())
      break
    case 'server_shutdown':
    case 'pong':
    case 'error':
      break
  }
}
