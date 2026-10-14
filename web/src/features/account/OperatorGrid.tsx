import type { OperatorRow } from './api'
import { OperatorCard } from './OperatorCard'

/**
 * 干员卡片网格（M43 规格 §4.4，M47 只读版）。
 *
 * 移动端单列撑满、桌面端双列等宽对齐，与页面容器边缘保持一致；
 * 只负责排布，分页与筛选都在 `OperatorsTab` 里。M47 起卡片没有点击行为
 * （编辑抽屉已下线），所以这里也不再传 `onSelect`。
 */
export function OperatorGrid({ rows }: { rows: OperatorRow[] }) {
  return (
    <ul
      aria-label="干员列表"
      data-testid="operator-grid"
      className="grid list-none grid-cols-1 gap-3 p-0 md:grid-cols-2"
    >
      {rows.map((row) => (
        <OperatorCard key={row.char_id} row={row} />
      ))}
    </ul>
  )
}
