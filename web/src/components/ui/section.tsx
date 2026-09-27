import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export interface SectionProps {
  title?: ReactNode
  description?: ReactNode
  /** 标题行右侧的操作。 */
  actions?: ReactNode
  children: ReactNode
  className?: string
  id?: string
}

/** 页面里的一个区块：标题行 + 内容，不自带容器边框。 */
export function Section({ title, description, actions, children, className, id }: SectionProps) {
  const headingId = id ? `${id}-title` : undefined
  return (
    <section className={cn('space-y-2.5', className)} aria-labelledby={title ? headingId : undefined} id={id}>
      {title || actions ? (
        <div className="flex min-h-8 items-end justify-between gap-3 px-1">
          <div className="min-w-0">
            {title ? <h2 id={headingId} className="text-headline font-semibold">{title}</h2> : null}
            {description ? <p className="mt-0.5 text-footnote text-muted-foreground">{description}</p> : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-1">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  )
}
