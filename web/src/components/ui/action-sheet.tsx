import * as React from 'react'
import { MoreHorizontal, type LucideIcon } from 'lucide-react'
import { Button, type ButtonProps } from '@/components/ui/button'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'

export interface ActionSheetAction {
  label: string
  description?: string
  icon?: LucideIcon
  tone?: 'default' | 'danger'
  disabled?: boolean
  onSelect: () => void
}

export interface ActionSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: React.ReactNode
  description?: React.ReactNode
  actions: readonly ActionSheetAction[]
}

/** 一组动作按钮组成的面板；选中后先关闭再执行。 */
export function ActionSheet({ open, onOpenChange, title, description, actions }: ActionSheetProps) {
  const baseId = React.useId()
  return (
    <ResponsiveSheet open={open} onOpenChange={onOpenChange} title={title} description={description} size="sm">
      <div className="-mx-2 flex flex-col gap-0.5">
        {actions.map((action, index) => {
          const Icon = action.icon
          const descriptionId = action.description ? `${baseId}-${index}` : undefined
          return (
            <button
              key={`${action.label}-${index}`}
              type="button"
              aria-label={action.label}
              aria-describedby={descriptionId}
              disabled={action.disabled}
              onClick={() => {
                onOpenChange(false)
                action.onSelect()
              }}
              className={cn(
                'flex min-h-12 w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-[0.9375rem] font-medium transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-45',
                action.tone === 'danger' && 'text-danger',
              )}
            >
              {Icon ? <Icon aria-hidden="true" className={cn('size-5 shrink-0', action.tone === 'danger' ? 'text-danger' : 'text-muted-foreground')} /> : null}
              <span className="min-w-0 flex-1">
                <span className="block">{action.label}</span>
                {action.description ? <span id={descriptionId} className="block text-footnote font-normal text-muted-foreground">{action.description}</span> : null}
              </span>
            </button>
          )
        })}
      </div>
    </ResponsiveSheet>
  )
}

export interface ActionMenuProps {
  /** 触发按钮的无障碍名称，例如「任务 2 的更多操作」。 */
  label?: string
  title?: React.ReactNode
  description?: React.ReactNode
  actions: readonly ActionSheetAction[]
  icon?: LucideIcon
  variant?: ButtonProps['variant']
  size?: ButtonProps['size']
  className?: string
  children?: React.ReactNode
  disabled?: boolean
}

/** 「更多操作」按钮 + 动作面板。 */
export function ActionMenu({
  label = '更多操作',
  title,
  description,
  actions,
  icon: Icon = MoreHorizontal,
  variant = 'ghost',
  size = 'icon',
  className,
  children,
  disabled,
}: ActionMenuProps) {
  const [open, setOpen] = React.useState(false)
  return (
    <>
      <Button type="button" variant={variant} size={size} aria-label={label} aria-haspopup="dialog" className={className} disabled={disabled} onClick={() => setOpen(true)}>
        {children ?? <Icon aria-hidden="true" />}
      </Button>
      <ActionSheet open={open} onOpenChange={setOpen} title={title ?? label} description={description} actions={actions} />
    </>
  )
}
