import * as React from 'react'
import { cn } from '@/lib/utils'

/** 输入控件共用的外观；字号保持 16px，避免 iOS 聚焦时自动放大页面。 */
export const controlClassName =
  'w-full min-w-0 rounded-lg border border-input bg-surface text-base text-foreground transition-[border-color,box-shadow] placeholder:text-muted-foreground focus-visible:border-primary focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-danger aria-invalid:ring-danger/15 lg:text-[0.9375rem]'

/** 只读外观只给可编辑的文本控件：`<select>` 在浏览器里恒为 :read-only，不能共用。 */
export const readOnlyClassName = 'read-only:bg-surface-2'

const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<'input'>>(
  ({ className, type, ...props }, ref) => (
    <input ref={ref} type={type} className={cn(controlClassName, readOnlyClassName, 'flex h-11 px-3', className)} {...props} />
  ),
)
Input.displayName = 'Input'

export { Input }
