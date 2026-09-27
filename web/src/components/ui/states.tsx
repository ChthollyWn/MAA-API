import type { ReactNode } from 'react'
import { AlertCircle, LoaderCircle, type LucideIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export function Spinner({ className }: { className?: string }) {
  return <LoaderCircle aria-hidden="true" className={cn('size-4 animate-spin', className)} />
}

export function LoadingState({ label = '正在加载…', className }: { label?: ReactNode; className?: string }) {
  return (
    <div role="status" className={cn('flex min-h-24 items-center justify-center gap-2 text-sm text-muted-foreground', className)}>
      <Spinner />
      {label}
    </div>
  )
}

export interface EmptyStateProps {
  title: ReactNode
  description?: ReactNode
  icon?: LucideIcon
  action?: ReactNode
  className?: string
}

export function EmptyState({ title, description, icon: Icon, action, className }: EmptyStateProps) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-3 px-6 py-10 text-center', className)}>
      {Icon ? (
        <span className="grid size-12 place-items-center rounded-full bg-surface-2 text-muted-foreground">
          <Icon aria-hidden="true" className="size-6" />
        </span>
      ) : null}
      <div className="space-y-1">
        <p className="text-[0.9375rem] font-semibold">{title}</p>
        {description ? <p className="mx-auto max-w-sm text-footnote text-muted-foreground">{description}</p> : null}
      </div>
      {action ? <div className="mt-1 flex flex-wrap justify-center gap-2">{action}</div> : null}
    </div>
  )
}

export interface ErrorStateProps {
  title?: ReactNode
  message: ReactNode
  onRetry?: () => void
  retrying?: boolean
  retryLabel?: string
  className?: string
}

export function ErrorState({ title, message, onRetry, retrying = false, retryLabel = '重试', className }: ErrorStateProps) {
  return (
    <div className={cn('flex flex-col items-center gap-3 px-6 py-8 text-center', className)}>
      <span className="grid size-11 place-items-center rounded-full bg-danger-soft text-danger">
        <AlertCircle aria-hidden="true" className="size-5" />
      </span>
      <div className="space-y-1">
        {title ? <p className="text-[0.9375rem] font-semibold">{title}</p> : null}
        <p role="alert" className="mx-auto max-w-sm break-words text-footnote text-muted-foreground">{message}</p>
      </div>
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry} loading={retrying}>
          {retrying ? '正在重试…' : retryLabel}
        </Button>
      ) : null}
    </div>
  )
}
