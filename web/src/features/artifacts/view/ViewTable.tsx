import { formatViewNumber } from './ViewStats'
import type { ViewCell, ViewColumn, ViewComponent } from './schema'

function cellText(value: ViewCell, format: ViewColumn['format']): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') {
    return format === 'percent' ? `${formatViewNumber(value)}%` : formatViewNumber(value)
  }
  return value
}

export interface ViewTableProps {
  component: Extract<ViewComponent, { type: 'table' }>
}

/** 只读数据表：第一列是行标题，右对齐列用等宽数字，说明写在表下。 */
export function ViewTable({ component }: ViewTableProps) {
  const { columns, rows, title, caption } = component
  return (
    <div className="min-w-0 space-y-1.5">
      {title ? <p className="text-sm font-medium">{title}</p> : null}
      <div className="max-h-96 overflow-auto rounded-lg border">
        <table className="min-w-full text-sm">
          <thead className="bg-surface-2">
            <tr>
              {columns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={column.align === 'right'
                    ? 'whitespace-nowrap px-3 py-2 text-right font-medium'
                    : 'whitespace-nowrap px-3 py-2 text-left font-medium'}
                >
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex} className="border-t">
                {columns.map((column, columnIndex) => {
                  const text = cellText(row[column.key] ?? null, column.format)
                  const className = column.align === 'right'
                    ? 'whitespace-nowrap px-3 py-2 text-right tabular-nums'
                    : 'px-3 py-2 text-left'
                  return columnIndex === 0 ? (
                    <th key={column.key} scope="row" className={`${className} font-normal`}>{text}</th>
                  ) : (
                    <td key={column.key} className={className}>{text}</td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {caption ? <p className="text-footnote text-muted-foreground">{caption}</p> : null}
    </div>
  )
}
