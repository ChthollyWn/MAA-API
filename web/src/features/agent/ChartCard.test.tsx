import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { useUiStore } from '@/stores/ui'
import { ChartCard } from './MediaCards'
import type { ChartCardData } from './media'

/** jsdom 没有 canvas，ECharts 真渲染不了：模块 mock 后只断言 option 由 spec 正确生成。 */
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

function makeCard(overrides: Partial<ChartCardData> = {}): ChartCardData {
  return {
    type: 'chart',
    caption: '最近三天的理智消耗',
    chartType: 'line',
    title: '理智消耗趋势',
    categories: ['周一', '周二', '周三'],
    series: [
      { name: '理智', values: [12, null, 30] },
      { name: '理智药', values: [0, 2, 1] },
    ],
    stacked: false,
    xLabel: '日期',
    yLabel: '数量',
    unit: '个',
    source: 'get_logs(source=maa, limit=3)',
    ...overrides,
  }
}

type LooseOption = Record<string, any>

function lastOption(): LooseOption {
  const calls = echarts.instance.setOption.mock.calls
  return calls.at(-1)?.[0] as LooseOption
}

/** 主题 token 在 jsdom 里读不到 CSS，用 getComputedStyle 替身给出两套可断言的颜色。 */
function stubThemeTokens() {
  const value = (name: string) => {
    const dark = document.documentElement.classList.contains('dark')
    if (name.startsWith('--chart-')) return dark ? ` #dark${name.slice(8)} ` : ` #light${name.slice(8)} `
    return ''
  }
  vi.stubGlobal('getComputedStyle', () => ({ getPropertyValue: value }))
}

describe('ChartCard', () => {
  beforeEach(() => {
    stubThemeTokens()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    act(() => {
      document.documentElement.classList.remove('dark')
      useUiStore.setState({ theme: 'system' })
    })
  })

  it('registers only the pieces it needs and builds a category-axis option from the spec', async () => {
    render(<ChartCard card={makeCard()} />)

    await waitFor(() => expect(echarts.instance.setOption).toHaveBeenCalled())
    expect(echarts.use).toHaveBeenCalledTimes(1)
    expect((echarts.use.mock.calls[0]![0] as Array<{ name: string }>).map((item) => item.name)).toEqual([
      'LineChart', 'BarChart', 'PieChart', 'GridComponent', 'TooltipComponent', 'LegendComponent', 'TitleComponent', 'CanvasRenderer',
    ])

    const option = lastOption()
    expect(option.tooltip.trigger).toBe('axis')
    expect(option.xAxis.type).toBe('category')
    expect(option.xAxis.data).toEqual(['周一', '周二', '周三'])
    expect(option.yAxis.name).toBe('数量（个）')
    expect(option.grid.containLabel).toBe(true)
    expect(option.series.map((series: LooseOption) => series.type)).toEqual(['line', 'line'])
    // 断点保留成 null，不写成 0。
    expect(option.series[0].data).toEqual([12, null, 30])
    expect(option.series[0].connectNulls).toBe(false)
    // 多系列才有图例；颜色只来自 --chart-*。
    expect(option.legend.show).toBe(true)
    expect(option.color).toEqual(['#light1', '#light2', '#light3', '#light4', '#light5', '#light6'])
    expect(screen.getByText('数据来源：get_logs(source=maa, limit=3)')).toBeInTheDocument()
  })

  it('uses a single hidden legend for one series and stacks bars on request', async () => {
    render(<ChartCard card={makeCard({ chartType: 'bar', stacked: true, series: [{ name: '数量', values: [1, 2, 3] }] })} />)

    await waitFor(() => expect(echarts.instance.setOption).toHaveBeenCalled())
    const option = lastOption()
    expect(option.legend.show).toBe(false)
    expect(option.series[0].type).toBe('bar')
    expect(option.series[0].stack).toBe('total')
    expect(option.series[0].barMaxWidth).toBe(32)
  })

  it('draws a donut from the first series only and triggers the tooltip per item', async () => {
    render(<ChartCard card={makeCard({
      chartType: 'pie',
      categories: ['固源岩', '龙门币'],
      series: [
        { name: '掉落', values: [12, 3] },
        { name: '被忽略的系列', values: [9, 9] },
      ],
    })} />)

    await waitFor(() => expect(echarts.instance.setOption).toHaveBeenCalled())
    const option = lastOption()
    expect(option.tooltip.trigger).toBe('item')
    expect(option.series).toHaveLength(1)
    expect(option.series[0].type).toBe('pie')
    expect(option.series[0].radius).toEqual(['40%', '70%'])
    expect(option.series[0].data).toEqual([
      { name: '固源岩', value: 12 },
      { name: '龙门币', value: 3 },
    ])
    expect(option.legend.show).toBe(true)
  })

  it('rebuilds the option from the new palette when the theme changes', async () => {
    render(<ChartCard card={makeCard()} />)
    await waitFor(() => expect(lastOption().color).toEqual(['#light1', '#light2', '#light3', '#light4', '#light5', '#light6']))
    const before = echarts.instance.setOption.mock.calls.length

    act(() => useUiStore.getState().setTheme('dark'))

    await waitFor(() => expect(lastOption().color).toEqual(['#dark1', '#dark2', '#dark3', '#dark4', '#dark5', '#dark6']))
    expect(echarts.instance.setOption.mock.calls.length).toBeGreaterThan(before)
    // 重建用的还是同一份规格，只有颜色变了。
    expect(lastOption().xAxis.data).toEqual(['周一', '周二', '周三'])
  })

  it('keeps the data table available and shows it when init throws', async () => {
    echarts.init.mockImplementationOnce(() => {
      throw new Error('canvas 不可用')
    })
    render(<ChartCard card={makeCard()} />)

    expect(await screen.findByText('图表加载失败，已改用数据表。')).toBeInTheDocument()
    const details = screen.getByTestId('chart-data')
    expect(details).toHaveAttribute('open')
    const table = details.querySelector('table')!
    expect([...table.querySelectorAll('th[scope="col"]')].map((item) => item.textContent)).toEqual(['日期', '理智', '理智药'])
    expect([...table.querySelectorAll('th[scope="row"]')].map((item) => item.textContent)).toEqual(['周一', '周二', '周三'])
    // null 断点在表里是 —，不是 0。
    expect(within(table).getByText('—')).toBeInTheDocument()
    expect(within(table).getByText('30')).toBeInTheDocument()
  })

  it('always renders the data table, collapsed by default', async () => {
    render(<ChartCard card={makeCard({ chartType: 'pie', categories: ['固源岩', '龙门币'], series: [{ name: '掉落', values: [12, 3] }, { name: '另一个系列', values: [1, 1] }] })} />)

    await waitFor(() => expect(echarts.instance.setOption).toHaveBeenCalled())
    const details = screen.getByTestId('chart-data')
    expect(details).not.toHaveAttribute('open')
    expect(within(details).getByText('数据')).toBeInTheDocument()
    // 饼图只画第一条系列，表也只列它。
    const table = details.querySelector('table')!
    expect([...table.querySelectorAll('th[scope="col"]')].map((item) => item.textContent)).toEqual(['日期', '掉落'])
  })

  it('falls back to the data table when the echarts chunk cannot be loaded', async () => {
    vi.resetModules()
    vi.doMock('echarts/core', () => {
      throw new Error('chunk 加载失败')
    })
    const { ChartCard: FreshChartCard } = await import('./MediaCards')
    render(<FreshChartCard card={makeCard()} />)

    expect(await screen.findByText('图表加载失败，已改用数据表。')).toBeInTheDocument()
    expect(screen.getByTestId('chart-data')).toHaveAttribute('open')
  })
})
