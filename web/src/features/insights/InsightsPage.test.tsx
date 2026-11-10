import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { createConsoleApi, json, renderAt, type Json } from '@/features/console/test-utils'
import type { components } from '@/types/api'
import InsightsPage from './InsightsPage'
import { buildBreakdownChartData, buildTrendChartData } from './chart/insight-chart'

const echarts = vi.hoisted(() => {
  const instance = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }
  return { instance, use: vi.fn(), init: vi.fn(() => instance) }
})

vi.mock('echarts/core', () => ({ use: echarts.use, init: echarts.init }))
vi.mock('echarts/charts', () => ({
  LineChart: { name: 'LineChart' },
  BarChart: { name: 'BarChart' },
  PieChart: { name: 'PieChart' },
}))
vi.mock('echarts/components', () => ({
  GridComponent: { name: 'GridComponent' },
  TooltipComponent: { name: 'TooltipComponent' },
  LegendComponent: { name: 'LegendComponent' },
  TitleComponent: { name: 'TitleComponent' },
}))
vi.mock('echarts/renderers', () => ({ CanvasRenderer: { name: 'CanvasRenderer' } }))

// ---------------------------------------------------------------------------
// 假后端：catalog、overview、trend、breakdown、feedback、evals 列表
// ---------------------------------------------------------------------------

const CATALOG_ITEMS: Json[] = [
  {
    id: 'accuracy',
    label: '准确率',
    scope: 'offline',
    unit: 'ratio',
    definition: '标了 accuracy 的 capability 用例在 record/live 运行中的通过率',
    formula: 'passed / (passed + failed)',
    source: 'eval_case_result、eval_run',
    evaluation: '场景断言：tool_sequence、outcome_state、text_contains 确定性判分',
    evidence: 'measured',
    min_sample: 10,
    limitations: ['replay 只作回归基线，不进入能力口径'],
    supports_trend: true,
    supports_breakdown: false,
  },
  {
    id: 'hallucination_rate',
    label: '幻觉率',
    scope: 'offline',
    unit: 'ratio',
    definition: '标了 hallucination_rate 的 2 个场景中，出现无依据断言的用例比例',
    formula: '1 - passed / (passed + failed)',
    source: 'eval_case_result、eval_run',
    evaluation: '要求只依据资料或明确表示无法确认',
    evidence: 'measured',
    min_sample: 5,
    limitations: ['只覆盖 2 个场景'],
    supports_trend: true,
    supports_breakdown: false,
  },
  {
    id: 'safety_rate',
    label: '安全率',
    scope: 'offline',
    unit: 'ratio',
    definition: '标了 safety_rate 的 6 个场景的通过率',
    formula: 'passed / (passed + failed)',
    source: 'eval_case_result、eval_run',
    evaluation: '攻击场景要求拒绝，对照场景要求正常完成',
    evidence: 'measured',
    min_sample: 6,
    limitations: ['只覆盖 6 个场景'],
    supports_trend: true,
    supports_breakdown: false,
  },
  {
    id: 'task_completion',
    label: '任务完成率',
    scope: 'online',
    unit: 'ratio',
    definition: '窗口内进入终态的工单中 done 的比例',
    formula: 'done / (done + failed + cancelled)',
    source: 'ticket',
    evaluation: 'done 是 Worker 自述，附机器验证子集',
    evidence: 'self_reported',
    min_sample: 20,
    limitations: ['只统计进入终态的工单'],
    supports_trend: true,
    supports_breakdown: true,
  },
  {
    id: 'conversation_efficiency',
    label: '对话效率',
    scope: 'online',
    unit: 'turns',
    definition: '窗口内 done 工单的用户轮次中位数',
    formula: 'agent_inbox 里 user_message 条数的中位数',
    source: 'ticket、agent_inbox、agent_run',
    evaluation: '只对完成工单条件化，中位数抗长尾',
    evidence: 'measured',
    min_sample: 20,
    limitations: ['失败与取消的轮次不计'],
    supports_trend: true,
    supports_breakdown: true,
  },
  {
    id: 'cost',
    label: '成本',
    scope: 'online',
    unit: 'micros',
    definition: '窗口内 done 工单的平均 Agent 成本',
    formula: 'Σ agent_run.cost_micros / done 工单数',
    source: 'agent_run、cost_ledger',
    evaluation: '分币种汇总',
    evidence: 'measured',
    min_sample: 20,
    limitations: ['多币种时不给合并值'],
    supports_trend: true,
    supports_breakdown: true,
  },
  {
    id: 'user_satisfaction',
    label: '用户满意度',
    scope: 'online',
    unit: 'ratio',
    definition: '窗口内新增评价中 rating >= 4 的比例',
    formula: 'positive / total',
    source: 'run_feedback、agent_run',
    evaluation: '运行详情页显式评价 1-5 分',
    evidence: 'self_reported',
    min_sample: 20,
    limitations: ['只覆盖主动评价的运行'],
    supports_trend: true,
    supports_breakdown: true,
  },
]

const CATALOG: Json = { items: CATALOG_ITEMS }

function metricCard(overrides: Json): Json {
  return {
    id: 'accuracy',
    label: '准确率',
    scope: 'offline',
    unit: 'ratio',
    currency: null,
    value: null,
    sample_size: 0,
    ci_low: null,
    ci_high: null,
    delta: null,
    evidence: 'measured',
    updated_at: '2026-11-10T08:00:00Z',
    secondary: [],
    ...overrides,
  }
}

const FULL_CARDS: Json[] = [
  metricCard({
    id: 'accuracy',
    label: '准确率',
    value: 0.8,
    sample_size: 10,
    ci_low: 0.4902,
    ci_high: 0.9433,
    delta: 0.1,
    secondary: [{ label: '可判定用例数', value: 10, unit: 'count', sample_size: 10 }],
  }),
  metricCard({
    id: 'hallucination_rate',
    label: '幻觉率',
    value: 0.1,
    sample_size: 10,
    ci_low: 0.0179,
    ci_high: 0.4045,
    delta: -0.05,
  }),
  metricCard({
    id: 'safety_rate',
    label: '安全率',
    value: 1,
    sample_size: 6,
    ci_low: 0.6093,
    ci_high: 1,
    secondary: [{ label: '攻击场景通过', value: 4, unit: 'count', sample_size: 4 }],
  }),
  metricCard({
    id: 'task_completion',
    label: '任务完成率',
    scope: 'online',
    value: 0.75,
    sample_size: 4,
    ci_low: 0.3006,
    ci_high: 0.9544,
    delta: 0.05,
    evidence: 'self_reported',
    secondary: [{ label: '机器验证通过', value: 0.5, unit: 'ratio', sample_size: 4 }],
  }),
  metricCard({
    id: 'conversation_efficiency',
    label: '对话效率',
    scope: 'online',
    unit: 'turns',
    value: 3,
    sample_size: 4,
    delta: -1,
    evidence: 'measured',
    secondary: [{ label: '每单运行次数中位', value: 2, unit: 'count', sample_size: 4 }],
  }),
  metricCard({
    id: 'cost',
    label: '成本',
    scope: 'online',
    unit: 'micros',
    currency: 'CNY',
    value: 250_000,
    sample_size: 4,
    delta: 100_000,
    evidence: 'measured',
    secondary: [{ label: '窗口总成本', value: 1_000_000, unit: 'micros', sample_size: 4 }],
  }),
  metricCard({
    id: 'user_satisfaction',
    label: '用户满意度',
    scope: 'online',
    value: 1,
    sample_size: 2,
    ci_low: 0.3424,
    ci_high: 1,
    evidence: 'self_reported',
    secondary: [
      { label: '评价数', value: 2, unit: 'count', sample_size: 2 },
      { label: '回收率', value: 0.5, unit: 'ratio', sample_size: 4 },
    ],
  }),
]

const MULTI_CURRENCY_CARDS: Json[] = FULL_CARDS.map((card) =>
  card.id === 'cost'
    ? metricCard({
        id: 'cost',
        label: '成本',
        scope: 'online',
        unit: 'micros',
        currency: null,
        value: null,
        sample_size: 4,
        secondary: [
          { label: 'CNY', value: 500_000, unit: 'micros', sample_size: 3 },
          { label: 'USD', value: 200_000, unit: 'micros', sample_size: 1 },
        ],
      })
    : card,
)

const EMPTY_OFFLINE_CARDS: Json[] = [
  metricCard({
    id: 'accuracy',
    label: '准确率',
    secondary: [
      { label: '可判定用例数', value: 0, unit: 'count', sample_size: 0 },
      { label: 'replay 用例数', value: 26, unit: 'count', sample_size: 26 },
    ],
  }),
  metricCard({ id: 'hallucination_rate', label: '幻觉率' }),
  metricCard({ id: 'safety_rate', label: '安全率' }),
  metricCard({
    id: 'task_completion',
    label: '任务完成率',
    scope: 'online',
    value: 0.5,
    sample_size: 2,
    ci_low: 0.0945,
    ci_high: 0.9055,
    evidence: 'self_reported',
  }),
  metricCard({ id: 'conversation_efficiency', label: '对话效率', scope: 'online', unit: 'turns' }),
  metricCard({ id: 'cost', label: '成本', scope: 'online', unit: 'micros' }),
  metricCard({ id: 'user_satisfaction', label: '用户满意度', scope: 'online' }),
]

type TrendPointFixture = components['schemas']['TrendPointOut']
type BreakdownItemFixture = components['schemas']['BreakdownItemOut']

const TREND_POINTS: TrendPointFixture[] = [
  { date: '2026-11-09', value: 0.5, sample_size: 4, ci_low: 0.2, ci_high: 0.8 },
  { date: '2026-11-10', value: 0.75, sample_size: 8, ci_low: 0.4, ci_high: 0.95 },
]

const BREAKDOWN_ITEMS: BreakdownItemFixture[] = [
  // API 契约：label 是 ticket.kind 原值；界面必须换成 spec title。
  { key: 'daily_ops', label: 'daily_ops', value: 0.8, sample_size: 5, ci_low: 0.4, ci_high: 0.96 },
  { key: 'analyst', label: 'analyst', value: 0.5, sample_size: 2, ci_low: 0.1, ci_high: 0.9 },
]

const EVAL_RUNS: Json = {
  items: [
    {
      id: 'run-1',
      suite: 'capability',
      mode: 'replay',
      started_at: '2026-11-10T08:00:00Z',
      finished_at: '2026-11-10T08:02:05Z',
      duration_ms: 125_000,
      total: 20,
      passed: 15,
      failed: 4,
      error: 1,
      skipped: 0,
      pass_rate: 0.75,
      model_calls: 30,
      cost_micros: 500_000,
      currency: 'CNY',
    },
  ],
  total: 1,
  page: 1,
  size: 10,
}

const FEEDBACK: Json = {
  window_days: 30,
  total: 2,
  positive: 1,
  negative: 1,
  neutral: 0,
  response_rate: 0.5,
  reasons: [{ reason: 'inaccurate', count: 1 }],
  items: [
    {
      id: 1,
      run_id: 'run-1',
      ticket_id: 't1',
      agent_name: 'general',
      rating: 5,
      reasons: ['inaccurate'],
      comment: '结果很准',
      created_at: '2026-11-10T09:00:00Z',
    },
  ],
}

interface InsightsApiOptions {
  cards?: Json[]
  warnings?: Json[]
  trendPoints?: Json[]
  breakdownItems?: Json[]
  evalRuns?: Json
  feedback?: Json
  catalog?: Json
}

function insightsApi(options: InsightsApiOptions = {}) {
  return createConsoleApi({
    specs: [
      { name: 'general', title: '通用 Worker', kind: 'worker', description: '通用工单代理', version: 1 },
      { name: 'daily_ops', title: '执行官', kind: 'worker', description: '日常任务工单代理', version: 1 },
      { name: 'analyst', title: '分析师', kind: 'worker', description: '分析工单代理', version: 1 },
    ],
    overrides: [({ method, path, query }) => {
      if (method !== 'GET') return undefined
      if (path === '/api/agent/insights/catalog') return json(options.catalog ?? CATALOG)
      if (path === '/api/agent/insights/overview') {
        return json({
          window_days: Number(query.get('window_days') ?? 30),
          generated_at: '2026-11-11T00:00:00Z',
          items: options.cards ?? FULL_CARDS,
          warnings: options.warnings ?? [],
        })
      }
      if (path === '/api/agent/insights/trend') {
        return json({
          metric: query.get('metric') ?? 'task_completion',
          scope: query.get('metric') === 'accuracy' ? 'offline' : 'online',
          unit: 'ratio',
          points: options.trendPoints ?? TREND_POINTS,
        })
      }
      if (path === '/api/agent/insights/breakdown') {
        return json({
          metric: query.get('metric') ?? 'task_completion',
          dimension: 'ticket_kind',
          items: options.breakdownItems ?? BREAKDOWN_ITEMS,
          truncated: false,
        })
      }
      if (path === '/api/agent/insights/feedback') return json(options.feedback ?? FEEDBACK)
      if (path === '/api/agent/evals/runs') return json(options.evalRuns ?? EVAL_RUNS)
      return undefined
    }],
  })
}

function renderPage() {
  return renderAt('/more/insights', [{ path: '/more/insights', element: <InsightsPage /> }])
}

function requestsTo(api: ReturnType<typeof insightsApi>, path: string) {
  return api.requests.filter((request) => request.method === 'GET' && request.path === path)
}

describe('M99 能力效果页', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders seven metric cards grouped by scope', async () => {
    insightsApi()
    renderPage()

    const capability = await screen.findByRole('region', { name: '能力指标' })
    expect(within(capability).getAllByRole('article')).toHaveLength(3)
    for (const label of ['准确率', '幻觉率', '安全率']) {
      expect(within(capability).getByRole('article', { name: `${label}指标` })).toBeInTheDocument()
    }
    expect(within(capability).getByText('80.0%')).toBeInTheDocument()
    expect(within(capability).getByText('10.0%')).toBeInTheDocument()
    expect(within(capability).getByText('100.0%')).toBeInTheDocument()
    expect(within(capability).getAllByText(/n=10/)).toHaveLength(2)
    expect(within(capability).getByText(/95% CI 49\.0%–94\.3%/)).toBeInTheDocument()
    expect(within(capability).getByText('+10.0pp')).toBeInTheDocument()
    expect(within(capability).getAllByText('实测')).toHaveLength(3)

    const effect = screen.getByRole('region', { name: '运行效果' })
    expect(within(effect).getAllByRole('article')).toHaveLength(4)
    for (const label of ['任务完成率', '对话效率', '成本', '用户满意度']) {
      expect(within(effect).getByRole('article', { name: `${label}指标` })).toBeInTheDocument()
    }
    expect(within(effect).getByText('75.0%')).toBeInTheDocument()
    expect(within(effect).getByText('3 轮')).toBeInTheDocument()
    // 单币种成本卡带币种符号：主值、delta 与「窗口总成本」都写 ¥。
    expect(within(effect).getByText('¥0.25')).toBeInTheDocument()
    expect(within(effect).getByText('+¥0.1')).toBeInTheDocument()
    expect(within(effect).getByText('¥1')).toBeInTheDocument()
    expect(within(effect).getByText('机器验证通过')).toBeInTheDocument()
    expect(within(effect).getByText('回收率')).toBeInTheDocument()
    expect(within(effect).getAllByText('自述')).toHaveLength(2)
  })

  it('shows empty-data hints for offline metrics', async () => {
    insightsApi({ cards: EMPTY_OFFLINE_CARDS })
    renderPage()

    const capability = await screen.findByRole('region', { name: '能力指标' })
    expect(within(capability).getAllByText('暂无数据')).toHaveLength(3)
    expect(within(capability).getAllByText(/先跑一次 live 评测/)).toHaveLength(3)
    expect(within(capability).getAllByText(/scripts\/agent_eval\.py --suite capability --mode live --publish/)).toHaveLength(3)
    expect(within(capability).queryByText('0.0%')).not.toBeInTheDocument()
    expect(within(capability).getAllByText(/n=0/)).toHaveLength(3)

    const effect = screen.getByRole('region', { name: '运行效果' })
    expect(within(effect).getByText('在运行详情页评价一次后用')).toBeInTheDocument()
  })

  it('keeps n and hides the acquisition hint when samples exist but value is empty', async () => {
    insightsApi({ cards: MULTI_CURRENCY_CARDS })
    renderPage()

    const effect = await screen.findByRole('region', { name: '运行效果' })
    const costCard = within(effect).getByRole('article', { name: '成本指标' })
    expect(within(costCard).getByText('暂无数据')).toBeInTheDocument()
    expect(within(costCard).getByText('n=4')).toBeInTheDocument()
    expect(within(costCard).getByText('CNY')).toBeInTheDocument()
    expect(within(costCard).getByText('USD')).toBeInTheDocument()
    // 多币种时 currency 为空，保持金额不带符号的现状。
    expect(within(costCard).queryByText(/¥|\$/)).not.toBeInTheDocument()
    expect(within(costCard).queryByText('运行产生模型调用后显示')).not.toBeInTheDocument()
  })

  it('switches window and refetches', async () => {
    const api = insightsApi()
    renderPage()

    await screen.findByRole('region', { name: '能力指标' })
    await waitFor(() => expect(requestsTo(api, '/api/agent/insights/overview').some((request) => request.query.get('window_days') === '30')).toBe(true))
    await waitFor(() => expect(requestsTo(api, '/api/agent/insights/breakdown').length).toBeGreaterThan(0))
    expect(requestsTo(api, '/api/agent/insights/overview')[0]?.query.get('window_days')).toBe('30')

    fireEvent.click(screen.getByRole('radio', { name: '90 天' }))

    await waitFor(() => {
      expect(requestsTo(api, '/api/agent/insights/overview').some((request) => request.query.get('window_days') === '90')).toBe(true)
      expect(requestsTo(api, '/api/agent/insights/trend').some((request) => request.query.get('window_days') === '90')).toBe(true)
      expect(requestsTo(api, '/api/agent/insights/breakdown').some((request) => request.query.get('window_days') === '90')).toBe(true)
      expect(requestsTo(api, '/api/agent/insights/feedback').some((request) => request.query.get('window_days') === '90')).toBe(true)
    })
  })

  it('renders trend and breakdown', async () => {
    const api = insightsApi()
    renderPage()

    const trend = await screen.findByRole('region', { name: '趋势' })
    expect(await within(trend).findByText('75.0% · n=8 · 95% CI 40.0%–95.0%')).toBeInTheDocument()
    expect(within(trend).getByText('2 项 · 1 个系列')).toBeInTheDocument()
    expect(within(trend).getByText('数据来源：数据来自评测库与运行账本')).toBeInTheDocument()
    expect(within(trend).getByText('2026-11-10')).toBeInTheDocument()

    const breakdown = screen.getByRole('region', { name: '按工单类型' })
    // 图表与数据表都显示 spec title，不把 ticket.kind 的原值摆到界面上。
    expect(within(breakdown).getByText('执行官')).toBeInTheDocument()
    expect(within(breakdown).getByText('分析师')).toBeInTheDocument()
    expect(within(breakdown).queryByText('daily_ops')).not.toBeInTheDocument()
    expect(within(breakdown).queryByText('analyst')).not.toBeInTheDocument()
    expect(within(breakdown).getByText('2 项 · 1 个系列')).toBeInTheDocument()

    const trendRequests = requestsTo(api, '/api/agent/insights/trend')
    expect(trendRequests.some((request) => request.query.get('metric') === 'task_completion')).toBe(true)
    expect(requestsTo(api, '/api/agent/insights/breakdown').some((request) => request.query.get('metric') === 'task_completion')).toBe(true)

    fireEvent.click(screen.getByRole('radio', { name: '幻觉率' }))
    await waitFor(() => expect(requestsTo(api, '/api/agent/insights/trend').some((request) => request.query.get('metric') === 'hallucination_rate')).toBe(true))
    await waitFor(() => expect(screen.queryByRole('region', { name: '按工单类型' })).not.toBeInTheDocument())
  })

  it('renders recent eval runs and feedback', async () => {
    const api = insightsApi()
    renderPage()

    const evals = await screen.findByRole('region', { name: '离线评测' })
    const link = within(evals).getByRole('link')
    expect(link).toHaveAttribute('href', '/more/evals/run-1')
    expect(within(evals).getByText('能力')).toBeInTheDocument()
    expect(within(evals).getByText('回放')).toBeInTheDocument()
    expect(within(evals).getByText('75.0%')).toBeInTheDocument()
    expect(within(evals).getByText('有错误')).toBeInTheDocument()
    expect(within(evals).getByText('¥0.5')).toBeInTheDocument()
    expect(requestsTo(api, '/api/agent/evals/runs')[0]?.query.get('size')).toBe('10')

    const feedback = screen.getByRole('region', { name: '最近评价' })
    expect(within(feedback).getByRole('img', { name: '评分 5 分' })).toBeInTheDocument()
    expect(within(feedback).getByText('不准确')).toBeInTheDocument()
    expect(within(feedback).getByText('结果很准')).toBeInTheDocument()
    expect(within(feedback).getByText(/工单 t1/)).toBeInTheDocument()
    expect(within(feedback).getByText(/通用 Worker/)).toBeInTheDocument()

    fireEvent.click(link)
    expect(await screen.findByTestId('location')).toHaveTextContent('/more/evals/run-1')
  })

  it('renders evaluation methods', async () => {
    insightsApi()
    renderPage()

    const methods = await screen.findByRole('region', { name: '评估方法' })
    const items = Array.from(methods.querySelectorAll<HTMLLIElement>(':scope > ol > li'))
    expect(items).toHaveLength(7)
    expect(items.map((item) => item.querySelector('summary')?.textContent ?? '')).toEqual([
      expect.stringContaining('准确率'),
      expect.stringContaining('幻觉率'),
      expect.stringContaining('安全率'),
      expect.stringContaining('任务完成率'),
      expect.stringContaining('对话效率'),
      expect.stringContaining('成本'),
      expect.stringContaining('用户满意度'),
    ])

    const first = items[0]!
    const details = first.querySelector('details')
    expect(details?.open).toBe(false)
    fireEvent.click(within(first).getByText('准确率'))
    expect(details?.open).toBe(true)
    expect(within(first).getByText('标了 accuracy 的 capability 用例在 record/live 运行中的通过率')).toBeInTheDocument()
    expect(within(first).getByText('passed / (passed + failed)')).toBeInTheDocument()
    expect(within(first).getByText('eval_case_result、eval_run')).toBeInTheDocument()
    expect(within(first).getByText('实测')).toBeInTheDocument()
    expect(within(first).getByText('最少 10 个样本')).toBeInTheDocument()
    expect(within(first).getByText('replay 只作回归基线，不进入能力口径')).toBeInTheDocument()
  })

  it('maps trend and breakdown points to chart cards', () => {
    const trend = buildTrendChartData(TREND_POINTS, 'ratio')
    expect(trend).toMatchObject({
      type: 'chart',
      chartType: 'line',
      categories: ['2026-11-09', '2026-11-10'],
      series: [{ name: '比例', values: [0.5, 0.75] }],
      source: '数据来自评测库与运行账本',
    })
    expect(trend.caption).toBe('75.0% · n=8 · 95% CI 40.0%–95.0%')

    const breakdown = buildBreakdownChartData(
      BREAKDOWN_ITEMS,
      'ratio',
      new Map([['daily_ops', '执行官'], ['analyst', '分析师']]),
    )
    expect(breakdown).toMatchObject({
      type: 'chart',
      chartType: 'bar',
      categories: ['执行官', '分析师'],
      series: [{ name: '比例', values: [0.8, 0.5] }],
    })

    // 映射里没有的 kind 回退接口给的 label（API 契约仍是 kind）。
    const fallback = buildBreakdownChartData(
      [{ ...BREAKDOWN_ITEMS[0]!, key: 'unknown_kind', label: 'unknown_kind' }],
      'ratio',
      new Map([['daily_ops', '执行官']]),
    )
    expect(fallback.categories).toEqual(['unknown_kind'])
  })
})
