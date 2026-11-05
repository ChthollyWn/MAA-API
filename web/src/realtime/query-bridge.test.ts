import '@testing-library/jest-dom/vitest'
import { QueryClient } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { keys } from '@/api/keys'
import type { AgentRunEvent, ServerEvent } from '@/realtime/events'
import { handleServerEvent } from '@/realtime/query-bridge'
import { useLogBuffer } from '@/stores/log-buffer'
import { formatLogTime } from '@/features/logs/format'

/** 一条 `agent_run` 事件；字段默认值对应总控的一轮，按用例覆盖。 */
function agentRunEvent(data: Partial<AgentRunEvent> & Pick<AgentRunEvent, 'event'>): ServerEvent {
  return {
    type: 'agent_run',
    data: {
      run_id: 'run-1',
      session_id: 'session-1',
      ticket_id: null,
      agent: 'orchestrator',
      kind: 'orchestrator_turn',
      status: 'running',
      ...data,
    },
  }
}

/** 只保留列表真正渲染的字段，避免把缓冲区内部状态写进断言。 */
function bufferRows(source: string) {
  // 缓冲区攒批到下一帧才入 state，断言前先手动 flush。
  useLogBuffer.getState().flushPending()
  return useLogBuffer.getState().bySource[source] ?? []
}

describe('realtime log records carry a timestamp', () => {
  beforeEach(() => {
    useLogBuffer.getState().clear()
  })

  it("uses the envelope timestamp for live 'log' events", () => {
    const ts = Date.UTC(2026, 8, 27, 12, 30, 45) / 1000
    const queryClient = new QueryClient()

    // 服务端实时事件的形状：ts 在信封上，data 里没有 ts（见 maa_api/api/ws.py
    // 的 broadcast_log → _record_data）。日志列表只读 data，所以这一层必须把
    // 信封时间并入记录，否则列表渲染成「时间未知」。
    handleServerEvent(
      {
        type: 'log',
        ts,
        data: {
          id: 9042,
          stream_id: 'stream-1',
          source: 'service',
          level: 'INFO',
          content: 'HTTP GET /api/system/logs completed',
        },
      },
      queryClient,
    )

    const rows = bufferRows('service')
    expect(rows).toHaveLength(1)
    expect(rows[0].ts).toBe(ts)
    expect(formatLogTime(rows[0].ts)).not.toBe('时间未知')
  })

  it("keeps the per-record timestamp of 'log_batch' records", () => {
    const records = [
      { id: 1, source: 'core', level: 'INFO', content: 'a', ts: 1_790_000_001 },
      { id: 2, source: 'core', level: 'INFO', content: 'b', ts: 1_790_000_002 },
    ]
    const queryClient = new QueryClient()

    handleServerEvent(
      {
        type: 'log_batch',
        ts: 1_790_000_999,
        data: { records, truncated: false, stream_id: 'stream-1' },
      },
      queryClient,
    )

    expect(bufferRows('core').map((row) => row.ts)).toEqual([1_790_000_001, 1_790_000_002])
  })

  it('prefers the record timestamp when a live record already has one', () => {
    const queryClient = new QueryClient()

    handleServerEvent(
      {
        type: 'log',
        ts: 1_790_000_999,
        data: { id: 3, source: 'task', level: 'INFO', content: 'c', ts: 1_790_000_500 },
      },
      queryClient,
    )

    expect(bufferRows('task')[0].ts).toBe(1_790_000_500)
  })
})

describe('agent question events keep the pending question list fresh', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('invalidates pending questions on question_request and question_resolved', () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    for (const type of ['question_request', 'question_resolved'] as const) {
      handleServerEvent({ type, data: { question_id: 'q-1' } }, queryClient)
      // 失效是攒批的（250ms 窗口），推进计时器才会真正发出。
      vi.advanceTimersByTime(300)
    }

    expect(spy).toHaveBeenCalledWith({ queryKey: keys.agent.pendingQuestions(), refetchType: 'active' })
    // 提问不进确认链路：这条事件不该顺手把确认也刷一遍。
    expect(spy).not.toHaveBeenCalledWith({ queryKey: keys.agent.pendingConfirmations(), refetchType: 'active' })
  })

  it('leaves the confirmation invalidation exactly as it was', () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    handleServerEvent({ type: 'confirm_resolved', data: {} }, queryClient)
    vi.advanceTimersByTime(300)

    expect(spy).toHaveBeenCalledWith({ queryKey: keys.agent.pendingConfirmations(), refetchType: 'active' })
    expect(spy).not.toHaveBeenCalledWith({ queryKey: keys.agent.pendingQuestions(), refetchType: 'active' })
  })
})

describe('agent grant events refresh only the session detail', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('does not invalidate the message window while a turn is streaming (M59)', () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    for (const event of ['atomic_grant_changed', 'copilot_grant_changed'] as const) {
      handleServerEvent(
        {
          type: 'agent_event',
          data: { session_id: 'session-1', event, payload: { granted: true } },
        },
        queryClient,
      )
      vi.advanceTimersByTime(300)
    }

    expect(spy).toHaveBeenCalledWith({ queryKey: keys.agent.session('session-1'), refetchType: 'active' })
    // 直播中重取历史会把本轮已落库的消息带回窗口，和实时副本重复渲染。
    expect(spy).not.toHaveBeenCalledWith({ queryKey: keys.agent.messages('session-1'), refetchType: 'active' })
    expect(spy).not.toHaveBeenCalledWith({ queryKey: keys.agent.all(), refetchType: 'active' })
  })
})

describe('ticket events keep the ticket queries fresh', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('refreshes ticket lists, detail and reports on ticket_status events', () => {
    vi.useFakeTimers()

    const run = (event: 'ticket_created' | 'ticket_updated' | 'report_created') => {
      const queryClient = new QueryClient()
      const spy = vi.spyOn(queryClient, 'invalidateQueries')
      handleServerEvent(
        {
          type: 'ticket_status',
          data: {
            event,
            ticket_id: 'ticket-1',
            title: '日常',
            kind: 'daily_ops',
            status: 'done',
            last_report_status: 'ready',
            summary: '日常做完了',
            human_action: null,
          },
        },
        queryClient,
      )
      // 失效是攒批的（250ms 窗口），推进计时器才会真正发出。
      vi.advanceTimersByTime(300)
      return spy
    }

    const created = run('ticket_created')
    expect(created).toHaveBeenCalledWith({ queryKey: keys.agent.tickets.lists(), refetchType: 'active' })
    // 列表回应能拼出摘要，新建工单时还没有详情缓存可刷。
    expect(created).not.toHaveBeenCalledWith({
      queryKey: keys.agent.tickets.detail('ticket-1'),
      refetchType: 'active',
    })
    expect(created).not.toHaveBeenCalledWith({
      queryKey: keys.agent.tickets.reports('ticket-1'),
      refetchType: 'active',
    })

    const updated = run('ticket_updated')
    expect(updated).toHaveBeenCalledWith({ queryKey: keys.agent.tickets.lists(), refetchType: 'active' })
    expect(updated).toHaveBeenCalledWith({
      queryKey: keys.agent.tickets.detail('ticket-1'),
      refetchType: 'active',
    })
    expect(updated).not.toHaveBeenCalledWith({
      queryKey: keys.agent.tickets.reports('ticket-1'),
      refetchType: 'active',
    })

    const report = run('report_created')
    expect(report).toHaveBeenCalledWith({ queryKey: keys.agent.tickets.lists(), refetchType: 'active' })
    expect(report).toHaveBeenCalledWith({
      queryKey: keys.agent.tickets.detail('ticket-1'),
      refetchType: 'active',
    })
    expect(report).toHaveBeenCalledWith({
      queryKey: keys.agent.tickets.reports('ticket-1'),
      refetchType: 'active',
    })
    // 工单事件不进确认链路。
    expect(report).not.toHaveBeenCalledWith({ queryKey: keys.agent.pendingConfirmations(), refetchType: 'active' })
  })
})

describe('agent lease events refresh the device lease', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('refreshes the device lease on lease_changed', () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    handleServerEvent(
      {
        type: 'agent_event',
        data: {
          session_id: null,
          event: 'lease_changed',
          payload: {
            change: 'acquired',
            reason: null,
            held: true,
            lease: {
              lease_id: 'lease-1',
              device_id: 'device-1',
              ticket_id: 'ticket-1',
              ticket_title: '日常清理',
              holder_agent: 'worker',
              explicit: true,
            },
          },
        },
      },
      queryClient,
    )
    vi.advanceTimersByTime(300)

    expect(spy).toHaveBeenCalledWith({ queryKey: keys.agent.deviceLease(), refetchType: 'active' })
    expect(spy).toHaveBeenCalledWith({
      queryKey: keys.agent.tickets.detail('ticket-1'),
      refetchType: 'active',
    })
  })

  it('only invalidates the lease when the release carries no ticket', () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    handleServerEvent(
      {
        type: 'agent_event',
        data: {
          session_id: null,
          event: 'lease_changed',
          payload: { change: 'released', reason: 'user', held: false, lease: null },
        },
      },
      queryClient,
    )
    vi.advanceTimersByTime(300)

    expect(spy).toHaveBeenCalledWith({ queryKey: keys.agent.deviceLease(), refetchType: 'active' })
    expect(spy).not.toHaveBeenCalledWith({
      queryKey: keys.agent.tickets.detail('ticket-1'),
      refetchType: 'active',
    })
    expect(spy).not.toHaveBeenCalledWith({ queryKey: keys.agent.tickets.lists(), refetchType: 'active' })
  })
})

describe('agent run events refresh the conversation they belong to', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('invalidates the orchestrator state and messages when an orchestrator run finishes', () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    handleServerEvent(
      agentRunEvent({ event: 'run_finished', kind: 'orchestrator_turn', status: 'completed' }),
      queryClient,
    )
    vi.advanceTimersByTime(300)

    expect(spy).toHaveBeenCalledWith({
      queryKey: keys.agent.orchestrator.state(),
      refetchType: 'active',
    })
    expect(spy).toHaveBeenCalledWith({
      queryKey: keys.agent.orchestrator.messages(),
      refetchType: 'active',
    })
  })

  it('routes message_created by the cached orchestrator session id', () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient()
    // 路由写的用户消息没有运行，`kind` 是 null，只能靠总控状态里的 session_id 认。
    queryClient.setQueryData(keys.agent.orchestrator.state(), { session_id: 'orch-1' })
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    handleServerEvent(
      agentRunEvent({ event: 'message_created', kind: null, session_id: 'orch-1' }),
      queryClient,
    )
    vi.advanceTimersByTime(300)

    expect(spy).toHaveBeenCalledWith({
      queryKey: keys.agent.orchestrator.messages(),
      refetchType: 'active',
    })
    // message_created 不改变总控状态。
    expect(spy).not.toHaveBeenCalledWith({
      queryKey: keys.agent.orchestrator.state(),
      refetchType: 'active',
    })
  })

  it('invalidates the ticket scope queries for worker runs', () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    handleServerEvent(
      agentRunEvent({
        event: 'run_finished',
        kind: 'worker',
        agent: 'worker',
        session_id: 'worker-session',
        ticket_id: 'ticket-1',
      }),
      queryClient,
    )
    vi.advanceTimersByTime(300)

    expect(spy).toHaveBeenCalledWith({
      queryKey: keys.agent.tickets.detail('ticket-1'),
      refetchType: 'active',
    })
    expect(spy).toHaveBeenCalledWith({
      queryKey: keys.agent.tickets.runs('ticket-1'),
      refetchType: 'active',
    })
    expect(spy).toHaveBeenCalledWith({
      queryKey: keys.agent.tickets.messagesAll('ticket-1'),
      refetchType: 'active',
    })
    // 工单详情与消息各取各的，不整片失效工单 scope，也不碰总控会话。
    expect(spy).not.toHaveBeenCalledWith({
      queryKey: keys.agent.tickets.scope('ticket-1'),
      refetchType: 'active',
    })
    expect(spy).not.toHaveBeenCalledWith({
      queryKey: keys.agent.orchestrator.messages(),
      refetchType: 'active',
    })
  })
})

describe('reconnect reloads the v3 conversation state', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('reloads v3 conversation state after a reconnect', () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    handleServerEvent(
      { type: 'subscribed', data: { channels: ['agent_run', 'ticket_status'], backfilled: 3 } },
      queryClient,
    )
    vi.advanceTimersByTime(300)

    expect(spy).toHaveBeenCalledWith({ queryKey: keys.agent.orchestrator.all(), refetchType: 'active' })
    expect(spy).toHaveBeenCalledWith({ queryKey: keys.agent.tickets.lists(), refetchType: 'active' })
    expect(spy).toHaveBeenCalledWith({ queryKey: keys.agent.deviceLease(), refetchType: 'active' })
  })
})

describe('compaction marks the checkpoint list stale', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('only invalidates the checkpoints key of the compacted session', () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    handleServerEvent(
      {
        type: 'agent_event',
        data: {
          session_id: 'session-1',
          event: 'compaction_created',
          payload: {
            checkpoint_id: 'c-9',
            run_id: 'r-7',
            trigger: 'auto',
            tokens_before: 98213,
            tokens_after: 21455,
            upto_seq: 412,
            degraded: false,
          },
        },
      },
      queryClient,
    )
    vi.advanceTimersByTime(300)

    expect(spy).toHaveBeenCalledWith({
      queryKey: keys.agent.checkpoints('session-1'),
      refetchType: 'active',
    })
    // M74 §4.6：压缩不失效消息窗口与整片会话，避免直播中重复渲染（M59）。
    expect(spy).not.toHaveBeenCalledWith({
      queryKey: keys.agent.messages('session-1'),
      refetchType: 'active',
    })
    expect(spy).not.toHaveBeenCalledWith({ queryKey: keys.agent.all(), refetchType: 'active' })
    expect(spy).not.toHaveBeenCalledWith({ queryKey: keys.agent.checkpoints('session-2'), refetchType: 'active' })
  })
})

describe('memory proposals refresh the attention and memory queries', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('invalidates the memory prefix on memory_proposed', () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    handleServerEvent(
      {
        type: 'agent_event',
        data: {
          session_id: 'main',
          event: 'memory_proposed',
          payload: { id: 'm-1', kind: 'semantic', preview: '刷图不要用源石恢复理智' },
        },
      },
      queryClient,
    )
    vi.advanceTimersByTime(300)

    // 待你处理的待审核清单与记忆页在同一个前缀下，一次失效两处（M79 §4.6）。
    expect(spy).toHaveBeenCalledWith({
      queryKey: keys.agent.memories(),
      refetchType: 'active',
    })
    expect(spy).not.toHaveBeenCalledWith({ queryKey: keys.agent.all(), refetchType: 'active' })
  })
})

describe('workspace events refresh the plan and artifact queries', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('invalidates only the plan of the updated session', () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    handleServerEvent(
      {
        type: 'agent_event',
        data: {
          session_id: 'w-1',
          event: 'plan_updated',
          payload: {
            run_id: null,
            plan_id: 'p-1',
            status: 'awaiting_approval',
            items: [{ id: 'a1', title: '提交剿灭', status: 'pending', note: '' }],
          },
        },
      },
      queryClient,
    )
    vi.advanceTimersByTime(300)

    expect(spy).toHaveBeenCalledWith({
      queryKey: keys.agent.plan('w-1'),
      refetchType: 'active',
    })
    // M75 §4.6：计划不动消息窗口、工单详情与整片会话，避免直播中重复渲染（M59）。
    expect(spy).not.toHaveBeenCalledWith({ queryKey: keys.agent.plan('w-2'), refetchType: 'active' })
    expect(spy).not.toHaveBeenCalledWith({ queryKey: keys.agent.all(), refetchType: 'active' })
    expect(spy).not.toHaveBeenCalledWith({
      queryKey: keys.agent.tickets.detail('t-1'),
      refetchType: 'active',
    })
  })

  it('invalidates every artifact list because the payload has no ticket_id', () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    handleServerEvent(
      {
        type: 'agent_event',
        data: {
          session_id: 'w-1',
          event: 'artifact_created',
          payload: { run_id: 'r-1', artifact_id: 'a-1', kind: 'spill', title: 'get_logs 的输出' },
        },
      },
      queryClient,
    )
    vi.advanceTimersByTime(300)

    expect(spy).toHaveBeenCalledWith({
      queryKey: keys.agent.artifactsAll(),
      refetchType: 'active',
    })
  })
})

describe('session_rewound refreshes the rewound session messages', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('invalidates the session messages and the mounted ticket message window', () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')
    // 工单消息窗口的 key 是工单 id：从已缓存的工单详情反查 Worker 会话。
    queryClient.setQueryData(keys.agent.tickets.detail('t-1'), { workerSessionId: 'w-1' })

    handleServerEvent(
      {
        type: 'agent_event',
        data: {
          session_id: 'w-1',
          event: 'session_rewound',
          payload: { session_id: 'w-1', to_seq: 3, superseded: 2, notice_message_id: 10 },
        },
      },
      queryClient,
    )
    vi.advanceTimersByTime(300)

    // M80 §4.6：该会话的消息窗口重拉；不整片失效 agent 前缀（M59）。
    expect(spy).toHaveBeenCalledWith({
      queryKey: keys.agent.messages('w-1'),
      refetchType: 'active',
    })
    expect(spy).toHaveBeenCalledWith({
      queryKey: keys.agent.tickets.messagesAll('t-1'),
      refetchType: 'active',
    })
    expect(spy).not.toHaveBeenCalledWith({ queryKey: keys.agent.all(), refetchType: 'active' })
  })

  it('invalidates the orchestrator message window when the session matches', () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')
    queryClient.setQueryData(keys.agent.orchestrator.state(), { session_id: 'main' })

    handleServerEvent(
      {
        type: 'agent_event',
        data: {
          session_id: 'main',
          event: 'session_rewound',
          payload: { session_id: 'main', to_seq: 1, superseded: 3, notice_message_id: null },
        },
      },
      queryClient,
    )
    vi.advanceTimersByTime(300)

    expect(spy).toHaveBeenCalledWith({
      queryKey: keys.agent.orchestrator.messages(),
      refetchType: 'active',
    })
    expect(spy).toHaveBeenCalledWith({
      queryKey: keys.agent.orchestrator.state(),
      refetchType: 'active',
    })
    expect(spy).not.toHaveBeenCalledWith({
      queryKey: keys.agent.tickets.detail('t-1'),
      refetchType: 'active',
    })
  })
})

describe('observation_created refreshes observations and trigger rules', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('invalidates the observations prefix and the rule list', () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    handleServerEvent(
      {
        type: 'agent_event',
        data: {
          session_id: null,
          event: 'observation_created',
          payload: {
            observation_id: 12,
            trigger_id: 345,
            rule_id: 'sanity-full',
            summary: '理智估计已满（135/135）',
            created_at: '2026-11-20T01:00:00Z',
          },
        },
      },
      queryClient,
    )
    vi.advanceTimersByTime(300)

    // M81 §4.6：待审阅与已审阅两份观察记录缓存都在这个前缀下，规则统计也要重取。
    expect(spy).toHaveBeenCalledWith({
      queryKey: keys.agent.observationsAll(),
      refetchType: 'active',
    })
    expect(spy).toHaveBeenCalledWith({
      queryKey: keys.agent.triggerRules(),
      refetchType: 'active',
    })
    expect(spy).not.toHaveBeenCalledWith({ queryKey: keys.agent.all(), refetchType: 'active' })
  })
})
