import type { OperatorRow } from './api'
import { OperatorCard } from './OperatorCard'

/**
 * 干员卡片网格（M43 规格 §4.4，按用户反馈改成宽横卡）。
 *
 * 列宽用参考实现的同一套口径：`repeat(auto-fit, minmax(360px, 500px))` + 居中 —— 一行
 * 排 1–2 张（宽屏 2–3 张），每张至少 360px。窄方块卡那种 2–6 列网格会把「精英化」
 * 「技能等级」这些词挤成竖排，所以不做。
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
      className="grid list-none justify-center gap-3 p-0 [grid-template-columns:repeat(auto-fit,minmax(min(360px,100%),500px))]"
    >
      {rows.map((row) => (
        <OperatorCard key={row.char_id} row={row} onSelect={onSelect} />
      ))}
    </ul>
  )
}
