import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex max-w-full shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium leading-5 [&_svg]:size-3.5 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        primary: 'bg-primary-soft text-primary-text',
        neutral: 'bg-surface-2 text-muted-foreground',
        success: 'bg-success-soft text-success',
        warning: 'bg-warning-soft text-warning',
        danger: 'bg-danger-soft text-danger',
        outline: 'border border-border text-muted-foreground',
      },
    },
    defaultVariants: { variant: 'neutral' },
  },
)

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}

export type StatusTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger'

const dotTone: Record<StatusTone, string> = {
  neutral: 'bg-muted-foreground/60',
  primary: 'bg-primary',
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
}

/** 状态圆点；pulse 用于「进行中」。颜色之外必须另有文字说明状态。 */
function StatusDot({ tone = 'neutral', pulse = false, className }: { tone?: StatusTone; pulse?: boolean; className?: string }) {
  return (
    <span aria-hidden="true" className={cn('relative inline-flex size-2 shrink-0 rounded-full', dotTone[tone], className)}>
      {pulse ? <span className={cn('absolute inset-0 animate-ping rounded-full opacity-60', dotTone[tone])} /> : null}
    </span>
  )
}

export { Badge, badgeVariants, StatusDot }
