import type { ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, Info, XCircle, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

export type AlertTone = 'info' | 'success' | 'warning' | 'danger'

const toneStyle: Record<AlertTone, { className: string; icon: LucideIcon }> = {
  info: { className: 'bg-primary-soft text-foreground [&_[data-alert-icon]]:text-primary-text', icon: Info },
  success: { className: 'bg-success-soft text-foreground [&_[data-alert-icon]]:text-success', icon: CheckCircle2 },
  warning: { className: 'bg-warning-soft text-foreground [&_[data-alert-icon]]:text-warning', icon: AlertTriangle },
  danger: { className: 'bg-danger-soft text-foreground [&_[data-alert-icon]]:text-danger', icon: XCircle },
}

export interface InlineAlertProps {
  tone?: AlertTone
  title?: ReactNode
  children?: ReactNode
  /** 右侧或底部的操作按钮。 */
  action?: ReactNode
  /** 默认：danger / warning 为 alert，其余为 status。 */
  role?: 'alert' | 'status' | 'none'
  icon?: LucideIcon | null
  className?: string
}

export function InlineAlert({ tone = 'info', title, children, action, role, icon, className }: InlineAlertProps) {
  const style = toneStyle[tone]
  const Icon = icon === null ? null : icon ?? style.icon
  const resolvedRole = role ?? (tone === 'danger' || tone === 'warning' ? 'alert' : 'status')
  return (
    <div
      role={resolvedRole === 'none' ? undefined : resolvedRole}
      className={cn('flex flex-wrap items-start gap-x-3 gap-y-2 rounded-xl px-3.5 py-3 text-sm', style.className, className)}
    >
      {Icon ? <Icon data-alert-icon aria-hidden="true" className="mt-0.5 size-[1.125rem] shrink-0" /> : null}
      <div className="min-w-0 flex-1 space-y-0.5 break-words">
        {title ? <p className="font-semibold">{title}</p> : null}
        {children ? <div className={cn(title && 'text-muted-foreground')}>{children}</div> : null}
      </div>
      {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
    </div>
  )
}
