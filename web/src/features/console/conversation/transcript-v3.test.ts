import { describe, expect, it } from 'vitest'
import { buildConversationEntries, isInboxEvent } from './transcript-v3'
import type { V3Message } from './types'

function message(overrides: Partial<V3Message> & Pick<V3Message, 'id' | 'role'>): V3Message {
  return { session_id: 'main', seq: overrides.id, ...overrides }
}

describe('M72 会话条目', () => {
  it('renders inbox messages as event entries', () => {
    const { entries } = buildConversationEntries([
      message({
        id: 1,
        role: 'user',
        content: '【工单汇报】工单「日常」（general）进行中：已提交',
        inbox_id: 1,
        inbox_kind: 'report',
        inbox_thread_key: 'ticket:t2',
      }),
      message({
        id: 2,
        role: 'user',
        content: '【事件】pipeline:p1：流水线完成',
        inbox_id: 2,
        inbox_thread_key: 'pipeline:p1',
      }),
      message({
        id: 3,
        role: 'user',
        content: '【系统】设备已被接管',
        inbox_id: 3,
        inbox_kind: 'system',
        inbox_thread_key: 'ticket:t1',
      }),
      message({ id: 4, role: 'user', content: '把日常做了' }),
    ], { mainSessionId: 'main', currentTicketId: 't1' })

    expect(entries.map((entry) => entry.kind)).toEqual(['event', 'event', 'event', 'user'])
    expect(entries[0]).toMatchObject({
      eventKind: 'report',
      body: '工单「日常」（general）进行中：已提交',
      link: { to: '/tickets/t2', label: '打开工单' },
    })
    // inbox_kind 为空时按正文前缀判断；流水线话题给「查看流水线」。
    expect(entries[1]).toMatchObject({
      eventKind: 'thread_event',
      body: 'pipeline:p1：流水线完成',
      link: { to: '/tasks/history/p1', label: '查看流水线' },
    })
    // 当前工单自己的话题不出「打开工单」链接。
    expect(entries[2]).toMatchObject({ eventKind: 'system', link: null })
    expect(entries[3]).toMatchObject({ kind: 'user', text: '把日常做了', references: [] })

    expect(isInboxEvent({ inbox_id: 1, inbox_kind: 'report' })).toBe(true)
    expect(isInboxEvent({ inbox_id: 1, inbox_kind: 'user_message' })).toBe(false)
    expect(isInboxEvent({ inbox_id: null, inbox_kind: 'report' })).toBe(false)
  })

  it('splits the reference block off user messages', () => {
    const { entries } = buildConversationEntries([
      message({
        id: 1,
        role: 'user',
        content: '帮我看下\n\n【引用】\n- 知识「作战记录」\n- 关卡「1-7」',
      }),
    ], { mainSessionId: 'main' })

    expect(entries[0]).toMatchObject({
      kind: 'user',
      text: '帮我看下',
      references: ['知识「作战记录」', '关卡「1-7」'],
    })
  })

  it('groups subagent messages under the delegate call', () => {
    const { entries, subRuns } = buildConversationEntries([
      message({
        id: 1,
        role: 'assistant',
        content: null,
        tool_calls: [{ id: 'd1', function: { name: 'delegate', arguments: '{"agent":"researcher","task":"查掉落"}' } }],
      }),
      message({
        id: 2,
        role: 'tool',
        tool_name: 'delegate',
        tool_call_id: 'd1',
        content: '{"result":{"run_id":"sub-run","summary":"样本足够"}}',
      }),
      message({ id: 3, role: 'user', session_id: 'sub', run_id: 'sub-run', content: '查一下 1-7' }),
      message({ id: 4, role: 'assistant', session_id: 'sub', run_id: 'sub-run', content: '样本足够' }),
    ], { mainSessionId: 'main' })

    expect(entries).toHaveLength(1)
    expect(entries[0]).toMatchObject({ kind: 'tool', subRunId: 'sub-run' })
    expect(subRuns.get('sub-run')?.map((item) => item.id)).toEqual([3, 4])
  })

  it('renders report calls as report entries', () => {
    const { entries } = buildConversationEntries([
      message({
        id: 1,
        role: 'assistant',
        content: null,
        tool_calls: [{
          id: 'rep-1',
          function: {
            name: 'report',
            arguments: JSON.stringify({
              status: 'ready',
              summary: '1-7 刷完了',
              human_action: { type: 'decision', prompt: '要不要继续刷', ref_id: null },
            }),
          },
        }],
      }),
      message({ id: 2, role: 'tool', tool_name: 'report', tool_call_id: 'rep-1', content: '{"result":{"report_id":41}}' }),
    ], { mainSessionId: 'main' })

    expect(entries).toHaveLength(1)
    expect(entries[0]).toEqual({
      kind: 'report',
      key: 'call-1-rep-1',
      status: 'ready',
      summary: '1-7 刷完了',
      humanAction: { type: 'decision', prompt: '要不要继续刷', ref_id: null },
    })
  })
})
