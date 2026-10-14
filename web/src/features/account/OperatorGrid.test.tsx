import { afterEach, describe, expect, it } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { OperatorGrid } from './OperatorGrid'
import { OPERATORS } from './test-utils'

/** M43 网格（规格 §4.4）：响应式列数、每行一张卡；M47 起卡片只读，没有点击回调。 */

afterEach(() => {
  cleanup()
})

describe('M43 干员卡片网格', () => {
  it('按行渲染卡片，移动端单列、桌面端双列等宽对齐', () => {
    render(<OperatorGrid rows={OPERATORS} />)

    const grid = screen.getByTestId('operator-grid')
    expect(grid.className).toContain('grid-cols-1')
    expect(grid.className).toContain('md:grid-cols-2')
    for (const row of OPERATORS) {
      expect(screen.getByTestId(`operator-${row.char_id}`)).toBeInTheDocument()
    }
  })

  it('空列表渲染成空网格，不报错（空状态由页面负责）', () => {
    render(<OperatorGrid rows={[]} />)

    expect(screen.getByTestId('operator-grid').children).toHaveLength(0)
  })
})
