import * as React from 'react'
import { controlClassName } from '@/components/ui/input'
import { cn } from '@/lib/utils'

const Textarea = React.forwardRef<HTMLTextAreaElement, React.ComponentProps<'textarea'>>(
  ({ className, ...props }, ref) => (
    <textarea ref={ref} className={cn(controlClassName, 'block min-h-24 resize-y px-3 py-2.5 leading-relaxed', className)} {...props} />
  ),
)
Textarea.displayName = 'Textarea'

export { Textarea }
