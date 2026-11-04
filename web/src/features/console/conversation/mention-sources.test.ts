import { describe, expect, it } from 'vitest'
import {
  stageCandidate,
  toKnowledgeCandidates,
  toOperatorCandidates,
  toPipelineCandidates,
  toTicketCandidates,
} from './mention-sources'

describe('M72 mention sources', () => {
  it('filters tickets by title case-insensitively and labels the status', () => {
    const tickets = [
      { id: 't1', title: '日常', status: 'open' },
      { id: 't2', title: 'Daily Mission', status: 'waiting' },
    ]
    expect(toTicketCandidates(tickets, '日')).toEqual([
      { type: 'ticket', id: 't1', label: '日常', detail: '进行中' },
    ])
    expect(toTicketCandidates(tickets, 'DAILY')).toEqual([
      { type: 'ticket', id: 't2', label: 'Daily Mission', detail: '等你处理' },
    ])
    expect(toTicketCandidates(tickets, '')).toHaveLength(2)
    // 表里没有的状态原样显示。
    expect(toTicketCandidates([{ id: 't3', title: 'x', status: 'unknown' }], '')).toEqual([
      { type: 'ticket', id: 't3', label: 'x', detail: 'unknown' },
    ])
  })

  it('keeps enabled knowledge and matches id or title', () => {
    const entries = [
      { id: 'kb-1', title: '日常排班', enabled: true },
      { id: 'kb-2', title: '剿灭阵容', enabled: false },
    ]
    expect(toKnowledgeCandidates(entries, '排班')).toEqual([
      { type: 'knowledge', id: 'kb-1', label: '日常排班', detail: 'kb-1' },
    ])
    expect(toKnowledgeCandidates(entries, 'KB-2')).toEqual([])
    expect(toKnowledgeCandidates(entries, 'kb-')).toHaveLength(1)
  })

  it('turns operator rows into owned / not-owned candidates', () => {
    expect(toOperatorCandidates([
      { char_id: 'char_002_amiya', name: '阿米娅', own: true },
      { char_id: 'char_003_kalts', name: '凯尔希', own: false },
      { char_id: 'char_999_unknown', name: null, own: false },
    ])).toEqual([
      { type: 'operator', id: 'char_002_amiya', label: '阿米娅', detail: '持有' },
      { type: 'operator', id: 'char_003_kalts', label: '凯尔希', detail: '未持有' },
    ])
  })

  it('offers a stage candidate only for a local stage code', () => {
    expect(stageCandidate('1-7')).toEqual({ type: 'stage', id: '1-7', label: '1-7', detail: '关卡' })
    expect(stageCandidate('main_01-07')).toBeNull()
    expect(stageCandidate('')).toBeNull()
    expect(stageCandidate('-1')).toBeNull()
    expect(stageCandidate('a'.repeat(17))).toBeNull()
  })

  it('matches pipelines by title or id prefix and falls back to the id prefix', () => {
    const rows = [
      { id: 'abcdefgh1234', title: '每日刷图', status: 'completed' },
      { id: 'zzzzzzzz9999', title: '', status: 'running' },
    ]
    expect(toPipelineCandidates(rows, '刷图')).toEqual([
      { type: 'pipeline', id: 'abcdefgh1234', label: '每日刷图', detail: '已完成' },
    ])
    expect(toPipelineCandidates(rows, 'ABCDEF')).toHaveLength(1)
    expect(toPipelineCandidates(rows, '')).toEqual([
      { type: 'pipeline', id: 'abcdefgh1234', label: '每日刷图', detail: '已完成' },
      { type: 'pipeline', id: 'zzzzzzzz9999', label: '流水线 zzzzzzzz', detail: '正在执行' },
    ])
    expect(toPipelineCandidates(rows, '不存在')).toEqual([])
    // 状态文案表里没有就原样显示。
    expect(toPipelineCandidates([{ id: 'p1', title: 'x', status: 'mystery' }], '')).toEqual([
      { type: 'pipeline', id: 'p1', label: 'x', detail: 'mystery' },
    ])
  })
})
