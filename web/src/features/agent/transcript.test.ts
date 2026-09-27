import { describe, expect, it } from 'vitest'
import { buildTranscript, liveToolView } from './transcript'
import type { AgentMessage } from './types'

function msg(overrides: Partial<AgentMessage> & Pick<AgentMessage, 'id' | 'role'>): AgentMessage {
  return { session_id: 'session-1', seq: overrides.id, ...overrides }
}

describe('agent transcript', () => {
  it('pairs each tool call with its result and keeps the assistant text before it', () => {
    const entries = buildTranscript([
      msg({ id: 1, role: 'user', content: '分析失败' }),
      msg({ id: 2, role: 'assistant', content: '我先看日志。', tool_calls: [{ id: 'call-1', function: { name: 'get_logs', arguments: '{"source":"maa"}' } }] }),
      msg({ id: 3, role: 'tool', tool_call_id: 'call-1', tool_name: 'get_logs', audit_id: 7, content: '{"result":{"items":[]}}' }),
      msg({ id: 4, role: 'assistant', content: '日志里没有错误。' }),
    ])

    expect(entries.map((entry) => entry.kind)).toEqual(['user', 'assistant', 'tool', 'assistant'])
    const tool = entries[2]
    expect(tool?.kind === 'tool' && tool.tool).toMatchObject({
      name: 'get_logs',
      arguments: { source: 'maa' },
      result: { result: { items: [] } },
      status: 'done',
      auditId: 7,
      standalone: false,
    })
  })

  it('marks failed tool results and calls without a result', () => {
    const entries = buildTranscript([
      msg({ id: 1, role: 'assistant', tool_calls: [
        { id: 'call-1', function: { name: 'submit_pipeline', arguments: '{}' } },
        { id: 'call-2', function: { name: 'get_queue', arguments: 'not json' } },
      ] }),
      msg({ id: 2, role: 'tool', tool_call_id: 'call-1', tool_name: 'submit_pipeline', content: '{"error":{"code":"CONFIRMATION_REJECTED","message":"确认请求已被拒绝"}}' }),
    ])

    const [rejected, missing] = entries.map((entry) => (entry.kind === 'tool' ? entry.tool : null))
    expect(rejected).toMatchObject({ status: 'failed', errorMessage: '确认请求已被拒绝' })
    expect(missing).toMatchObject({ status: 'missing', arguments: 'not json' })
  })

  it('shows a tool result whose call is outside the loaded window on its own', () => {
    const entries = buildTranscript([
      msg({ id: 9, role: 'tool', tool_call_id: 'call-old', tool_name: 'get_logs', content: '{"total":0}' }),
    ])

    expect(entries).toHaveLength(1)
    expect(entries[0]?.kind === 'tool' && entries[0].tool).toMatchObject({ standalone: true, name: 'get_logs', status: 'done' })
  })

  it('derives the live tool status from the stream events', () => {
    expect(liveToolView({ toolCallId: 'a', name: 'get_queue' }, true).status).toBe('running')
    expect(liveToolView({ toolCallId: 'a', name: 'get_queue' }, false).status).toBe('missing')
    expect(liveToolView({ toolCallId: 'a', name: 'submit_pipeline', confirmationId: 'c-1' }, true).status).toBe('awaiting')
    const done = liveToolView({ toolCallId: 'a', name: 'get_queue', auditId: 3, result: { tool_call_id: 'a', name: 'get_queue', message_id: 5, audit_id: 3, result: { pending: [] } } }, true)
    // 结果区只展示工具返回体，不重复事件里的调用编号与消息编号。
    expect(done).toMatchObject({ status: 'done', auditId: 3, result: { result: { pending: [] } } })
    const failed = liveToolView({ toolCallId: 'a', name: 'click', result: { tool_call_id: 'a', error: { code: 'X', message: '不支持' } } }, false)
    expect(failed).toMatchObject({ status: 'failed', errorMessage: '不支持' })
  })
})
