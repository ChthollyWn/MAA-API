import * as React from 'react'
import { ChevronDown } from 'lucide-react'
import { controlClassName } from '@/components/ui/input'
import { cn } from '@/lib/utils'

export interface NativeSelectProps extends React.ComponentProps<'select'> {
  wrapperClassName?: string
}

/** 原生下拉：手机上会调出系统滚轮，比自绘下拉更好按，只统一外观。 */
const NativeSelect = React.forwardRef<HTMLSelectElement, NativeSelectProps>(
  ({ className, wrapperClassName, children, ...props }, ref) => (
    <div className={cn('relative min-w-0', wrapperClassName)}>
      <select ref={ref} className={cn(controlClassName, 'h-11 appearance-none truncate pl-3 pr-9', className)} {...props}>
        {children}
      </select>
      <ChevronDown aria-hidden="true" className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
    </div>
  ),
)
NativeSelect.displayName = 'NativeSelect'

export { NativeSelect }
