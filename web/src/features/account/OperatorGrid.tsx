import type { OperatorRow } from './api'
import { OperatorCard } from './OperatorCard'

/**
 * 干员卡片网格（M43 规格 §4.4）：移动 2 列 → 平板 3–4 列 → 桌面 6 列。
 *
 * 只负责排布与回调，不碰数据：分页、筛选、编辑抽屉都在 `OperatorsTab` 里。
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
      className="grid list-none grid-cols-2 gap-3 p-0 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6"
    >
      {rows.map((row) => (
        <OperatorCard key={row.char_id} row={row} onSelect={onSelect} />
      ))}
    </ul>
  )
}
