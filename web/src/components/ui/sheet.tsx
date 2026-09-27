import * as React from 'react'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export interface ResponsiveSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: React.ReactNode
  description?: React.ReactNode
  children?: React.ReactNode
  /** 固定在底部、不随内容滚动的操作区。 */
  footer?: React.ReactNode
  /** 标题行右侧、关闭按钮之前的操作，例如「完成」。 */
  headerActions?: React.ReactNode
  /** 桌面端（≥1024px）呈现为居中对话框或右侧面板。 */
  desktop?: 'dialog' | 'panel'
  /** 手机端按内容高度（最高 90dvh）升起，或铺满全屏。 */
  mobile?: 'auto' | 'full'
  /** 桌面对话框宽度。 */
  size?: 'sm' | 'md' | 'lg'
  hideClose?: boolean
  className?: string
  bodyClassName?: string
  onOpenAutoFocus?: (event: Event) => void
}

const dialogWidth = {
  sm: 'lg:w-[min(24rem,calc(100vw-4rem))]',
  md: 'lg:w-[min(32rem,calc(100vw-4rem))]',
  lg: 'lg:w-[min(44rem,calc(100vw-4rem))]',
} as const

/** 手机上从底部升起、桌面上居中或靠右的面板。所有弹层都用它，交互保持一致。 */
export function ResponsiveSheet({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  headerActions,
  desktop = 'dialog',
  mobile = 'auto',
  size = 'md',
  hideClose = false,
  className,
  bodyClassName,
  onOpenAutoFocus,
}: ResponsiveSheetProps) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-[var(--overlay)] data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out" />
        <DialogPrimitive.Content
          {...(description ? {} : { 'aria-describedby': undefined })}
          onOpenAutoFocus={onOpenAutoFocus}
          className={cn(
            'fixed z-50 flex flex-col bg-surface-raised text-foreground shadow-float outline-none',
            mobile === 'full'
              ? 'inset-0 pt-safe'
              : 'inset-x-0 bottom-0 max-h-[90dvh] rounded-t-2xl border-t',
            'data-[state=open]:animate-sheet-in data-[state=closed]:animate-sheet-out',
            desktop === 'panel'
              ? 'lg:inset-y-0 lg:left-auto lg:right-0 lg:h-dvh lg:max-h-none lg:w-[min(30rem,100vw)] lg:rounded-none lg:border-l lg:border-t-0 lg:pt-0 lg:data-[state=open]:animate-panel-in lg:data-[state=closed]:animate-panel-out'
              : cn(
                  'lg:inset-auto lg:left-1/2 lg:top-1/2 lg:max-h-[min(85dvh,52rem)] lg:-translate-x-1/2 lg:-translate-y-1/2 lg:rounded-2xl lg:border lg:pt-0 lg:data-[state=open]:animate-dialog-in lg:data-[state=closed]:animate-dialog-out',
                  dialogWidth[size],
                ),
            className,
          )}
        >
          {mobile === 'auto' ? <span aria-hidden="true" className="absolute left-1/2 top-2 h-1 w-9 -translate-x-1/2 rounded-full bg-border-strong lg:hidden" /> : null}
          <div className={cn('flex shrink-0 items-start gap-2 px-4 pb-2 lg:px-5 lg:pt-4', mobile === 'auto' ? 'pt-5' : 'pt-3')}>
            <div className="min-w-0 flex-1 pt-1.5">
              <DialogPrimitive.Title className="text-headline font-semibold">{title}</DialogPrimitive.Title>
              {description ? <DialogPrimitive.Description className="mt-0.5 text-footnote text-muted-foreground">{description}</DialogPrimitive.Description> : null}
            </div>
            {headerActions}
            {hideClose ? null : (
              <DialogPrimitive.Close asChild>
                <Button variant="ghost" size="icon-sm" aria-label="关闭" className="-mr-1.5 mt-0.5">
                  <X aria-hidden="true" />
                </Button>
              </DialogPrimitive.Close>
            )}
          </div>
          <div
            className={cn(
              'min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 lg:px-5',
              footer ? 'pb-4' : 'pb-[max(1rem,env(safe-area-inset-bottom))] lg:pb-5',
              bodyClassName,
            )}
          >
            {children}
          </div>
          {footer ? (
            <div className="shrink-0 border-t px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] lg:px-5 lg:pb-4">{footer}</div>
          ) : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
