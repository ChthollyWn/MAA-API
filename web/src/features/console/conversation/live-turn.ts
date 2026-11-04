import { isRecord } from '@/features/agent/format'
import type { AgentQuestionPrompt, ShownError, ToolTrace } from '@/features/agent/types'
import { splitReferenceBlock } from './references'
import type { SseV3Event } from './sse'
import { isInboxEvent } from './transcript-v3'
import type { HumanAction, Origin, V3Message } from './types'

export type LiveSegment =
  | { kind: 'text'; text: string }
  | { kind: 'tool'; toolCallId: string }
  | { kind: 'report'; reportId: number; status: string; summary: string; humanAction: HumanAction | null }
  | { kind: 'ticket'; ticketId: string; title: string }

export interface LiveRun {
  runId: string
  parentRunId: string | null
  agent: string
  /** 本端是否看到过这个运行的 `run_start`（/`subagent_start`）；没看到就是中途加入的。 */
  observedStart: boolean
  status: 'running' | 'succeeded' | 'interrupted' | 'failed'
  finishReason: string | null
  segments: LiveSegment[]
  tools: Record<string, ToolTrace>
  /** delegate 的 toolCallId → 子运行 id（结果里的 `run_id` 优先，其次按参数配对）。 */
  delegateRuns: Record<string, string>
  /** 子运行的任务原文（来自 `subagent_start`）。 */
  task: string | null
  /** `subagent_end` 的摘要。 */
  summary: string | null
}

export interface LiveTurn {
  /** 'orchestrator' 或 'ticket:<id>'（M72 §4.5.2）。 */
  scopeKey: string
  userText: string
  /** 发送时已加载历史的最后一条 id，没有历史为 0。 */
  baseMessageId: number
  rootOrder: string[]
  runs: Record<string, LiveRun>
  pendingConfirmation: { confirmationId: string; runId: string; toolCallId: string; origin: Origin | null } | null
  pendingQuestion: (AgentQuestionPrompt & { runId: string; origin: Origin | null }) | null
  running: boolean
  interrupted: boolean
  error: ShownError | null
}

function blankRun(runId: string, overrides: Partial<LiveRun> = {}): LiveRun {
  return {
    runId,
    parentRunId: null,
    agent: '',
    observedStart: false,
    status: 'running',
    finishReason: null,
    segments: [],
    tools: {},
    delegateRuns: {},
    task: null,
    summary: null,
    ...overrides,
  }
}

export function createLiveTurn(scopeKey: string, userText: string, baseMessageId: number): LiveTurn {
  return {
    scopeKey,
    userText,
    baseMessageId,
    rootOrder: [],
    runs: {},
    pendingConfirmation: null,
    pendingQuestion: null,
    running: true,
    interrupted: false,
    error: null,
  }
}

function replaceRun(turn: LiveTurn, run: LiveRun): LiveTurn {
  return { ...turn, runs: { ...turn.runs, [run.runId]: run } }
}

/**
 * 取运行；不存在就按「中途加入」新建并追加到 `rootOrder`（§4.5.2 的通用规则）。
 * `run_start`、`subagent_start` 的创建规则不同，各自处理。
 */
function ensureRun(turn: LiveTurn, runId: string): { turn: LiveTurn; run: LiveRun } {
  const existing = turn.runs[runId]
  if (existing) return { turn, run: existing }
  const run = blankRun(runId)
  return {
    turn: { ...turn, runs: { ...turn.runs, [runId]: run }, rootOrder: [...turn.rootOrder, runId] },
    run,
  }
}

function subagentStatus(status: string): LiveRun['status'] {
  if (status === 'succeeded') return 'succeeded'
  if (status === 'interrupted' || status === 'cancelled') return 'interrupted'
  return 'failed'
}

const INTERRUPTED_FINISH_REASONS: ReadonlySet<string> = new Set(['interrupted', 'recovered_after_restart', 'cancelled'])
const FAILED_FINISH_REASONS: ReadonlySet<string> = new Set(['error', 'stuck_loop', 'budget_exceeded', 'context_overflow'])

function finishStatus(reason: string): LiveRun['status'] {
  if (INTERRUPTED_FINISH_REASONS.has(reason)) return 'interrupted'
  if (FAILED_FINISH_REASONS.has(reason)) return 'failed'
  return 'succeeded'
}

function delegateCallMatches(trace: ToolTrace | undefined, agent: string, task: string): boolean {
  if (!trace || trace.name !== 'delegate' || !isRecord(trace.arguments)) return false
  return trace.arguments.agent === agent && trace.arguments.task === task
}

/** 实时副本 reducer：纯函数，始终返回新对象，不改入参（§4.5.2）。 */
export function reduceLiveTurn(turn: LiveTurn, event: SseV3Event): LiveTurn {
  switch (event.type) {
    case 'run_start': {
      const existing = turn.runs[event.runId]
      if (!existing) {
        const run = blankRun(event.runId, {
          observedStart: true,
          parentRunId: event.parentRunId,
          agent: event.agent,
        })
        const parentKnown = event.parentRunId !== null && Boolean(turn.runs[event.parentRunId])
        return {
          ...turn,
          runs: { ...turn.runs, [event.runId]: run },
          rootOrder: parentKnown ? turn.rootOrder : [...turn.rootOrder, event.runId],
        }
      }
      const run: LiveRun = {
        ...existing,
        observedStart: true,
        parentRunId: event.parentRunId ?? existing.parentRunId,
        agent: event.agent || existing.agent,
      }
      const parentKnown = event.parentRunId !== null && Boolean(turn.runs[event.parentRunId])
      const rootOrder = !parentKnown && !turn.rootOrder.includes(event.runId)
        ? [...turn.rootOrder, event.runId]
        : turn.rootOrder
      return { ...replaceRun(turn, run), rootOrder }
    }

    case 'text_delta': {
      if (!event.text) return turn
      const { turn: base, run } = ensureRun(turn, event.runId)
      const last = run.segments.at(-1)
      const segments: LiveSegment[] = last?.kind === 'text'
        ? [...run.segments.slice(0, -1), { kind: 'text', text: last.text + event.text }]
        : [...run.segments, { kind: 'text', text: event.text }]
      return replaceRun(base, { ...run, segments })
    }

    case 'tool_start': {
      const { turn: base, run } = ensureRun(turn, event.runId)
      if (run.tools[event.toolCallId]) return base
      const trace: ToolTrace = {
        toolCallId: event.toolCallId,
        name: event.name,
        arguments: event.arguments,
      }
      return replaceRun(base, {
        ...run,
        tools: { ...run.tools, [event.toolCallId]: trace },
        segments: [...run.segments, { kind: 'tool', toolCallId: event.toolCallId }],
      })
    }

    case 'tool_result': {
      const { turn: base, run } = ensureRun(turn, event.runId)
      const existing = run.tools[event.toolCallId]
      const trace: ToolTrace = {
        toolCallId: event.toolCallId,
        name: event.name || existing?.name || '',
        arguments: existing?.arguments,
        result: event.raw,
      }
      if (existing?.confirmationId) trace.confirmationId = existing.confirmationId
      if (event.auditId !== null) trace.auditId = event.auditId
      else if (existing?.auditId !== undefined) trace.auditId = existing.auditId

      let delegateRuns = run.delegateRuns
      const result = isRecord(event.raw.result) ? event.raw.result : null
      if (event.name === 'delegate' && typeof result?.run_id === 'string') {
        delegateRuns = { ...delegateRuns, [event.toolCallId]: result.run_id }
      }

      let pendingConfirmation = turn.pendingConfirmation
      if (pendingConfirmation?.toolCallId === event.toolCallId) pendingConfirmation = null
      let pendingQuestion = turn.pendingQuestion
      if (event.name === 'ask_user' && pendingQuestion && result?.question_id === pendingQuestion.question_id) {
        pendingQuestion = null
      }

      return {
        ...replaceRun(base, { ...run, tools: { ...run.tools, [event.toolCallId]: trace }, delegateRuns }),
        pendingConfirmation,
        pendingQuestion,
      }
    }

    case 'confirmation_required': {
      const { turn: base, run } = ensureRun(turn, event.runId)
      const existing = run.tools[event.toolCallId]
      const trace: ToolTrace = existing
        ? { ...existing, confirmationId: event.confirmationId }
        : { toolCallId: event.toolCallId, name: '', confirmationId: event.confirmationId }
      return {
        ...replaceRun(base, { ...run, tools: { ...run.tools, [event.toolCallId]: trace } }),
        pendingConfirmation: {
          confirmationId: event.confirmationId,
          runId: event.runId,
          toolCallId: event.toolCallId,
          origin: event.origin,
        },
      }
    }

    case 'question_required': {
      const { turn: base } = ensureRun(turn, event.runId)
      return {
        ...base,
        pendingQuestion: { ...event.question, runId: event.runId, origin: event.origin },
      }
    }

    case 'subagent_start': {
      let base = turn
      const existing = base.runs[event.runId]
      if (!existing) {
        const run = blankRun(event.runId, {
          observedStart: true,
          parentRunId: event.parentRunId,
          agent: event.agent,
          task: event.task,
        })
        const parentKnown = Boolean(base.runs[event.parentRunId])
        base = {
          ...base,
          runs: { ...base.runs, [event.runId]: run },
          // 父运行不在本端副本里时，子运行只能自己当根渲染。
          rootOrder: parentKnown ? base.rootOrder : [...base.rootOrder, event.runId],
        }
      }
      const parent = base.runs[event.parentRunId]
      if (!parent) return base
      const used = new Set(Object.values(parent.delegateRuns))
      const call = parent.segments.find((segment) => {
        if (segment.kind !== 'tool') return false
        if (used.has(segment.toolCallId)) return false
        return delegateCallMatches(parent.tools[segment.toolCallId], event.agent, event.task)
      })
      if (!call || call.kind !== 'tool') return base
      return replaceRun(base, {
        ...parent,
        delegateRuns: { ...parent.delegateRuns, [call.toolCallId]: event.runId },
      })
    }

    case 'subagent_end': {
      const { turn: base, run } = ensureRun(turn, event.runId)
      return replaceRun(base, { ...run, status: subagentStatus(event.status), summary: event.summary })
    }

    case 'report': {
      const { turn: base, run } = ensureRun(turn, event.runId)
      return replaceRun(base, {
        ...run,
        segments: [...run.segments, {
          kind: 'report',
          reportId: event.reportId,
          status: event.status,
          summary: event.summary,
          humanAction: event.humanAction,
        }],
      })
    }

    case 'ticket_created': {
      const targetId = event.runId !== null && turn.runs[event.runId]
        ? event.runId
        : turn.rootOrder.at(-1)
      if (!targetId) return turn
      const run = turn.runs[targetId]
      if (!run) return turn
      return replaceRun(turn, {
        ...run,
        segments: [...run.segments, { kind: 'ticket', ticketId: event.ticketId, title: event.title }],
      })
    }

    case 'interrupted': {
      const { turn: base, run } = ensureRun(turn, event.runId)
      const next = replaceRun(base, { ...run, status: 'interrupted' })
      if (!next.rootOrder.includes(event.runId)) return next
      return {
        ...next,
        running: false,
        interrupted: true,
        pendingConfirmation: null,
        pendingQuestion: null,
      }
    }

    case 'run_end': {
      const { turn: base, run } = ensureRun(turn, event.runId)
      return replaceRun(base, { ...run, status: finishStatus(event.finishReason), finishReason: event.finishReason })
    }

    case 'error':
      return { ...turn, error: { message: event.message, code: event.code } }

    case 'unknown':
      return turn
  }
}

/** 流结束（或读流失败）后只把 running 置 false；挂起的卡仍由 reducer 保留。 */
export function finishLiveTurn(turn: LiveTurn): LiveTurn {
  return { ...turn, running: false }
}

/**
 * 本轮涉及哪些运行：`full` 是观察到开始的（它们落库的消息全归实时副本），
 * `partial` 是中途加入的（只有发送之后的消息归实时副本）。
 */
export function liveRunSets(turn: LiveTurn): { full: Set<string>; partial: Set<string> } {
  const full = new Set<string>()
  const partial = new Set<string>()
  for (const run of Object.values(turn.runs)) {
    if (run.observedStart) full.add(run.runId)
    else partial.add(run.runId)
  }
  return { full, partial }
}

/** 实时副本在屏时，历史里由本轮运行产生的消息让位（§4.5.2）。 */
export function filterLiveHistory<T extends Pick<V3Message, 'id' | 'role' | 'run_id'>>(
  messages: readonly T[],
  turn: LiveTurn | null,
): T[] {
  if (!turn) return [...messages]
  const { full, partial } = liveRunSets(turn)
  return messages.filter((message) => {
    if (message.role === 'user' || !message.run_id) return true
    if (full.has(message.run_id)) return false
    return !(partial.has(message.run_id) && message.id > turn.baseMessageId)
  })
}

/**
 * 本轮的用户消息是否已经落库进历史（含跨标签页按 Enter 之后的历史刷新）。
 * 收件箱消息不是「用户本人说的话」，【引用】段也不属于原话，比较前都排除。
 */
export function userMessageArrived(
  messages: readonly V3Message[],
  text: string,
  baseMessageId: number,
): boolean {
  const wanted = text.trim()
  return messages.some((message) => message.id > baseMessageId
    && message.role === 'user'
    && !isInboxEvent(message)
    && splitReferenceBlock(message.content ?? '').text.trim() === wanted)
}
