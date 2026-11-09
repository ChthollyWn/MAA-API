import type { ViewStat } from './schema'

const numberFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 })

/** 视图里的数字展示：整数不带小数点，最多两位小数。 */
export function formatViewNumber(value: number): string {
  return numberFormat.format(value)
}

function deltaText(delta: number): string {
  return `${delta > 0 ? '+' : '−'}${formatViewNumber(Math.abs(delta))}`
}

export interface ViewStatsProps {
  items: readonly ViewStat[]
}

/** 指标行：`dl` 网格，标签在上、值、单位、变化与说明在下。 */
export function ViewStats({ items }: ViewStatsProps) {
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {items.map((item, index) => (
        <div key={`${item.label}-${index}`} className="min-w-0 rounded-xl border bg-card px-3 py-2.5">
          <dt className="truncate text-footnote text-muted-foreground">{item.label}</dt>
          <dd className="mt-0.5 flex flex-wrap items-baseline gap-x-1">
            <span className="text-title font-semibold tabular-nums">
              {typeof item.value === 'number' ? formatViewNumber(item.value) : item.value}
            </span>
            {item.unit ? <span className="text-footnote text-muted-foreground">{item.unit}</span> : null}
            {item.delta !== null && item.delta !== 0 ? (
              <span className={item.delta > 0 ? 'text-footnote font-medium text-success' : 'text-footnote font-medium text-danger'}>
                {deltaText(item.delta)}
              </span>
            ) : null}
          </dd>
          {item.hint ? <p className="mt-0.5 break-words text-footnote text-muted-foreground">{item.hint}</p> : null}
        </div>
      ))}
    </dl>
  )
}
