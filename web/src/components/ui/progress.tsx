import { cn } from '@/lib/utils'

export interface ProgressProps {
  /** 当前值；null 表示进度未知。 */
  value: number | null
  /** 最大值，默认 100（百分比）；计数型进度传总数。 */
  max?: number
  label: string
  tone?: 'primary' | 'success' | 'warning' | 'danger'
  className?: string
}

const barTone = {
  primary: 'bg-primary',
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
} as const

export function Progress({ value, max = 100, label, tone = 'primary', className }: ProgressProps) {
  const safeMax = max > 0 ? max : 1
  const clamped = value === null ? null : Math.max(0, Math.min(safeMax, value))
  const percent = clamped === null ? null : Math.round((clamped / safeMax) * 100)
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={safeMax}
      aria-valuenow={clamped ?? undefined}
      className={cn('h-1.5 w-full overflow-hidden rounded-full bg-surface-2', className)}
    >
      <div
        className={cn('h-full rounded-full transition-[width] duration-300 ease-out', barTone[tone], percent === null && 'w-1/3 animate-pulse')}
        style={percent === null ? undefined : { width: `${percent}%` }}
      />
    </div>
  )
}
