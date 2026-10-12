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
  it('按行渲染卡片，列宽用 360–500px 的 auto-fit（一行 1–2 张，不挤成窄方块卡）', () => {
    render(<OperatorGrid rows={OPERATORS} onSelect={vi.fn()} />)

    const grid = screen.getByTestId('operator-grid')
    // 窄方块卡会把「精英化」这类词挤成竖排，所以断言列宽下限，而不是列数。
    expect(grid.className).toContain('minmax(min(360px,100%),500px)')
    expect(grid.className).toContain('justify-center')
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

    // `operator-<id>` 是整行容器，可点的方块是 `operator-card-<id>`。
    screen.getByTestId('operator-card-char_102_texas').click()

    expect(onSelect).toHaveBeenCalledWith(row)
  })
})
