import { describe, expect, it } from 'vitest'
import type { AgentQuestion } from '@/features/agent/types'
import type { Confirmation } from '@/features/confirmations/ConfirmationsPanel'
import type { MemoryItem } from '@/features/memory/api'
import type { PlanView } from '@/features/tickets/workspace-api'
import type { AlertOut } from '@/features/watchdog/api'
import { buildAttentionItems, originLabel } from './attention'
import { toTicketView } from './api'
import { makeConfirmation, makeMemory, makeQuestion, makeTicket, makeWatchdogAlert } from './test-utils'

const NOW = Date.parse('2026-09-27T10:00:00Z')

function confirmation(overrides: Record<string, unknown> = {}): Confirmation {
  return makeConfirmation(overrides) as unknown as Confirmation
}

function question(overrides: Record<string, unknown> = {}): AgentQuestion {
  return makeQuestion(overrides) as unknown as AgentQuestion
}

function ticket(overrides: Record<string, unknown> = {}) {
  return toTicketView(makeTicket(overrides))
}

function origin(ticketId: string) {
  return { ticket_id: ticketId, ticket_title: '日常', agent: 'general', run_id: 'run-1' }
}

describe('M72 待你处理', () => {
  it('orders confirmations, questions, blocked and waiting tickets', () => {
    const items = buildAttentionItems({
      confirmations: [
        confirmation({ confirmation_id: 'confirm-late', expires_at: new Date(NOW + 100_000).toISOString() }),
        confirmation({ confirmation_id: 'confirm-soon', expires_at: new Date(NOW + 50_000).toISOString() }),
        // 读不出的过期时间排在能读出的之后。
        confirmation({ confirmation_id: 'confirm-bad', expires_at: '不是时间' }),
      ],
      questions: [
        // 提问没有有效期：按创建时间升序，最早的在最前面。
        question({ question_id: 'q-later', created_at: '2026-09-27T10:01:00Z' }),
        question({ question_id: 'q-sooner', created_at: '2026-09-27T10:00:00Z' }),
      ],
      tickets: [
        ticket({ id: 't-waiting', status: 'waiting', updated_at: '2026-09-27T10:03:00Z' }),
        ticket({ id: 't-blocked', status: 'blocked', updated_at: '2026-09-27T10:02:00Z' }),
        ticket({ id: 't-done', status: 'done' }),
      ],
    })

    expect(items.map((item) => item.kind)).toEqual([
      'confirmation',
      'confirmation',
      'confirmation',
      'question',
      'question',
      'ticket',
      'ticket',
    ])
    expect(items.map((item) => item.id)).toEqual([
      'confirm-soon',
      'confirm-late',
      'confirm-bad',
      'q-sooner',
      'q-later',
      't-blocked',
      't-waiting',
    ])
  })

  it('drops a ticket whose confirmation or question is already listed', () => {
    const items = buildAttentionItems({
      confirmations: [
        confirmation({ confirmation_id: 'confirm-1', origin: origin('t-origin') }),
      ],
      questions: [
        question({ question_id: 'q-1', origin: origin('t-question') }),
        question({ question_id: 'confirm-ref' }),
      ],
      tickets: [
        ticket({ id: 't-origin', status: 'waiting' }),
        ticket({ id: 't-question', status: 'blocked' }),
        ticket({
          id: 't-ref',
          status: 'waiting',
          human_action: { type: 'confirmation', prompt: '等你确认', ref_id: 'confirm-1' },
        }),
        ticket({ id: 't-free', status: 'waiting' }),
      ],
    })

    expect(items.filter((item) => item.kind === 'ticket').map((item) => item.id))
      .toEqual(['t-free'])
    expect(items.map((item) => item.id)).toEqual(['confirm-1', 'confirm-ref', 'q-1', 't-free'])
  })

  it('excludes the cards shown in the live turn', () => {
    const items = buildAttentionItems({
      confirmations: [confirmation({ confirmation_id: 'confirm-live', origin: origin('t-by-confirm') })],
      questions: [
        question({ question_id: 'q-live', origin: origin('t-by-question') }),
        question({ question_id: 'q-open' }),
      ],
      tickets: [
        ticket({ id: 't-by-confirm', status: 'waiting' }),
        ticket({ id: 't-by-question', status: 'blocked' }),
        ticket({
          id: 't-by-ref',
          status: 'waiting',
          human_action: { type: 'confirmation', prompt: '等你确认', ref_id: 'confirm-live' },
        }),
        ticket({ id: 't-open', status: 'waiting' }),
      ],
      excludeIds: new Set(['confirm-live', 'q-live']),
    })

    expect(items.map((item) => item.id)).toEqual(['q-open', 't-open'])
  })

  it('adds one pending-memory entry between questions and tickets', () => {
    const memory = (overrides: Record<string, unknown>) =>
      makeMemory(overrides) as unknown as MemoryItem
    const items = buildAttentionItems({
      confirmations: [confirmation({ confirmation_id: 'confirm-1' })],
      questions: [question({ question_id: 'q-1' })],
      memories: [
        memory({ id: 'm1', content: '刷图不用源石' }),
        memory({ id: 'm2', content: 'ADB 掉线先重连' }),
        // 已生效的不出现在待你处理里。
        memory({ id: 'm3', status: 'active', content: '已生效' }),
      ],
      tickets: [ticket({ id: 't-blocked', status: 'blocked' })],
    })

    expect(items.map((item) => item.kind)).toEqual([
      'confirmation',
      'question',
      'memory',
      'ticket',
    ])
    const entry = items.find((item) => item.kind === 'memory')
    expect(entry).toMatchObject({
      id: 'memory-proposed',
      count: 2,
      previews: ['刷图不用源石', 'ADB 掉线先重连'],
    })
    // 没有待审核记忆时这一项不出现。
    expect(buildAttentionItems({
      confirmations: [],
      questions: [],
      tickets: [],
    }).some((item) => item.kind === 'memory')).toBe(false)
  })

  it('lists only unresolved critical watchdog alerts, newest first', () => {
    const alert = (overrides: Record<string, unknown>) =>
      makeWatchdogAlert(overrides) as unknown as AlertOut
    const items = buildAttentionItems({
      confirmations: [],
      questions: [],
      alerts: [
        alert({ id: 7, created_at: '2026-11-05T04:00:00Z' }),
        alert({ id: 8, created_at: '2026-11-05T05:00:00Z' }),
        // 警告由页面读取告警列表，不进待你处理。
        alert({ id: 9, severity: 'warn' }),
        // 已解除的不出现。
        alert({ id: 10, resolved_at: '2026-11-05T06:00:00Z' }),
      ],
      tickets: [ticket({ id: 't-blocked', status: 'blocked' })],
    })

    expect(items.map((item) => item.kind)).toEqual(['alert', 'alert', 'ticket'])
    expect(items.filter((item) => item.kind === 'alert').map((item) => item.id))
      .toEqual(['8', '7'])
  })

  it('labels the origin of a card', () => {
    const titles = new Map([['general', '通用 Worker']])
    expect(originLabel(
      { ticket_id: 't1', ticket_title: '日常', agent: 'general', run_id: null },
      undefined,
      titles,
    )).toBe('来自工单「日常」· 通用 Worker')
    expect(originLabel(
      { ticket_id: 't1', ticket_title: null, agent: null, run_id: null },
      undefined,
      titles,
    )).toBe('来自工单「未命名」· 代理')
    expect(originLabel(null, { kind: 'rest' }, titles)).toBe('来自 REST 调用')
    expect(originLabel(null, { kind: 'internal' }, titles)).toBe('来自后台运行')
    expect(originLabel(null, undefined, titles)).toBe('来自后台运行')
  })
})

describe('计划待批准', () => {
  const decisionTicket = (overrides: Record<string, unknown> = {}) => ticket({
    id: 't-plan',
    status: 'waiting',
    human_action: { type: 'decision', prompt: '批准计划「清日常」？', ref_id: 'p1' },
    ...overrides,
  })

  const plan = (overrides: Partial<PlanView> = {}): PlanView => ({
    id: 'p1',
    session_id: 'w1',
    ticket_id: 't-plan',
    title: '清日常',
    items: [{ id: 'a1', title: '提交剿灭', status: 'in_progress', note: '先看体力' }],
    status: 'awaiting_approval',
    approved_by: null,
    approved_at: null,
    ...overrides,
  })

  it('turns a waiting ticket with a pending plan into a plan item', () => {
    const items = buildAttentionItems({
      confirmations: [],
      questions: [],
      tickets: [decisionTicket(), ticket({ id: 't-waiting', status: 'waiting' })],
      plans: [{ ticket: decisionTicket(), plan: plan(), workerSessionId: 'w1' }],
    })

    expect(items.map((item) => item.kind)).toEqual(['plan', 'ticket'])
    const planItem = items[0]
    if (planItem?.kind !== 'plan') throw new Error('expected a plan item')
    expect(planItem.id).toContain('p1')
    expect(planItem.workerSessionId).toBe('w1')
    expect(planItem.plan.title).toBe('清日常')
    expect(planItem.ticket.id).toBe('t-plan')
  })

  it('hides a ticket whose plan already left awaiting approval', () => {
    const items = buildAttentionItems({
      confirmations: [],
      questions: [],
      tickets: [decisionTicket()],
      suppressedTicketIds: new Set(['t-plan']),
    })

    expect(items).toEqual([])
  })

  it('keeps the ticket card when no plan data is available', () => {
    const items = buildAttentionItems({
      confirmations: [],
      questions: [],
      tickets: [decisionTicket({
        human_action: { type: 'decision', prompt: '需要你决定', ref_id: 'watchdog:1' },
      })],
    })

    expect(items.map((item) => item.kind)).toEqual(['ticket'])
  })
})
