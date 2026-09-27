import * as React from 'react'
import * as AlertDialog from '@radix-ui/react-alert-dialog'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export interface ConfirmOptions {
  title: React.ReactNode
  description?: React.ReactNode
  /** 写明后果，例如「清空 3 条待执行」。 */
  confirmText?: string
  cancelText?: string
  tone?: 'default' | 'danger'
}

export interface ConfirmDialogProps extends ConfirmOptions {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
}

/** 应用内确认框：手机上从底部升起，桌面居中。默认焦点在「取消」上。 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmText = '确认',
  cancelText = '取消',
  tone = 'default',
  onConfirm,
}: ConfirmDialogProps) {
  return (
    <AlertDialog.Root open={open} onOpenChange={onOpenChange}>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="fixed inset-0 z-[60] bg-[var(--overlay)] data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out" />
        <AlertDialog.Content
          {...(description ? {} : { 'aria-describedby': undefined })}
          className={cn(
            'fixed inset-x-0 bottom-0 z-[60] rounded-t-2xl border-t bg-surface-raised px-5 pt-6 pb-[max(1.25rem,env(safe-area-inset-bottom))] text-foreground shadow-float outline-none',
            'data-[state=open]:animate-sheet-in data-[state=closed]:animate-sheet-out',
            'lg:inset-auto lg:left-1/2 lg:top-1/2 lg:w-[min(26rem,calc(100vw-4rem))] lg:-translate-x-1/2 lg:-translate-y-1/2 lg:rounded-2xl lg:border lg:pb-5 lg:data-[state=open]:animate-dialog-in lg:data-[state=closed]:animate-dialog-out',
          )}
        >
          <AlertDialog.Title className="text-headline font-semibold">{title}</AlertDialog.Title>
          {description ? <AlertDialog.Description className="mt-1.5 text-sm text-muted-foreground">{description}</AlertDialog.Description> : null}
          <div className="mt-6 flex flex-col-reverse gap-2 lg:flex-row lg:justify-end">
            <AlertDialog.Cancel asChild>
              <Button variant="outline" className="w-full lg:w-auto">{cancelText}</Button>
            </AlertDialog.Cancel>
            <AlertDialog.Action asChild>
              <Button variant={tone === 'danger' ? 'destructive' : 'default'} className="w-full lg:w-auto" onClick={onConfirm}>
                {confirmText}
              </Button>
            </AlertDialog.Action>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  )
}

/**
 * 以 Promise 形式使用确认框：`if (await confirm({...})) doIt()`。
 * 返回的元素需要渲染在组件里。
 */
export function useConfirm(): [(options: ConfirmOptions) => Promise<boolean>, React.ReactNode] {
  const [open, setOpen] = React.useState(false)
  const [options, setOptions] = React.useState<ConfirmOptions>({ title: '' })
  const resolverRef = React.useRef<((value: boolean) => void) | null>(null)

  const settle = React.useCallback((value: boolean) => {
    const resolve = resolverRef.current
    resolverRef.current = null
    setOpen(false)
    resolve?.(value)
  }, [])

  const confirm = React.useCallback((next: ConfirmOptions) => {
    resolverRef.current?.(false)
    return new Promise<boolean>((resolve) => {
      resolverRef.current = resolve
      setOptions(next)
      setOpen(true)
    })
  }, [])

  React.useEffect(() => () => resolverRef.current?.(false), [])

  const element = (
    <ConfirmDialog
      {...options}
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) settle(false)
      }}
      onConfirm={() => settle(true)}
    />
  )
  return [confirm, element]
}
