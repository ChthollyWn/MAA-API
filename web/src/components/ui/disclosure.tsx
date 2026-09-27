import type { ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

export interface DisclosureProps {
  title: ReactNode
  description?: ReactNode
  /** 折叠头右侧的提示，例如「已修改 2 项」。 */
  badge?: ReactNode
  defaultOpen?: boolean
  /** card：带边框的独立块；plain：嵌在其他容器里用。 */
  variant?: 'card' | 'plain'
  children: ReactNode
  className?: string
}

/** 可折叠区块，基于原生 details，无需脚本即可展开收起。 */
export function Disclosure({ title, description, badge, defaultOpen = false, variant = 'card', children, className }: DisclosureProps) {
  return (
    <details
      open={defaultOpen || undefined}
      className={cn('group', variant === 'card' && 'rounded-xl border bg-card shadow-card', className)}
    >
      <summary
        className={cn(
          'flex min-h-12 list-none items-center gap-3 py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring [&::-webkit-details-marker]:hidden',
          variant === 'card' ? 'rounded-xl px-4' : 'rounded-lg px-1',
        )}
      >
        <span className="min-w-0 flex-1">
          <span className="block text-[0.9375rem] font-medium">{title}</span>
          {description ? <span className="mt-0.5 block text-footnote text-muted-foreground">{description}</span> : null}
        </span>
        {badge ? <Badge variant="primary">{badge}</Badge> : null}
        <ChevronDown aria-hidden="true" className="size-4 shrink-0 text-muted-foreground transition-transform duration-200 group-open:rotate-180" />
      </summary>
      <div className={cn(variant === 'card' ? 'border-t px-4 py-4' : 'px-1 pb-2 pt-1')}>{children}</div>
    </details>
  )
}
