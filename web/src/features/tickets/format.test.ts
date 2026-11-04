import { createElement } from 'react'
import { describe, expect, it } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import {
  FINISH_REASON_LABELS,
  ORIGIN_LABELS,
  REPORT_STATUS_LABELS,
  RISK_LABELS,
  RUN_KIND_LABELS,
  RUN_STATUS_LABELS,
  STRENGTH_LABELS,
  TICKET_STATUS_LABELS,
  formatCost,
  formatDuration,
  toLeaseView,
  toReportView,
  toRunNode,
  toTicketDetailView,
  toTicketView,
} from './format'
import { TicketStatusBadge } from './TicketStatusBadge'

describe('M72 工单文案与适配', () => {
  it('labels ticket, report and run statuses in Chinese', () => {
    expect(TICKET_STATUS_LABELS).toEqual({
      open: '进行中',
      waiting: '等你处理',
      blocked: '受阻',
      done: '已完成',
      failed: '失败',
      cancelled: '已取消',
    })
    expect(REPORT_STATUS_LABELS).toEqual({
      progress: '进展',
      waiting: '等你处理',
      ready: '已完成',
      blocked: '受阻',
      failed: '失败',
      dropped: '已放弃',
    })
    expect(RUN_STATUS_LABELS).toEqual({
      running: '运行中',
      waiting_human: '等你处理',
      succeeded: '已完成',
      failed: '失败',
      interrupted: '已停止',
      cancelled: '已取消',
    })
    expect(RUN_KIND_LABELS).toMatchObject({
      orchestrator_turn: '总控一轮',
      worker_turn: 'Worker 一轮',
      subagent: '子代理',
      trigger_eval: '事件研判',
      compaction: '摘要压缩',
      memory_extract: '记忆整理',
      title: '标题',
      watchdog_check: 'Watchdog 巡检',
      eval_case: '评测',
      legacy_turn: '旧会话一轮',
    })
    expect(FINISH_REASON_LABELS).toMatchObject({
      stop: '正常结束',
      report_submitted: '已汇报',
      question_unanswered: '提问没有回答',
      confirmation_rejected: '确认被拒绝',
      confirmation_expired: '确认已过期',
      stuck_loop: '重复调用被叫停',
      max_steps: '达到步数上限',
      budget_exceeded: '超出成本预算',
      context_overflow: '上下文装不下',
      interrupted: '被停止',
      cancelled: '工单已取消',
      lease_preempted: '设备被接管',
      error: '出错',
      recovered_after_restart: '服务重启时停止',
    })
    expect(ORIGIN_LABELS).toEqual({ user: '你发起', trigger: '事件', goal: '目标', watchdog: 'Watchdog', system: '系统' })
    expect(STRENGTH_LABELS).toEqual({ explicit: '你明确要求的', inferred: '系统推断的' })
    expect(RISK_LABELS).toEqual({
      none: '只做无需确认的操作',
      consume: '最高到消耗类操作',
      destructive: '最高到破坏类操作',
    })

    // 表里没有的取值原样显示。
    render(createElement(TicketStatusBadge, { status: 'paused' }))
    expect(screen.getByText('paused')).toBeInTheDocument()

    // 适配函数按 §0 #4 抄下的 wire 属性名：id 可缺、汇报列表读 recent_reports。
    const ticket = toTicketView({
      ticket_id: 't1',
      title: '日常',
      kind: 'general',
      status: 'waiting',
      origin: 'user',
      summary: '等你确认',
      last_report_status: 'waiting',
      human_action: { type: 'confirmation', prompt: '可以用一次理智药吗？', ref_id: 'confirm-1' },
      created_at: '2026-09-27T10:00:00Z',
      updated_at: '2026-09-27T10:01:00Z',
      ended_at: null,
    })
    expect(ticket).toEqual({
      id: 't1',
      title: '日常',
      kind: 'general',
      status: 'waiting',
      origin: 'user',
      summary: '等你确认',
      lastReportStatus: 'waiting',
      humanAction: { type: 'confirmation', prompt: '可以用一次理智药吗？', ref_id: 'confirm-1' },
      createdAt: '2026-09-27T10:00:00Z',
      updatedAt: '2026-09-27T10:01:00Z',
      endedAt: null,
    })

    const detail = toTicketDetailView({
      ticket: { id: 't1', title: '日常', status: 'open' },
      mandate: {
        scope: '刷 1-7',
        strength: 'explicit',
        source: 'user',
        sender: null,
        risk_ceiling: 'consume',
        created_at: '2026-09-27T10:00:00Z',
      },
      recent_reports: [{ id: 3, status: 'progress', summary: '刷到一半', human_action: null, details: null, created_at: '2026-09-27T10:02:00Z' }],
      worker_session_id: 'w1',
      turn_running: true,
    })
    expect(detail.workerSessionId).toBe('w1')
    expect(detail.turnRunning).toBe(true)
    expect(detail.mandate?.riskCeiling).toBe('consume')
    expect(detail.reports).toHaveLength(1)
    expect(detail.reports[0]?.summary).toBe('刷到一半')

    // reports 存在时优先读它。
    expect(toTicketDetailView({
      ticket: { id: 't1', title: '日常' },
      reports: [{ id: 1, status: 'ready', summary: '第一份', created_at: '2026-09-27T10:00:00Z' }],
      recent_reports: [{ id: 2, status: 'ready', summary: '第二份', created_at: '2026-09-27T10:00:00Z' }],
    }).reports.map((report) => report.summary)).toEqual(['第一份'])

    expect(toReportView({
      id: 7,
      status: 'waiting',
      summary: '等你选关卡',
      human_action: { type: 'question', prompt: '用哪个关卡？', ref_id: 'q1' },
      details: { pipeline_id: 'p1' },
      created_at: '2026-09-27T10:00:00Z',
    })).toEqual({
      id: 7,
      status: 'waiting',
      summary: '等你选关卡',
      humanAction: { type: 'question', prompt: '用哪个关卡？', ref_id: 'q1' },
      details: { pipeline_id: 'p1' },
      createdAt: '2026-09-27T10:00:00Z',
    })

    expect(toLeaseView({
      held: true,
      lease: {
        ticket_id: 't1',
        holder_agent: 'general',
        purpose: '刷理智',
        expires_at: '2026-09-27T10:10:00Z',
      },
      waiters: 2,
    })).toEqual({
      held: true,
      ticketId: 't1',
      holderAgent: 'general',
      purpose: '刷理智',
      expiresAt: '2026-09-27T10:10:00Z',
      waiters: 2,
    })

    // 运行节点：run_id 缺了取 id、agent_name 缺了取 agent，children 递归。
    const node = toRunNode({
      id: 'r1',
      agent: 'general',
      kind: 'worker_turn',
      status: 'succeeded',
      steps: 3,
      children: [{ run_id: 'r2', agent_name: 'scout', status: 'running', children: [{ run_id: 'r3' }] }],
    })
    expect(node.runId).toBe('r1')
    expect(node.agent).toBe('general')
    expect(node.children[0]).toMatchObject({ runId: 'r2', agent: 'scout', children: [{ runId: 'r3' }] })
  })

  it('formats run cost and duration', () => {
    expect(formatCost(12_345, 'CNY')).toBe('¥0.0123')
    expect(formatCost(12_345, 'usd')).toBe('0.0123 USD')
    expect(formatCost(12_345, null)).toBe('0.0123')
    expect(formatCost(1_000_000, 'CNY')).toBe('¥1')
    expect(formatCost(0, 'CNY')).toBe('—')

    expect(formatDuration('2026-09-27T10:00:00Z', '2026-09-27T10:01:05Z')).toBe('1 分 05 秒')
    expect(formatDuration('2026-09-27T10:00:00Z', null, Date.parse('2026-09-27T10:01:05Z'))).toBe('1 分 05 秒')
    expect(formatDuration('2026-09-27T10:00:00Z', '2026-09-27T10:00:45Z')).toBe('45 秒')
    expect(formatDuration('2026-09-27T10:00:00Z', '2026-09-27T12:00:00Z')).toBe('2 小时')
    expect(formatDuration(null, null)).toBe('—')
  })
})
