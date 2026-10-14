import type { OperatorRow } from './api'
import { OperatorCard } from './OperatorCard'

/**
 * 干员卡片网格（M43 规格 §4.4）。
 *
 * 移动端单列撑满、桌面端双列等宽对齐，与页面容器边缘保持一致；
 * 只负责排布与回调，分页、筛选、编辑抽屉都在 `OperatorsTab` 里。
 */
export function OperatorGrid({
  rows,
  onSelect,
}: {
  rows: OperatorRow[]
  onSelect: (row: OperatorRow) => void
}) {
  return (
    <ul
      aria-label="干员列表"
      data-testid="operator-grid"
      className="grid list-none grid-cols-1 gap-3 p-0 md:grid-cols-2"
    >
      {rows.map((row) => (
        <OperatorCard key={row.char_id} row={row} onSelect={onSelect} />
      ))}
    </ul>
  )
}
