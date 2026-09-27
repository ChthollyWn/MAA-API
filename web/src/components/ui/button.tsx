import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { LoaderCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'relative inline-flex select-none items-center justify-center gap-2 whitespace-nowrap rounded-lg font-medium transition-[background-color,color,border-color,box-shadow,opacity] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-45 aria-disabled:pointer-events-none aria-disabled:opacity-45 [&_svg]:pointer-events-none [&_svg]:size-[1.125rem] [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:bg-primary-hover',
        primary: 'bg-primary text-primary-foreground hover:bg-primary-hover',
        secondary: 'bg-surface-2 text-foreground hover:bg-border',
        soft: 'bg-primary-soft text-primary-text hover:bg-primary-soft/70',
        outline: 'border border-border-strong bg-surface text-foreground hover:bg-surface-2',
        ghost: 'text-foreground hover:bg-surface-2',
        destructive: 'bg-danger text-danger-foreground hover:opacity-90',
        'destructive-soft': 'bg-danger-soft text-danger hover:opacity-85',
        link: 'h-auto min-h-0 px-0 text-primary-text underline-offset-4 hover:underline',
      },
      size: {
        default: 'min-h-11 px-4 text-[0.9375rem]',
        sm: 'min-h-10 rounded-md px-3 text-sm',
        lg: 'min-h-12 rounded-xl px-5 text-base',
        icon: 'size-11 shrink-0 p-0',
        'icon-sm': 'size-9 shrink-0 rounded-md p-0',
      },
    },
    compoundVariants: [{ variant: 'link', className: 'min-h-0 px-0' }],
    defaultVariants: { variant: 'default', size: 'default' },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
  /** 显示加载圈并禁用按钮；按钮文字保持不变，由调用方决定是否改成「正在…」。 */
  loading?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, loading = false, disabled, children, ...props }, ref) => {
    if (asChild) {
      return <Slot ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props}>{children}</Slot>
    }
    return (
      <button
        ref={ref}
        className={cn(buttonVariants({ variant, size }), className)}
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        {...props}
      >
        {loading ? <LoaderCircle aria-hidden="true" className="animate-spin" /> : null}
        {children}
      </button>
    )
  },
)
Button.displayName = 'Button'

export { Button, buttonVariants }
