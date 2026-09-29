import { afterEach, describe, expect, it } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { StageMapCard } from './MediaCards'
import type { StageMapCardData } from './media'

/** 3×2：row 0 是 `tiles[0]`，按规格 §7 的假设画在最下方。 */
function makeCard(overrides: Partial<StageMapCardData> = {}): StageMapCardData {
  return {
    type: 'stage_map',
    caption: '1-7 的布局',
    code: '1-7',
    name: '暴君',
    stageId: 'main_01-07',
    width: 3,
    height: 2,
    candidates: 2,
    tiles: [
      ['#00', '.21', 'w31'],
      ['.00', '!10', '~00'],
    ],
    start: [[0, 0]],
    end: [[2, 1]],
    legend: [{ code: '!00', label: '特殊地形' }],
    ...overrides,
  }
}

function grid(container: HTMLElement): SVGSVGElement {
  return container.querySelector('[data-testid="stage-map-grid"]') as SVGSVGElement
}

describe('StageMapCard', () => {
  afterEach(cleanup)

  it('draws one rect per tile in an SVG sized by width×height', () => {
    const { container } = render(<StageMapCard card={makeCard()} />)
    const svg = grid(container)
    expect(svg.getAttribute('viewBox')).toBe('0 0 54 36')
    expect(svg.querySelectorAll('rect[data-terrain]')).toHaveLength(6)
    expect(screen.getByText('暴君')).toBeInTheDocument()
    expect(screen.getByText('1-7')).toBeInTheDocument()
    expect(screen.getByText('main_01-07')).toBeInTheDocument()
    expect(screen.getByText('3×2')).toBeInTheDocument()
  })

  it('maps the 3-character tile code to colour, deploy stroke and high-ground dot', () => {
    const { container } = render(<StageMapCard card={makeCard()} />)
    const svg = grid(container)
    const cell = (row: number, column: number) => svg.querySelector(`[data-cell="${row}-${column}"] rect[data-terrain]`)!

    // 地形 → 填充色（只用 --chart-* token）。
    expect(cell(0, 0).getAttribute('fill')).toBe('var(--chart-1)')
    expect(cell(1, 0).getAttribute('fill')).toBe('var(--chart-3)')
    expect(cell(0, 2).getAttribute('fill')).toBe('var(--chart-4)')
    expect(cell(1, 1).getAttribute('fill')).toBe('var(--chart-2)')
    expect(cell(1, 2).getAttribute('fill')).toBe('var(--chart-5)')
    // 可部署类型 → 描边线型；0 只有网格线。
    expect(cell(0, 0).getAttribute('stroke')).toBe('var(--border)')
    expect(cell(0, 0).getAttribute('stroke-dasharray')).toBeNull()
    expect(cell(0, 1).getAttribute('stroke')).toBe('var(--chart-6)')
    expect(cell(0, 1).getAttribute('stroke-dasharray')).toBe('3 2')
    expect(cell(0, 2).getAttribute('stroke-dasharray')).toBe('1 1.5')
    expect(cell(1, 1).getAttribute('stroke-dasharray')).toBeNull()

    // 高度类型 1（高台）多一个圆点。
    expect(svg.querySelectorAll('[data-tile-height="1"]')).toHaveLength(2)

    // 每格都带一句可读的说明（无障碍 + 悬停）。
    expect(svg.querySelector('[data-cell="0-1"] title')?.textContent).toContain('普通地面')
  })

  it('puts tiles[0] at the bottom and marks start/end in different colours', () => {
    const { container } = render(<StageMapCard card={makeCard()} />)
    const svg = grid(container)
    // 行序：tiles[0] 在最下方（y=18），tiles[1] 在上方（y=0）。
    expect(svg.querySelector('[data-cell="0-0"] rect')!.getAttribute('y')).toBe('18')
    expect(svg.querySelector('[data-cell="1-0"] rect')!.getAttribute('y')).toBe('0')

    const start = svg.querySelectorAll('[data-marker="start"]')
    const end = svg.querySelectorAll('[data-marker="end"]')
    expect(start).toHaveLength(1)
    expect(end).toHaveLength(1)
    expect(start[0]!.getAttribute('stroke')).toBe('var(--success)')
    expect(end[0]!.getAttribute('stroke')).toBe('var(--danger)')
    // start [0, 0] 是左下角；end [2, 1] 是右上角。
    expect(start[0]!.getAttribute('cy')).toBe('27')
    expect(end[0]!.getAttribute('cy')).toBe('9')
    expect(screen.getByText('起点')).toBeInTheDocument()
    expect(screen.getByText('终点')).toBeInTheDocument()
  })

  it('renders only the legend the backend sent and hints at duplicate stage codes', () => {
    const { container } = render(<StageMapCard card={makeCard()} />)
    expect(screen.getByText('特殊地形')).toBeInTheDocument()
    expect(container.querySelectorAll('svg[width="14"] rect[data-terrain]')).toHaveLength(1)
    expect(screen.getByText('同名关卡 2 个，已取 main_01-07')).toBeInTheDocument()
    expect(screen.getByText('1-7 的布局')).toBeInTheDocument()
  })

  it('draws legend swatches from the partial codes the backend emits', () => {
    const { container } = render(<StageMapCard card={makeCard({
      legend: [
        { code: '!', label: '特殊机制' },
        { code: '11', label: '可部署类型 1 · 高台' },
        { code: '20', label: '可部署类型 2 · 地面' },
      ],
    })} />)

    const swatches = container.querySelectorAll('svg[width="14"]')
    expect(swatches).toHaveLength(3)
    const rect = (index: number) => swatches[index]!.querySelector('rect[data-terrain]')!
    // 1 字符＝只讲地形：地形色 + 中性网格线，没有可部署信息。
    expect(rect(0).getAttribute('fill')).toBe('var(--chart-2)')
    expect(rect(0).getAttribute('stroke')).toBe('var(--border)')
    expect(swatches[0]!.querySelectorAll('[data-tile-height="1"]')).toHaveLength(0)
    // 2 字符＝可部署+高度：地形留空（中性底色），线型与高台圆点照旧。
    expect(rect(1).getAttribute('fill')).toBe('var(--surface-2)')
    expect(rect(1).getAttribute('stroke')).toBe('var(--chart-6)')
    expect(swatches[1]!.querySelectorAll('[data-tile-height="1"]')).toHaveLength(1)
    expect(rect(2).getAttribute('stroke-dasharray')).toBe('3 2')
    expect(screen.getByText('可部署类型 1 · 高台')).toBeInTheDocument()
  })

  it('hides the candidate hint for an unambiguous stage and keeps an empty legend empty', () => {
    const { container } = render(<StageMapCard card={makeCard({ candidates: 1, legend: [], caption: '', name: '' })} />)
    expect(screen.queryByText(/同名关卡/)).not.toBeInTheDocument()
    expect(container.querySelectorAll('svg[width="14"]')).toHaveLength(0)
    // 没有名字时用编号兜底，卡片不会出现空标题。
    expect(screen.getAllByText('1-7').length).toBeGreaterThanOrEqual(1)
    expect(grid(container)).toBeInTheDocument()
  })
})
