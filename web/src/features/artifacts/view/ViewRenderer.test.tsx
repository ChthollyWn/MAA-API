import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ViewRenderer } from './ViewRenderer'
import { parseView, type FormValues, type ParsedView } from './schema'

/** jsdom 没有 canvas：图表模块 mock 掉，数据表兜底仍然要渲染。 */
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

function viewOf(components: unknown[], overrides: Record<string, unknown> = {}): ParsedView {
  const view = parseView({ version: 1, title: '视图标题', components, ...overrides })
  if (!view) throw new Error('fixture 不是合法视图')
  return view
}

function renderView(view: ParsedView, options: {
  mode?: 'interactive' | 'readonly'
  values?: FormValues
} = {}) {
  return render(
    <ViewRenderer
      view={view}
      mode={options.mode ?? 'interactive'}
      values={options.values ?? {}}
      errors={{}}
      onChange={() => {}}
    />,
  )
}

/** jsdom 没有 ResizeObserver；Radix 开关会用它量尺寸。 */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

describe('M90 视图渲染', () => {
  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders text as plain text without markup', () => {
    const view = viewOf([{ type: 'text', text: '<b>粗</b> 与 **粗**' }])
    const { container } = renderView(view)

    expect(screen.getByText('<b>粗</b> 与 **粗**')).toBeInTheDocument()
    expect(container.querySelector('b')).toBeNull()
    expect(container.querySelector('strong')).toBeNull()
  })

  it('never creates img, iframe or anchor elements from view data', () => {
    const view = viewOf([{
      type: 'text',
      text: '<img src=x onerror=alert(1)> https://evil.example/x.png',
    }])
    const { container } = renderView(view)

    expect(screen.getByText('<img src=x onerror=alert(1)> https://evil.example/x.png')).toBeInTheDocument()
    expect(container.querySelector('img, iframe, a, object, embed')).toBeNull()
  })

  it('does not request anything while rendering', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const view = viewOf([
      { type: 'text', text: 'https://evil.example/x.png' },
      { type: 'stats', items: [{ label: '次数', value: 3 }] },
    ])

    renderView(view)

    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('renders stats, tables and charts with the data table fallback', () => {
    const view = viewOf([
      {
        type: 'stats',
        items: [
          { label: '理智消耗', value: 1234.567, unit: '点', delta: 3, hint: '比上周多' },
          { label: '剩余', value: 64, delta: -2, hint: '还能刷两次' },
          { label: '变化', value: 0, delta: 0 },
        ],
      },
      {
        type: 'table',
        columns: [
          { key: 'stage', label: '关卡' },
          { key: 'rate', label: '占比', align: 'right', format: 'percent' },
          { key: 'enabled', label: '可刷', align: 'right', format: 'text' },
          { key: 'missing', label: '缺值', align: 'right', format: 'text' },
        ],
        rows: [{ stage: '1-7', rate: 42.5, enabled: true, missing: null }],
        caption: '本周数据',
      },
      {
        type: 'chart',
        chart: {
          chart_type: 'bar',
          title: '掉落趋势',
          categories: ['周一', '周二'],
          series: [{ name: '掉落', values: [3, 5] }],
        },
      },
    ])
    renderView(view)

    expect(screen.getByText('1,234.57')).toBeInTheDocument()
    expect(screen.getByText('点')).toBeInTheDocument()
    expect(screen.getByText('+3')).toBeInTheDocument()
    expect(screen.getByText('−2')).toBeInTheDocument()
    expect(screen.queryByText('+0')).not.toBeInTheDocument()
    expect(screen.getByText('比上周多')).toBeInTheDocument()

    expect(screen.getByText('42.5%')).toBeInTheDocument()
    expect(screen.getByText('是')).toBeInTheDocument()
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
    expect(screen.getByText('本周数据')).toBeInTheDocument()

    expect(screen.getByText('掉落趋势')).toBeInTheDocument()
    expect(screen.getByTestId('chart-data')).toBeInTheDocument()
    expect(screen.getByText('数据')).toBeInTheDocument()
  })

  it('disables every field in readonly mode', () => {
    const view = viewOf([
      {
        type: 'field',
        field: 'select',
        name: 'stage',
        label: '关卡',
        options: [{ value: '1-7', label: '1-7' }],
        multiple: false,
      },
      {
        type: 'field',
        field: 'select',
        name: 'tags',
        label: '标签',
        required: false,
        options: [{ value: 'fast', label: '快速' }],
        multiple: true,
      },
      {
        type: 'field',
        field: 'number',
        name: 'count',
        label: '次数',
        required: false,
      },
      {
        type: 'field',
        field: 'text',
        name: 'note',
        label: '备注',
        required: false,
        max_length: 20,
        multiline: true,
      },
      {
        type: 'field',
        field: 'toggle',
        name: 'potion',
        label: '可以用理智药',
        required: false,
      },
      { type: 'submit', label: '提交' },
    ])
    const { container } = renderView(view, {
      mode: 'readonly',
      values: { stage: '1-7', tags: ['fast'], count: '2', note: '只读', potion: true },
    })

    for (const control of Array.from(container.querySelectorAll('input, select, textarea, button'))) {
      expect(control).toBeDisabled()
    }
    expect(screen.queryByRole('button', { name: '提交' })).not.toBeInTheDocument()
    expect(screen.getByDisplayValue('只读')).toBeDisabled()
  })

  it('renders heading levels', () => {
    const view = viewOf([
      { type: 'heading', text: '二级标题', level: 2 },
      { type: 'heading', text: '三级标题', level: 3 },
    ])
    renderView(view)

    expect(screen.getByRole('heading', { level: 3, name: '二级标题' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 4, name: '三级标题' })).toBeInTheDocument()
  })
})
