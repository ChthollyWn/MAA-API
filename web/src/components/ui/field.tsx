import type { ReactNode } from 'react'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'

export interface FieldProps {
  label: ReactNode
  htmlFor?: string
  /** 标签右侧的小字或按钮，例如「默认」「已修改」「恢复默认」。 */
  labelAside?: ReactNode
  description?: ReactNode
  descriptionId?: string
  error?: ReactNode
  errorId?: string
  children: ReactNode
  className?: string
}

/** 标签在上、控件在下、说明与错误紧贴控件的表单字段。 */
export function Field({ label, htmlFor, labelAside, description, descriptionId, error, errorId, children, className }: FieldProps) {
  return (
    <div className={cn('space-y-1.5', className)}>
      <div className="flex min-h-5 items-center justify-between gap-2">
        <Label htmlFor={htmlFor}>{label}</Label>
        {labelAside ? <div className="flex shrink-0 items-center gap-1.5">{labelAside}</div> : null}
      </div>
      {children}
      {description ? <p id={descriptionId} className="text-footnote text-muted-foreground">{description}</p> : null}
      {error ? <p id={errorId} className="text-footnote font-medium text-danger">{error}</p> : null}
    </div>
  )
}
