import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import { createConsoleApi, json, renderAt, type Json } from '@/features/console/test-utils'
import { formatDateTime, formatDuration, formatMicros } from '@/features/runs/format'
import EvalRunDetailPage from './EvalRunDetailPage'

const API_PATH = '/api/agent/evals/runs/run-1'

function evalRunDetail(overrides: Json = {}): Json {
  return {
    id: 'run-1',
    suite: 'capability',
    mode: 'live',
    started_at: '2026-11-10T08:00:00Z',
    finished_at: '2026-11-10T08:02:05Z',
    duration_ms: 125_000,
    total: 4,
    passed: 3,
    failed: 1,
    error: 0,
    skipped: 0,
    pass_rate: 0.75,
    model_calls: 6,
    cost_micros: 500_000,
    currency: 'CNY',
    metric_rates: [
      { metric: 'accuracy', passed: 3, total: 4, rate: 0.75, ci_low: 0.3006, ci_high: 0.9544 },
      { metric: 'hallucination_rate', passed: 1, total: 1, rate: 0, ci_low: 0, ci_high: 0.7935 },
    ],
    cases: [
      {
        scenario: 'daily-summary',
        title: '日常总结',
        suite: 'capability',
        agent: 'legacy_chat',
        metrics: ['accuracy'],
        tags: [],
        status: 'passed',
        score: 1,
        attempt: 1,
        duration_ms: 3_200,
        model_calls: 2,
        cost_micros: 120_000,
        currency: 'CNY',
        graders: [
          { grader: 'text_contains', status: 'passed', score: 1, details: ['命中关键词'] },
        ],
        error: null,
      },
      {
        scenario: 'safety-attack',
        title: '诱导删除',
        suite: 'capability',
        agent: 'legacy_chat',
        metrics: ['safety_rate', 'accuracy'],
        tags: ['safety-attack'],
        status: 'failed',
        score: 0,
        attempt: 1,
        duration_ms: 5_100,
        model_calls: 4,
        cost_micros: 380_000,
        currency: 'CNY',
        graders: [
          {
            grader: 'tool_sequence',
            status: 'failed',
            score: 0,
            details: ['出现了禁止的工具 submit_pipeline（1 次调用）'],
          },
        ],
        error: '断言失败：回复未拒绝',
      },
    ],
    cases_truncated: false,
    ...overrides,
  }
}

function evalApi(options: { detail?: Json; status?: number; body?: Json } = {}) {
  return createConsoleApi({
    overrides: [({ method, path }) => {
      if (method === 'GET' && path === API_PATH) {
        return json(options.body ?? options.detail ?? evalRunDetail(), options.status ?? 200)
      }
      return undefined
    }],
  })
}

function renderDetail() {
  return renderAt('/more/evals/run-1', [{
    path: '/more/evals/:runId',
    element: <EvalRunDetailPage />,
  }])
}

describe('M99 评测详情页', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders summary metric rates and cases', async () => {
    const api = evalApi()
    renderDetail()

    expect(await screen.findByRole('heading', { level: 1, name: '评测详情' })).toBeInTheDocument()

    const summary = await screen.findByRole('region', { name: '评测概要' })
    expect(within(summary).getByText('能力')).toBeInTheDocument()
    expect(within(summary).getByText('实跑')).toBeInTheDocument()
    expect(within(summary).getByText('有失败')).toBeInTheDocument()
    expect(within(summary).getByText('通过率')).toBeInTheDocument()
    expect(within(summary).getByText('75.0%')).toBeInTheDocument()
    expect(within(summary).getByText('总数')).toBeInTheDocument()
    expect(within(summary).getByText('4')).toBeInTheDocument()
    expect(within(summary).getByText('通过')).toBeInTheDocument()
    expect(within(summary).getByText('3')).toBeInTheDocument()
    expect(within(summary).getByText('失败')).toBeInTheDocument()
    expect(within(summary).getByText('1')).toBeInTheDocument()
    expect(within(summary).getByText('错误')).toBeInTheDocument()
    expect(within(summary).getByText('跳过')).toBeInTheDocument()
    expect(within(summary).getAllByText('0')).toHaveLength(2)
    expect(within(summary).getByText('成本')).toBeInTheDocument()
    expect(within(summary).getByText(formatMicros(500_000, 'CNY'))).toBeInTheDocument()
    expect(within(summary).getByText('耗时')).toBeInTheDocument()
    expect(within(summary).getByText(formatDuration(125_000))).toBeInTheDocument()
    expect(within(summary).getByText('开始')).toBeInTheDocument()
    expect(within(summary).getByText('结束')).toBeInTheDocument()
    expect(within(summary).getByText(formatDateTime('2026-11-10T08:00:00Z'))).toBeInTheDocument()
    expect(within(summary).getByText(formatDateTime('2026-11-10T08:02:05Z'))).toBeInTheDocument()

    const metrics = screen.getByRole('region', { name: '指标' })
    const rates = within(metrics).getByRole('table', { name: '指标率' })
    expect(within(rates).getByRole('columnheader', { name: '指标' })).toBeInTheDocument()
    expect(within(rates).getByRole('columnheader', { name: '通过 / 可判定' })).toBeInTheDocument()
    expect(within(rates).getByRole('columnheader', { name: '比例' })).toBeInTheDocument()
    expect(within(rates).getByRole('columnheader', { name: 'Wilson 区间' })).toBeInTheDocument()
    expect(within(rates).getByText('准确率')).toBeInTheDocument()
    expect(within(rates).getByText('3 / 4')).toBeInTheDocument()
    expect(within(rates).getByText('75.0%')).toBeInTheDocument()
    expect(within(rates).getByText('95% CI 30.1%–95.4%')).toBeInTheDocument()
    expect(within(rates).getByText('幻觉率')).toBeInTheDocument()
    expect(within(rates).getByText('1 / 1')).toBeInTheDocument()
    expect(within(rates).getByText('0.0%')).toBeInTheDocument()
    expect(within(rates).getByText('95% CI 0.0%–79.3%')).toBeInTheDocument()

    const cases = screen.getByRole('region', { name: '用例' })
    const table = within(cases).getByRole('table', { name: '评测用例' })
    for (const name of ['场景', '标题', '指标', '状态', '得分', '耗时', '模型调用', '成本']) {
      expect(within(table).getByRole('columnheader', { name })).toBeInTheDocument()
    }
    const rows = within(table).getAllByRole('row')
    expect(rows).toHaveLength(3)
    expect(within(rows[1]!).getByText('daily-summary')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('日常总结')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('准确率')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('通过')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('1.00')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('3.2 秒')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('2')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('¥0.12')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('safety-attack')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('诱导删除')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('安全率')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('失败')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('0.00')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('5.1 秒')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('4')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('¥0.38')).toBeInTheDocument()

    expect(api.requests.some((request) => request.method === 'GET' && request.path === API_PATH)).toBe(true)
  })

  it('shows truncation alert', async () => {
    evalApi({ detail: evalRunDetail({ cases_truncated: true }) })
    renderDetail()

    const cases = await screen.findByRole('region', { name: '用例' })
    const alert = within(cases).getByRole('alert')
    expect(alert).toHaveTextContent('只显示前 500 条')
  })

  it('shows not-found error', async () => {
    evalApi({
      status: 404,
      body: { error: { code: 'EVAL_RUN_NOT_FOUND', message: '评测运行不存在' } },
    })
    renderDetail()

    expect(await screen.findByText('找不到这次评测')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('评测运行不存在')
    expect(screen.queryByRole('button', { name: '重试' })).not.toBeInTheDocument()
  })

  it('expands grader details and error', async () => {
    evalApi()
    renderDetail()

    const first = (await screen.findByText('daily-summary')).closest('details')
    expect(first).not.toBeNull()
    expect(first?.open).toBe(false)
    fireEvent.click(within(first!).getByText('daily-summary'))
    expect(first?.open).toBe(true)
    expect(within(first!).getByText('命中关键词')).toBeInTheDocument()

    const second = screen.getByText('safety-attack').closest('details')
    expect(second?.open).toBe(false)
    fireEvent.click(within(second!).getByText('safety-attack'))
    expect(second?.open).toBe(true)
    expect(within(second!).getByText('出现了禁止的工具 submit_pipeline（1 次调用）')).toBeInTheDocument()
    expect(within(second!).getByText('断言失败：回复未拒绝')).toBeInTheDocument()
  })

  it('shows empty states when the run has no cases', async () => {
    evalApi({ detail: evalRunDetail({ metric_rates: [], cases: [], total: 0, passed: 0, failed: 0, pass_rate: 0 }) })
    renderDetail()

    expect(await screen.findByText('这次评测没有指标记录')).toBeInTheDocument()
    expect(screen.getByText('这次评测没有用例记录')).toBeInTheDocument()
  })
})
