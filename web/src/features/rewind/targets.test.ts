import { describe, expect, it } from 'vitest'
import type { V3Message } from '@/features/console/conversation/types'
import { messageActions } from './targets'

function message(overrides: Partial<V3Message> & Pick<V3Message, 'id' | 'role'>): V3Message {
  return { session_id: 's1', seq: overrides.id, content: '', ...overrides }
}

const ORCHESTRATOR = { kind: 'orchestrator', running: false } as const
const WORKER = { kind: 'worker', running: false } as const

describe('messageActions（M80 §4.6）', () => {
  it('人类消息给出「回到这里」「编辑后重发」，总控首条是 -1', () => {
    const messages = [
      message({ id: 1, role: 'user', content: '把日常做了' }),
      message({ id: 2, role: 'assistant', content: '好的。' }),
    ]
    expect(messageActions(messages, messages[0]!, ORCHESTRATOR)).toEqual({
      rewindTo: 1,
      edit: { toSeq: -1, content: '把日常做了' },
    })
  })

  it('工单页的人类消息带「分叉」，首条没有 -1 的「编辑后重发」', () => {
    const messages = [
      message({ id: 1, role: 'user', session_id: 'w', content: '刷 1-7' }),
      message({ id: 2, role: 'assistant', session_id: 'w', content: '开始。' }),
    ]
    expect(messageActions(messages, messages[0]!, WORKER)).toEqual({
      rewindTo: 1,
      forkAt: 1,
    })
  })

  it('最终回复给出「重新生成」，toSeq 是那条人类消息之前最近的消息', () => {
    const messages = [
      message({ id: 1, role: 'user', content: '第一件事' }),
      message({ id: 2, role: 'assistant', content: '做完了。' }),
      message({ id: 3, role: 'user', content: '再来一次' }),
      message({ id: 4, role: 'assistant', content: '好的，再来。' }),
    ]
    expect(messageActions(messages, messages[3]!, ORCHESTRATOR)).toEqual({
      rewindTo: 4,
      regenerate: { toSeq: 2, content: '再来一次' },
    })
  })

  it('总控第一条最终回复的「重新生成」回到 -1', () => {
    const messages = [
      message({ id: 1, role: 'user', content: '你好' }),
      message({ id: 2, role: 'assistant', content: '你好，博士。' }),
    ]
    expect(messageActions(messages, messages[1]!, ORCHESTRATOR)).toMatchObject({
      rewindTo: 2,
      regenerate: { toSeq: -1, content: '你好' },
    })
  })

  it('带 tool_calls 的 assistant 不是合法目标，也没有动作', () => {
    const messages = [
      message({ id: 1, role: 'user', content: '建个工单' }),
      message({
        id: 2,
        role: 'assistant',
        content: '正在建。',
        tool_calls: [{ id: 'c1', function: { name: 'create_ticket', arguments: '{}' } }],
      }),
      message({ id: 3, role: 'tool', tool_call_id: 'c1', tool_name: 'create_ticket', content: '{"result":{}}' }),
    ]
    expect(messageActions(messages, messages[1]!, ORCHESTRATOR)).toEqual({})
  })

  it('只有同一批 tool_call 的最后一个结果才是合法目标', () => {
    const messages = [
      message({
        id: 1,
        role: 'assistant',
        content: null,
        tool_calls: [
          { id: 'c1', function: { name: 'get_stage', arguments: '{}' } },
          { id: 'c2', function: { name: 'get_queue', arguments: '{}' } },
        ],
      }),
      message({ id: 2, role: 'tool', tool_call_id: 'c1', tool_name: 'get_stage', content: '{"result":{}}' }),
      message({ id: 3, role: 'tool', tool_call_id: 'c2', tool_name: 'get_queue', content: '{"result":{}}' }),
    ]
    expect(messageActions(messages, messages[1]!, ORCHESTRATOR)).toEqual({})
    expect(messageActions(messages, messages[2]!, ORCHESTRATOR)).toEqual({ rewindTo: 3 })
  })

  it('已被取代的消息没有任何动作', () => {
    const messages = [
      message({ id: 1, role: 'user', content: '回到这里' }),
      message({ id: 2, role: 'user', content: '旧的', superseded_at: '2026-11-05T00:00:00Z' }),
      message({ id: 3, role: 'assistant', content: '旧回复', superseded_at: '2026-11-05T00:00:00Z' }),
    ]
    expect(messageActions(messages, messages[1]!, ORCHESTRATOR)).toEqual({})
    expect(messageActions(messages, messages[2]!, ORCHESTRATOR)).toEqual({})
  })

  it('算 toSeq 时跳过已被取代的消息', () => {
    const messages = [
      message({ id: 1, role: 'user', content: '第一句' }),
      message({ id: 2, role: 'user', content: '被回溯掉的一句', superseded_at: '2026-11-05T00:00:00Z' }),
      message({ id: 3, role: 'user', content: '现在这句' }),
      message({ id: 4, role: 'assistant', content: '好。' }),
    ]
    expect(messageActions(messages, messages[2]!, ORCHESTRATOR)).toMatchObject({
      edit: { toSeq: 1, content: '现在这句' },
    })
    expect(messageActions(messages, messages[3]!, ORCHESTRATOR)).toMatchObject({
      regenerate: { toSeq: 1, content: '现在这句' },
    })
  })

  it('收件箱转出的事件不是人类消息：可「回到这里」，但不能编辑或作为重发对象', () => {
    const messages = [
      message({ id: 1, role: 'user', inbox_id: 9, inbox_kind: 'report', inbox_thread_key: 'ticket:t1', content: '【工单汇报】完成' }),
      message({ id: 2, role: 'assistant', content: '收到。' }),
    ]
    expect(messageActions(messages, messages[0]!, ORCHESTRATOR)).toEqual({ rewindTo: 1 })
    expect(messageActions(messages, messages[1]!, ORCHESTRATOR)).toEqual({ rewindTo: 2 })
  })

  it('人类消息按 inbox_kind=user_message 认', () => {
    const messages = [
      message({ id: 1, role: 'user', inbox_id: 3, inbox_kind: 'user_message', content: '用户打字的消息' }),
      message({ id: 2, role: 'assistant', content: '嗯。' }),
    ]
    expect(messageActions(messages, messages[0]!, ORCHESTRATOR)).toMatchObject({
      edit: { toSeq: -1, content: '用户打字的消息' },
    })
  })

  it('不在列表里的消息不给动作', () => {
    const messages = [message({ id: 1, role: 'user', content: 'a' })]
    expect(messageActions(messages, message({ id: 2, role: 'user', content: 'b' }), ORCHESTRATOR)).toEqual({})
  })

  it('running 只影响禁用，不改变返回的动作集合', () => {
    const messages = [
      message({ id: 1, role: 'user', content: '把日常做了' }),
      message({ id: 2, role: 'assistant', content: '好的。' }),
    ]
    expect(messageActions(messages, messages[1]!, { kind: 'orchestrator', running: true }))
      .toEqual(messageActions(messages, messages[1]!, ORCHESTRATOR))
  })
})
