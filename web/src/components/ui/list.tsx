import * as React from 'react'
import { Link } from 'react-router'
import { ChevronRight, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface ListGroupProps {
  title?: React.ReactNode
  /** 组下方的说明小字。 */
  footer?: React.ReactNode
  children: React.ReactNode
  className?: string
  /** 标题用的标签；默认 h2，嵌套在分区里时可以改成 h3。 */
  headingLevel?: 'h2' | 'h3'
}

/** iOS 式分组列表：一组圆角容器，行之间用分隔线。 */
function ListGroup({ title, footer, children, className, headingLevel = 'h2' }: ListGroupProps) {
  const Heading = headingLevel
  return (
    <section className={cn('space-y-1.5', className)}>
      {title ? <Heading className="px-1 text-footnote font-medium text-muted-foreground">{title}</Heading> : null}
      <div className="divide-y divide-border overflow-hidden rounded-xl border bg-card shadow-card">{children}</div>
      {footer ? <p className="px-1 text-footnote text-muted-foreground">{footer}</p> : null}
    </section>
  )
}

export type ListItemTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger'

const iconTone: Record<ListItemTone, string> = {
  neutral: 'bg-surface-2 text-foreground',
  primary: 'bg-primary-soft text-primary-text',
  success: 'bg-success-soft text-success',
  warning: 'bg-warning-soft text-warning',
  danger: 'bg-danger-soft text-danger',
}

export interface ListItemProps {
  title: React.ReactNode
  description?: React.ReactNode
  icon?: LucideIcon
  iconTone?: ListItemTone
  /** 行右侧的值、徽章或控件。 */
  trailing?: React.ReactNode
  /** 默认在可点击的行上显示。 */
  chevron?: boolean
  to?: string
  onClick?: () => void
  disabled?: boolean
  className?: string
  'aria-label'?: string
  'aria-current'?: React.AriaAttributes['aria-current']
  tone?: 'default' | 'danger'
}

function ListItem({
  title,
  description,
  icon: Icon,
  iconTone: tone = 'neutral',
  trailing,
  chevron,
  to,
  onClick,
  disabled = false,
  className,
  tone: textTone = 'default',
  ...aria
}: ListItemProps) {
  const interactive = Boolean(to || onClick)
  const showChevron = chevron ?? interactive
  const body = (
    <>
      {Icon ? (
        <span className={cn('grid size-9 shrink-0 place-items-center rounded-lg', iconTone[tone])}>
          <Icon aria-hidden="true" className="size-[1.125rem]" />
        </span>
      ) : null}
      <span className="min-w-0 flex-1">
        <span className={cn('block text-[0.9375rem] font-medium leading-5', textTone === 'danger' && 'text-danger')}>{title}</span>
        {description ? <span className="mt-0.5 block text-footnote text-muted-foreground">{description}</span> : null}
      </span>
      {trailing ? <span className="flex shrink-0 items-center gap-2 text-sm text-muted-foreground">{trailing}</span> : null}
      {showChevron ? <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground/70" /> : null}
    </>
  )
  const rowClassName = cn(
    'flex min-h-14 w-full items-center gap-3 px-4 py-3 text-left',
    interactive && 'transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
    disabled && 'pointer-events-none opacity-50',
    className,
  )
  if (to) {
    return <Link to={to} className={rowClassName} aria-disabled={disabled || undefined} {...aria}>{body}</Link>
  }
  if (onClick) {
    return <button type="button" onClick={onClick} disabled={disabled} className={rowClassName} {...aria}>{body}</button>
  }
  return <div className={rowClassName} {...aria}>{body}</div>
}

export { ListGroup, ListItem }
