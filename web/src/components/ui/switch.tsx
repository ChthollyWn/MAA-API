import * as React from 'react'
import * as SwitchPrimitive from '@radix-ui/react-switch'
import { cn } from '@/lib/utils'

const Switch = React.forwardRef<
  React.ElementRef<typeof SwitchPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof SwitchPrimitive.Root>
>(({ className, ...props }, ref) => (
  <SwitchPrimitive.Root
    ref={ref}
    className={cn(
      'peer relative inline-flex h-[1.625rem] w-11 shrink-0 items-center rounded-full bg-border-strong p-0.5 transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-45 data-[state=checked]:bg-primary',
      // 扩大触控区域到 44px，而不放大视觉尺寸。
      'before:absolute before:-inset-2.5 before:content-[""]',
      className,
    )}
    {...props}
  >
    <SwitchPrimitive.Thumb className="pointer-events-none block size-[1.375rem] rounded-full bg-white shadow-[0_1px_3px_rgb(0_0_0/0.25)] transition-transform duration-200 data-[state=checked]:translate-x-[1.125rem]" />
  </SwitchPrimitive.Root>
))
Switch.displayName = SwitchPrimitive.Root.displayName

export interface SwitchRowProps {
  label: React.ReactNode
  description?: React.ReactNode
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  disabled?: boolean
  id?: string
  className?: string
  aside?: React.ReactNode
}

/** 整行可点的开关：文字在左、开关在右，点击文字也会切换。 */
function SwitchRow({ label, description, checked, onCheckedChange, disabled, id, className, aside }: SwitchRowProps) {
  const generatedId = React.useId()
  const controlId = id ?? generatedId
  const descriptionId = description ? `${controlId}-description` : undefined
  return (
    <div className={cn('flex min-h-12 items-center gap-3', className)}>
      <div className="min-w-0 flex-1">
        <label htmlFor={controlId} className={cn('block text-[0.9375rem] font-medium leading-5', disabled ? 'opacity-60' : 'cursor-pointer')}>
          {label}
        </label>
        {description ? <p id={descriptionId} className="mt-0.5 text-footnote text-muted-foreground">{description}</p> : null}
      </div>
      {aside}
      <Switch id={controlId} checked={checked} onCheckedChange={onCheckedChange} disabled={disabled} aria-describedby={descriptionId} />
    </div>
  )
}

export { Switch, SwitchRow }
