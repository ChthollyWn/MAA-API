import * as React from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface ChipProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  selected?: boolean
}

/** 可切换的芯片（多选筛选、星期选择）。用 aria-pressed 表达选中状态。 */
const Chip = React.forwardRef<HTMLButtonElement, ChipProps>(({ className, selected = false, type = 'button', ...props }, ref) => (
  <button
    ref={ref}
    type={type}
    aria-pressed={selected}
    className={cn(
      'inline-flex h-9 shrink-0 select-none items-center justify-center gap-1.5 rounded-full border px-3.5 text-sm font-medium transition-[background-color,color,border-color] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-45 [&_svg]:size-4',
      selected
        ? 'border-primary/60 bg-primary-soft text-primary-text'
        : 'border-border bg-surface text-foreground hover:bg-surface-2',
      className,
    )}
    {...props}
  />
))
Chip.displayName = 'Chip'

export interface RemovableChipProps {
  children: React.ReactNode
  onRemove: () => void
  removeLabel: string
  className?: string
}

/** 表示一个生效条件的芯片，右侧按钮移除该条件。 */
function RemovableChip({ children, onRemove, removeLabel, className }: RemovableChipProps) {
  return (
    <span className={cn('inline-flex h-8 max-w-full shrink-0 items-center gap-1 rounded-full bg-primary-soft pl-3 pr-1 text-[0.8125rem] font-medium text-primary-text', className)}>
      <span className="truncate">{children}</span>
      <button
        type="button"
        aria-label={removeLabel}
        onClick={onRemove}
        className="relative grid size-6 shrink-0 place-items-center rounded-full hover:bg-primary/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring before:absolute before:-inset-2 before:content-['']"
      >
        <X aria-hidden="true" className="size-3.5" />
      </button>
    </span>
  )
}

export { Chip, RemovableChip }
