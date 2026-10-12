import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { OperatorGrid } from './OperatorGrid'
import { OPERATORS, operatorRow } from './test-utils'

/** M43 网格（规格 §4.4）：响应式列数、每行一张卡、透传点击回调。 */

afterEach(() => {
  cleanup()
})

describe('M43 干员卡片网格', () => {
  it('按行渲染卡片，并带上响应式列数', () => {
    render(<OperatorGrid rows={OPERATORS} onSelect={vi.fn()} />)

    const grid = screen.getByTestId('operator-grid')
    expect(grid.className).toContain('grid-cols-2')
    expect(grid.className).toContain('sm:grid-cols-3')
    expect(grid.className).toContain('lg:grid-cols-4')
    expect(grid.className).toContain('xl:grid-cols-6')
    for (const row of OPERATORS) {
      expect(screen.getByTestId(`operator-${row.char_id}`)).toBeInTheDocument()
    }
  })

  it('空列表渲染成空网格，不报错（空状态由页面负责）', () => {
    render(<OperatorGrid rows={[]} onSelect={vi.fn()} />)

    expect(screen.getByTestId('operator-grid').children).toHaveLength(0)
  })

  it('透传点击回调：点哪张卡就把哪一行交出去', () => {
    const onSelect = vi.fn()
    const row = operatorRow({ char_id: 'char_102_texas', name: '德克萨斯' })
    render(<OperatorGrid rows={[row]} onSelect={onSelect} />)

    screen.getByTestId('operator-char_102_texas').click()

    expect(onSelect).toHaveBeenCalledWith(row)
  })
})
