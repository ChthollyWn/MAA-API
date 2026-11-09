import { describe, expect, it } from 'vitest'
import { toSseV3Event } from './sse'

describe('M72 SSE v3 归一', () => {
  it('normalizes every SSE v3 event into a typed event', () => {
    expect(toSseV3Event('run_start', {
      run_id: 'r1',
      parent_run_id: null,
      session_id: 's1',
      ticket_id: null,
      agent: 'orchestrator',
      kind: 'orchestrator_turn',
      consumed_inbox_ids: [3],
    })).toEqual({
      type: 'run_start',
      runId: 'r1',
      parentRunId: null,
      agent: 'orchestrator',
      kind: 'orchestrator_turn',
      ticketId: null,
    })

    expect(toSseV3Event('text_delta', { run_id: 'r1', text: '你好' }))
      .toEqual({ type: 'text_delta', runId: 'r1', text: '你好' })

    expect(toSseV3Event('tool_start', {
      run_id: 'r1',
      tool_call_id: 'c1',
      name: 'create_ticket',
      arguments: { title: '日常' },
    })).toEqual({
      type: 'tool_start',
      runId: 'r1',
      toolCallId: 'c1',
      name: 'create_ticket',
      arguments: { title: '日常' },
    })

    expect(toSseV3Event('tool_result', {
      run_id: 'r1',
      tool_call_id: 'c1',
      name: 'create_ticket',
      message_id: 88,
      audit_id: 301,
      result: { ticket_id: 't1' },
    })).toEqual({
      type: 'tool_result',
      runId: 'r1',
      toolCallId: 'c1',
      name: 'create_ticket',
      auditId: 301,
      raw: {
        run_id: 'r1',
        tool_call_id: 'c1',
        name: 'create_ticket',
        message_id: 88,
        audit_id: 301,
        result: { ticket_id: 't1' },
      },
    })

    expect(toSseV3Event('confirmation_required', {
      run_id: 'r2',
      tool_call_id: 'c9',
      name: 'submit_pipeline',
      confirmation_id: 'cf1',
      audit_id: 305,
      expires_at: '2026-11-02T08:10:00Z',
      origin: { ticket_id: 't1', ticket_title: '日常', agent: 'general', run_id: 'r2' },
    })).toEqual({
      type: 'confirmation_required',
      runId: 'r2',
      toolCallId: 'c9',
      confirmationId: 'cf1',
      origin: { ticket_id: 't1', ticket_title: '日常', agent: 'general', run_id: 'r2' },
    })

    expect(toSseV3Event('question_required', {
      run_id: 'r1',
      tool_call_id: 'c2',
      question_id: 'q1',
      question: '刷哪一关？',
      options: [{ label: '1-7' }, { label: 'CE-6', description: '龙门币' }, { nope: true }],
      multiple: false,
      origin: null,
    })).toEqual({
      type: 'question_required',
      runId: 'r1',
      toolCallId: 'c2',
      question: {
        question_id: 'q1',
        question: '刷哪一关？',
        options: [{ label: '1-7', description: null }, { label: 'CE-6', description: '龙门币' }],
        multiple: false,
      },
      origin: null,
    })

    expect(toSseV3Event('subagent_start', {
      run_id: 'r5',
      parent_run_id: 'r2',
      agent: 'researcher',
      task: '查 1-7 的掉落',
    })).toEqual({
      type: 'subagent_start',
      runId: 'r5',
      parentRunId: 'r2',
      agent: 'researcher',
      task: '查 1-7 的掉落',
    })

    expect(toSseV3Event('subagent_end', { run_id: 'r5', status: 'succeeded', summary: '样本足够' }))
      .toEqual({ type: 'subagent_end', runId: 'r5', status: 'succeeded', summary: '样本足够' })

    expect(toSseV3Event('report', {
      run_id: 'r2',
      ticket_id: 't1',
      report_id: 41,
      status: 'progress',
      summary: '流水线已提交',
      human_action: { type: 'decision', prompt: '等流水线结果', ref_id: null },
    })).toEqual({
      type: 'report',
      runId: 'r2',
      ticketId: 't1',
      reportId: 41,
      status: 'progress',
      summary: '流水线已提交',
      humanAction: { type: 'decision', prompt: '等流水线结果', ref_id: null },
    })

    expect(toSseV3Event('ticket_created', { ticket_id: 't1', title: '日常', kind: 'general', status: 'open' }))
      .toEqual({ type: 'ticket_created', runId: null, ticketId: 't1', title: '日常' })

    expect(toSseV3Event('interrupted', { run_id: 'r2', finish_reason: 'interrupted' }))
      .toEqual({ type: 'interrupted', runId: 'r2' })

    expect(toSseV3Event('run_end', { run_id: 'r1', finish_reason: 'stop', usage: {} }))
      .toEqual({ type: 'run_end', runId: 'r1', finishReason: 'stop' })

    expect(toSseV3Event('error', { code: 'LLM_REQUEST_FAILED', message: '模型挂了' }))
      .toEqual({ type: 'error', code: 'LLM_REQUEST_FAILED', message: '模型挂了' })

    expect(toSseV3Event('compaction', { run_id: 'r1', checkpoint_id: 'ck1' }))
      .toEqual({ type: 'unknown', name: 'compaction' })
  })

  it('turns unknown or run-less events into no-ops', () => {
    expect(toSseV3Event('plan_updated', { run_id: 'r1', plan_id: 'p1' }))
      .toEqual({ type: 'unknown', name: 'plan_updated' })
    expect(toSseV3Event('text_delta', { text: '没有运行 id' }))
      .toEqual({ type: 'unknown', name: 'text_delta' })
    expect(toSseV3Event('text_delta', { run_id: '', text: '空运行 id' }))
      .toEqual({ type: 'unknown', name: 'text_delta' })
    expect(toSseV3Event('text_delta', null)).toEqual({ type: 'unknown', name: 'text_delta' })
    // error 与 ticket_created 是仅有的两个允许没有 run_id 的事件。
    expect(toSseV3Event('error', { message: '' }))
      .toEqual({ type: 'error', code: '', message: '本轮回复失败，请重试。' })
    expect(toSseV3Event('ticket_created', { ticket_id: 't1', title: '日常' }))
      .toEqual({ type: 'ticket_created', runId: null, ticketId: 't1', title: '日常' })
  })

  it('reads origin from confirmation and question events', () => {
    const origin = { ticket_id: 't1', ticket_title: '日常', agent: 'general', run_id: 'r2' }
    expect(toSseV3Event('confirmation_required', {
      run_id: 'r2', tool_call_id: 'c1', confirmation_id: 'cf1', origin,
    })).toMatchObject({ origin })
    expect(toSseV3Event('question_required', {
      run_id: 'r2', tool_call_id: 'c1', question_id: 'q1', origin,
    })).toMatchObject({ origin: { ticket_id: 't1', ticket_title: '日常', agent: 'general', run_id: 'r2' } })
    // REST 发起的确认/提问没有 origin；字段缺失时每个字段写 null。
    expect(toSseV3Event('confirmation_required', {
      run_id: 'r2', tool_call_id: 'c1', confirmation_id: 'cf1',
    })).toMatchObject({ origin: null })
    expect(toSseV3Event('question_required', {
      run_id: 'r2', tool_call_id: 'c1', question_id: 'q1', origin: { ticket_id: 't1' },
    })).toMatchObject({ origin: { ticket_id: 't1', ticket_title: null, agent: null, run_id: null } })
  })
})
