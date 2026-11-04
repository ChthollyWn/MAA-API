import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { CostsPage } from '@/features/costs/CostsPage'
import { costRange } from '@/features/costs/format'
import { useAuth } from '@/stores/auth'

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

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function toRequest(input: RequestInfo | URL, init?: RequestInit): Request {
  return input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
}

const specs = {
  items: [
    { name: 'orchestrator', title: '总控', description: '总控代理', kind: 'orchestrator', model_role: 'orchestrator', spec_version: 'spec-orchestrator', version: 1, tools: [], delegates: ['worker'] },
  ],
}

function groupsFor(groupBy: string | null) {
  const key = groupBy === 'agent' ? 'orchestrator' : groupBy === 'model' ? 'deepseek-chat' : groupBy === 'role' ? 'worker' : '2026-11-02'
  return [{ key, calls: 2, prompt_tokens: 1000, cached_tokens: 500, completion_tokens: 200, cost_micros: 1_500_000 }]
}

function installMock(options: { todayMicros?: number; limit?: number | null; warnRatio?: number } = {}) {
  const urls: URL[] = []
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = new URL(toRequest(input, init).url)
    urls.push(url)
    if (url.pathname === '/api/agent/specs') return json(specs)
    if (url.pathname === '/api/settings/schema') return json({ items: [] })
    if (url.pathname === '/api/settings') {
      return json({
        items: {
          'budget.daily_cost_limit': { value: options.limit ?? 50, source: 'db' },
          'budget.warn_ratio': { value: options.warnRatio ?? 0.8, source: 'db' },
        },
      })
    }
    if (url.pathname === '/api/agent/costs') {
      const currency = url.searchParams.get('currency')
      if (currency === 'CNY') {
        return json({
          currency: 'CNY',
          total_micros: options.todayMicros ?? 30_000_000,
          groups: [{ key: '2026-11-04', calls: 1, prompt_tokens: 100, cached_tokens: 50, completion_tokens: 10, cost_micros: options.todayMicros ?? 30_000_000 }],
        })
      }
      return json({ currency: 'CNY', total_micros: 1_500_000, groups: groupsFor(url.searchParams.get('group_by')) })
    }
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  return { fetchMock, urls }
}

function renderCosts(fetchMock: typeof fetch) {
  vi.stubGlobal('fetch', fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/more/costs']}>
        <CostsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function costQueries(urls: URL[]) {
  return urls.filter((url) => url.pathname === '/api/agent/costs' && url.searchParams.get('currency') !== 'CNY')
}

describe('Costs page', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuth.getState().clearToken()
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('switches range and group', async () => {
    const { fetchMock, urls } = installMock()
    renderCosts(fetchMock)

    expect(await screen.findByRole('heading', { name: '成本', level: 1 })).toBeInTheDocument()
    expect(await screen.findByText('50%')).toBeInTheDocument()
    await waitFor(() => expect(costQueries(urls).some((url) => url.searchParams.get('group_by') === 'day')).toBe(true))
    const initial = costQueries(urls).find((url) => url.searchParams.get('group_by') === 'day')!
    expect(initial.searchParams.get('since')).toBe(costRange('today').since)

    fireEvent.click(screen.getByRole('radio', { name: '近 7 天' }))
    await waitFor(() => expect(costQueries(urls).some((url) => (
      url.searchParams.get('group_by') === 'day' && url.searchParams.get('since') === costRange('7d').since
    ))).toBe(true))

    fireEvent.click(screen.getByRole('radio', { name: '按代理' }))
    await waitFor(() => expect(costQueries(urls).some((url) => url.searchParams.get('group_by') === 'agent')).toBe(true))
    expect(await screen.findByText('总控')).toBeInTheDocument()
    expect(screen.queryByText('orchestrator')).not.toBeInTheDocument()
  })

  it('shows cache hit ratio', async () => {
    const { fetchMock } = installMock()
    renderCosts(fetchMock)

    const summary = await screen.findByRole('region', { name: '成本汇总' })
    expect(within(summary).getByText('总费用')).toBeInTheDocument()
    expect(within(summary).getByText('¥1.5')).toBeInTheDocument()
    expect(within(summary).getByText('调用次数')).toBeInTheDocument()
    expect(within(summary).getByText('2')).toBeInTheDocument()
    expect(within(summary).getByText('缓存命中率')).toBeInTheDocument()
    expect(within(summary).getByText('50%')).toBeInTheDocument()
  })

  it('warns at warn ratio', async () => {
    const { fetchMock } = installMock({ todayMicros: 45_000_000, limit: 50, warnRatio: 0.8 })
    renderCosts(fetchMock)

    const today = await screen.findByRole('region', { name: '今日费用' })
    await waitFor(() => expect(within(today).getByText('已接近每日上限')).toBeInTheDocument())
    expect(within(today).getByText('¥45')).toBeInTheDocument()
    expect(within(today).getByText('¥50')).toBeInTheDocument()
  })

  it('shows unlimited when limit is zero', async () => {
    const { fetchMock } = installMock({ todayMicros: 45_000_000, limit: 0 })
    renderCosts(fetchMock)

    const today = await screen.findByRole('region', { name: '今日费用' })
    await waitFor(() => expect(within(today).getByText('不限')).toBeInTheDocument())
    expect(within(today).queryByText('已接近每日上限')).not.toBeInTheDocument()
  })
})
