import type { ReactNode } from 'react'
import { InfoHint } from '@/components/ui/info-hint'
import { cn } from '@/lib/utils'

export interface SectionProps {
  title?: ReactNode
  /** 标题下的短信息：计数、时间、当前状态这类一眼要看到的。 */
  description?: ReactNode
  /** 解释性文字（为什么这样、有什么条件），收进标题旁的小问号。 */
  hint?: ReactNode
  /** 标题行右侧的操作。 */
  actions?: ReactNode
  children: ReactNode
  className?: string
  id?: string
}

/** 页面里的一个区块：标题行 + 内容，不自带容器边框。 */
export function Section({ title, description, hint, actions, children, className, id }: SectionProps) {
  const headingId = id ? `${id}-title` : undefined
  return (
    <section className={cn('space-y-2.5', className)} aria-labelledby={title ? headingId : undefined} id={id}>
      {title || actions ? (
        <div className="flex min-h-8 items-end justify-between gap-3 px-1">
          <div className="min-w-0">
            {title ? (
              <h2 id={headingId} className="flex items-center gap-1.5 text-headline font-semibold">
                {title}
                {hint ? <InfoHint label={typeof title === 'string' ? `${title} 说明` : '说明'}>{hint}</InfoHint> : null}
              </h2>
            ) : null}
            {description ? <p className="mt-0.5 text-footnote text-muted-foreground">{description}</p> : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-1">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  )
}
