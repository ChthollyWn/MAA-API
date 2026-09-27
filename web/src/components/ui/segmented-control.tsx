import type { ReactNode } from 'react'
import * as ToggleGroup from '@radix-ui/react-toggle-group'
import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface SegmentedOption<T extends string> {
  value: T
  label: ReactNode
  icon?: LucideIcon
  disabled?: boolean
  /** 只显示图标时必须提供。 */
  ariaLabel?: string
}

export interface SegmentedControlProps<T extends string> {
  value: T
  onValueChange: (value: T) => void
  options: readonly SegmentedOption<T>[]
  ariaLabel: string
  size?: 'sm' | 'md'
  /** 选项平均分满整行（手机上的默认用法）。 */
  fullWidth?: boolean
  disabled?: boolean
  className?: string
}

/** 单选分段控件：始终有且只有一个选中项。 */
export function SegmentedControl<T extends string>({
  value,
  onValueChange,
  options,
  ariaLabel,
  size = 'md',
  fullWidth = false,
  disabled = false,
  className,
}: SegmentedControlProps<T>) {
  return (
    <ToggleGroup.Root
      type="single"
      value={value}
      onValueChange={(next) => {
        if (next) onValueChange(next as T)
      }}
      aria-label={ariaLabel}
      disabled={disabled}
      className={cn('inline-flex items-center gap-1 rounded-lg bg-surface-2 p-1', fullWidth && 'flex w-full', className)}
    >
      {options.map(({ value: optionValue, label, icon: Icon, disabled: optionDisabled, ariaLabel: optionLabel }) => (
        <ToggleGroup.Item
          key={optionValue}
          value={optionValue}
          disabled={optionDisabled}
          aria-label={optionLabel}
          className={cn(
            'inline-flex min-w-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium text-muted-foreground transition-[background-color,color,box-shadow] duration-150 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-45 data-[state=on]:bg-surface-active data-[state=on]:text-foreground data-[state=on]:shadow-[0_1px_2px_rgb(16_18_24/0.08)]',
            size === 'md' ? 'h-9 px-3 text-sm' : 'h-8 px-2.5 text-[0.8125rem]',
            fullWidth && 'flex-1',
            '[&_svg]:size-4 [&_svg]:shrink-0',
          )}
        >
          {Icon ? <Icon aria-hidden="true" /> : null}
          {label}
        </ToggleGroup.Item>
      ))}
    </ToggleGroup.Root>
  )
}
