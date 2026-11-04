import { describe, expect, it } from 'vitest'
import {
  createLiveTurn,
  filterLiveHistory,
  liveRunSets,
  reduceLiveTurn,
  userMessageArrived,
  type LiveTurn,
} from './live-turn'
import type { SseV3Event } from './sse'
import type { V3Message } from './types'

function reduce(turn: LiveTurn, events: SseV3Event[]): LiveTurn {
  return events.reduce(reduceLiveTurn, turn)
}

function runStart(runId: string, parentRunId: string | null = null, agent = 'orchestrator'): SseV3Event {
  return { type: 'run_start', runId, parentRunId, agent, kind: 'orchestrator_turn', ticketId: null }
}

function message(overrides: Partial<V3Message> & Pick<V3Message, 'id' | 'role'>): V3Message {
  return { session_id: 'main', seq: overrides.id, ...overrides }
}

describe('M72 实时副本 reducer', () => {
  it('appends text deltas and tool calls to the run they belong to', () => {
    const turn = reduce(createLiveTurn('orchestrator', '把日常做了', 10), [
      runStart('r1'),
      runStart('r2', null, 'worker'),
      { type: 'text_delta', runId: 'r1', text: '第一段' },
      { type: 'tool_start', runId: 'r1', toolCallId: 'c1', name: 'get_device_status', arguments: {} },
      { type: 'text_delta', runId: 'r2', text: '别处的文字' },
      { type: 'text_delta', runId: 'r1', text: '接在工具后面' },
    ])

    expect(turn.rootOrder).toEqual(['r1', 'r2'])
    expect(turn.runs.r1?.segments).toEqual([
      { kind: 'text', text: '第一段' },
      { kind: 'tool', toolCallId: 'c1' },
      { kind: 'text', text: '接在工具后面' },
    ])
    expect(turn.runs.r1?.tools.c1).toMatchObject({ name: 'get_device_status' })
    expect(turn.runs.r2?.segments).toEqual([{ kind: 'text', text: '别处的文字' }])
  })

  it('marks runs joined midway as partial', () => {
    const turn = reduce(createLiveTurn('ticket:t1', '继续', 4), [
      { type: 'text_delta', runId: 'r-joined', text: '我先看到的半截' },
    ])

    expect(turn.rootOrder).toEqual(['r-joined'])
    expect(turn.runs['r-joined']?.observedStart).toBe(false)
    expect(liveRunSets(turn)).toEqual({ full: new Set(), partial: new Set(['r-joined']) })
  })

  it('nests a subagent run under the matching delegate call', () => {
    const turn = reduce(createLiveTurn('orchestrator', '查一下', 0), [
      runStart('r1'),
      { type: 'tool_start', runId: 'r1', toolCallId: 'd1', name: 'delegate', arguments: { agent: 'researcher', task: '查 1-7 的掉落' } },
      { type: 'tool_start', runId: 'r1', toolCallId: 'd2', name: 'delegate', arguments: { agent: 'general', task: '刷 1-7' } },
      { type: 'subagent_start', runId: 'r5', parentRunId: 'r1', agent: 'researcher', task: '查 1-7 的掉落' },
      { type: 'subagent_start', runId: 'r6', parentRunId: 'r1', agent: 'general', task: '刷 1-7' },
    ])

    expect(turn.rootOrder).toEqual(['r1'])
    expect(turn.runs.r1?.delegateRuns).toEqual({ d1: 'r5', d2: 'r6' })
    expect(turn.runs.r5?.task).toBe('查 1-7 的掉落')
    expect(turn.runs.r5?.observedStart).toBe(true)
  })

  it('re-keys a delegate call by the run_id in its result', () => {
    const turn = reduce(createLiveTurn('orchestrator', '查一下', 0), [
      runStart('r1'),
      { type: 'tool_start', runId: 'r1', toolCallId: 'd1', name: 'delegate', arguments: { agent: 'researcher', task: '查掉落' } },
      { type: 'subagent_start', runId: 'r5', parentRunId: 'r1', agent: 'researcher', task: '查掉落' },
      {
        type: 'tool_result',
        runId: 'r1',
        toolCallId: 'd1',
        name: 'delegate',
        auditId: null,
        raw: { tool_call_id: 'd1', name: 'delegate', result: { run_id: 'r9', summary: '样本足够' } },
      },
    ])

    expect(turn.runs.r1?.delegateRuns).toEqual({ d1: 'r9' })
  })

  it('clears pending cards when their results arrive', () => {
    const withCards = reduce(createLiveTurn('orchestrator', '做吧', 0), [
      runStart('r1'),
      { type: 'tool_start', runId: 'r1', toolCallId: 'c1', name: 'submit_pipeline', arguments: {} },
      { type: 'tool_start', runId: 'r1', toolCallId: 'c2', name: 'ask_user', arguments: {} },
      {
        type: 'confirmation_required',
        runId: 'r1',
        toolCallId: 'c1',
        confirmationId: 'cf1',
        origin: null,
      },
      {
        type: 'question_required',
        runId: 'r1',
        toolCallId: 'c2',
        question: { question_id: 'q1', question: '刷哪关？', options: [], multiple: false, expires_at: '' },
        origin: null,
      },
    ])
    expect(withCards.pendingConfirmation?.confirmationId).toBe('cf1')
    expect(withCards.pendingQuestion?.question_id).toBe('q1')
    expect(withCards.runs.r1?.tools.c1?.confirmationId).toBe('cf1')

    const afterConfirmation = reduceLiveTurn(withCards, {
      type: 'tool_result',
      runId: 'r1',
      toolCallId: 'c1',
      name: 'submit_pipeline',
      auditId: 3,
      raw: { result: { pipeline_id: 'p1' } },
    })
    expect(afterConfirmation.pendingConfirmation).toBeNull()
    expect(afterConfirmation.pendingQuestion?.question_id).toBe('q1')

    const afterQuestion = reduceLiveTurn(afterConfirmation, {
      type: 'tool_result',
      runId: 'r1',
      toolCallId: 'c2',
      name: 'ask_user',
      auditId: null,
      raw: { result: { question_id: 'q1', answer: '1-7' } },
    })
    expect(afterQuestion.pendingQuestion).toBeNull()
  })

  it('stops the turn and drops pending cards on interrupted', () => {
    const turn = reduce(createLiveTurn('orchestrator', '做吧', 0), [
      runStart('r1'),
      {
        type: 'confirmation_required',
        runId: 'r1',
        toolCallId: 'c1',
        confirmationId: 'cf1',
        origin: null,
      },
      { type: 'interrupted', runId: 'r1' },
    ])

    expect(turn.running).toBe(false)
    expect(turn.interrupted).toBe(true)
    expect(turn.pendingConfirmation).toBeNull()
    expect(turn.pendingQuestion).toBeNull()
    expect(turn.runs.r1?.status).toBe('interrupted')
  })

  it('records report and ticket segments', () => {
    const turn = reduce(createLiveTurn('orchestrator', '做吧', 0), [
      runStart('r1'),
      {
        type: 'report',
        runId: 'r1',
        ticketId: 't1',
        reportId: 41,
        status: 'ready',
        summary: '刷完了',
        humanAction: null,
      },
      { type: 'ticket_created', runId: null, ticketId: 't9', title: '日常' },
    ])

    expect(turn.runs.r1?.segments).toEqual([
      { kind: 'report', reportId: 41, status: 'ready', summary: '刷完了', humanAction: null },
      { kind: 'ticket', ticketId: 't9', title: '日常' },
    ])
  })

  it('filters history produced by fully observed runs only', () => {
    const turn = reduce(createLiveTurn('orchestrator', '做吧', 5), [
      runStart('r-full'),
      { type: 'text_delta', runId: 'r-partial', text: '半截' },
    ])
    const messages = [
      message({ id: 100, role: 'user', run_id: 'r-full' }),
      message({ id: 6, role: 'assistant', run_id: 'r-full' }),
      message({ id: 4, role: 'assistant', run_id: 'r-partial' }),
      message({ id: 7, role: 'assistant', run_id: 'r-partial' }),
      message({ id: 8, role: 'assistant', run_id: 'r-other' }),
      message({ id: 9, role: 'assistant', run_id: null }),
    ]

    expect(filterLiveHistory(messages, turn).map((item) => item.id)).toEqual([100, 4, 8, 9])
    expect(filterLiveHistory(messages, null).map((item) => item.id)).toEqual([100, 6, 4, 7, 8, 9])
  })

  it('hides the optimistic bubble once the persisted user message arrives', () => {
    const messages = [
      message({ id: 11, role: 'user', content: '把日常做了', inbox_id: 1, inbox_kind: 'report' }),
      message({ id: 12, role: 'user', content: '把日常做了\n\n【引用】\n- 知识「日常」\n- 关卡「1-7」' }),
      message({ id: 9, role: 'user', content: '把日常做了' }),
    ]

    expect(userMessageArrived(messages, ' 把日常做了 ', 10)).toBe(true)
    expect(userMessageArrived(messages, '别的', 10)).toBe(false)
    // 只认发送之后的消息；且收件箱里的同一句话不算用户消息。
    expect(userMessageArrived([messages[0]!], '把日常做了', 10)).toBe(false)
    expect(userMessageArrived([messages[2]!], '把日常做了', 10)).toBe(false)
  })
})
