import { cn } from '@/lib/utils'

export interface ProgressProps {
  /** 0–100；null 表示进度未知。 */
  value: number | null
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

export function Progress({ value, label, tone = 'primary', className }: ProgressProps) {
  const clamped = value === null ? null : Math.max(0, Math.min(100, Math.round(value)))
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped ?? undefined}
      className={cn('h-1.5 w-full overflow-hidden rounded-full bg-surface-2', className)}
    >
      <div
        className={cn('h-full rounded-full transition-[width] duration-300 ease-out', barTone[tone], clamped === null && 'w-1/3 animate-pulse')}
        style={clamped === null ? undefined : { width: `${clamped}%` }}
      />
    </div>
  )
}
